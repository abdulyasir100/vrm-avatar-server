"""Playwright automation — login, clock-in, clock-out on Sharing Vision timesheet."""

import logging
import os
import random
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright, Browser, BrowserContext, Page

logger = logging.getLogger(__name__)

# Config from env vars
TIMESHEET_URL = os.environ.get("TIMESHEET_URL", "https://new-timesheet.sharingvisionjakarta.com")
TIMESHEET_EMAIL = os.environ.get("TIMESHEET_EMAIL", "")
TIMESHEET_PASSWORD = os.environ.get("TIMESHEET_PASSWORD", "")
OFFICE_LATITUDE = float(os.environ.get("OFFICE_LATITUDE", "-6.247139"))
OFFICE_LONGITUDE = float(os.environ.get("OFFICE_LONGITUDE", "106.831458"))
WORK_MODE = os.environ.get("WORK_MODE", "WFO")
WORKPLACE = os.environ.get("WORKPLACE", "Menara BRILiaN")
MAX_RETRIES = int(os.environ.get("CLOCKIN_MAX_RETRIES", "3"))
RETRY_DELAY = int(os.environ.get("CLOCKIN_RETRY_DELAY", "30"))

DATA_DIR = Path("data/plugins/clockin")
COOKIES_PATH = DATA_DIR / "cookies.json"
SCREENSHOTS_DIR = DATA_DIR / "screenshots"


async def _save_screenshot(page: Page, prefix: str) -> str:
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = SCREENSHOTS_DIR / f"{prefix}_{ts}.png"
    await page.screenshot(path=str(path), full_page=True)
    logger.info(f"[clocker] Screenshot saved: {path}")
    return str(path)


def _jittered_coords() -> tuple[float, float]:
    lat = OFFICE_LATITUDE + random.uniform(-0.000010, 0.000010)
    lng = OFFICE_LONGITUDE + random.uniform(-0.000010, 0.000010)
    return lat, lng


async def _create_context(browser: Browser) -> BrowserContext:
    storage = str(COOKIES_PATH) if COOKIES_PATH.exists() else None
    lat, lng = _jittered_coords()
    logger.info(f"[clocker] GPS: {lat:.6f}, {lng:.6f}")
    ctx = await browser.new_context(
        geolocation={"latitude": lat, "longitude": lng},
        permissions=["geolocation"],
        timezone_id="Asia/Jakarta",
        storage_state=storage,
    )
    return ctx


async def _login(page: Page) -> bool:
    sign_in_btn = page.locator("button:has-text('SIGN IN'), button:has-text('Sign In')")
    if await sign_in_btn.count() == 0:
        logger.info("[clocker] Already logged in")
        return True

    logger.info("[clocker] Login page detected, filling credentials")
    await page.fill("input[type='email'], input[placeholder*='email' i]", TIMESHEET_EMAIL)
    await page.fill("input[type='password']", TIMESHEET_PASSWORD)
    await sign_in_btn.first.click()
    await page.wait_for_load_state("networkidle", timeout=15000)
    return True


async def _dismiss_modal(page: Page) -> None:
    """Dismiss the KEBIJAKAN MUTU MUI modal that appears after login."""
    await page.wait_for_timeout(3000)

    modal = page.locator(".MuiModal-root")
    if await modal.count() == 0:
        logger.info("[clocker] No modal found")
        return

    logger.info("[clocker] Modal detected, removing via JS")
    # MUI modals are tricky (viewport, backdrop, etc.) — just nuke them from the DOM
    await page.evaluate("""
        document.querySelectorAll('.MuiModal-root').forEach(el => el.remove());
        document.querySelectorAll('.MuiBackdrop-root').forEach(el => el.remove());
    """)
    await page.wait_for_timeout(1000)
    logger.info("[clocker] Modal removed")


async def _navigate_to_presences(page: Page) -> None:
    presences_tab = page.locator("text=Presences").first
    await presences_tab.wait_for(state="visible", timeout=10000)
    await presences_tab.click()
    await page.wait_for_load_state("networkidle", timeout=15000)
    logger.info("[clocker] Navigated to Presences tab")


async def _wait_for_map(page: Page) -> None:
    await page.locator(".leaflet-container").first.wait_for(state="visible", timeout=20000)
    await page.wait_for_timeout(2000)
    logger.info("[clocker] Work location map loaded")


async def _get_detected_location(page: Page) -> dict | None:
    """Extract the GPS coords that the page detected via browser geolocation."""
    try:
        coords = await page.evaluate("""
            () => new Promise((resolve) => {
                navigator.geolocation.getCurrentPosition(
                    (pos) => resolve({lat: pos.coords.latitude, lng: pos.coords.longitude}),
                    () => resolve(null),
                    {timeout: 5000}
                );
            })
        """)
        return coords
    except Exception:
        return None


async def _submit_presence(page: Page, action: str) -> dict:
    card = page.locator(f"text='{action}'").first
    await card.wait_for(state="visible", timeout=10000)
    await card.click()
    logger.info(f"[clocker] Clicked {action} card")
    await page.wait_for_timeout(1500)

    if action == "Clock In":
        try:
            mode_select = page.locator("text='Select Work Mode'").locator("..").locator("select")
            if await mode_select.count() > 0:
                await mode_select.select_option(label=WORK_MODE)
            else:
                selects = page.locator("select")
                count = await selects.count()
                if count >= 1:
                    await selects.nth(0).select_option(label=WORK_MODE)
        except Exception:
            await page.get_by_text("Select Work Mode").click()
            await page.wait_for_timeout(500)
            await page.get_by_text(WORK_MODE, exact=True).click()

        await page.wait_for_timeout(500)

        try:
            workplace_select = page.locator("text='Select Another Workplace'").locator("..").locator("select")
            if await workplace_select.count() > 0:
                await workplace_select.select_option(label=WORKPLACE)
            else:
                selects = page.locator("select")
                count = await selects.count()
                if count >= 2:
                    await selects.nth(1).select_option(label=WORKPLACE)
        except Exception:
            await page.get_by_text("Select Another Workplace").click()
            await page.wait_for_timeout(500)
            await page.get_by_text(WORKPLACE, exact=True).click()

    await page.wait_for_timeout(500)

    submit_btn = page.locator("button:has-text('SUBMIT'), button:has-text('Submit')")
    await submit_btn.first.wait_for(state="visible", timeout=5000)
    await submit_btn.first.click()
    logger.info(f"[clocker] Clicked SUBMIT for {action}")

    await page.wait_for_load_state("networkidle", timeout=15000)
    await page.wait_for_timeout(2000)

    return {"success": True, "message": f"{action} submitted successfully"}


async def _run_action(action: str) -> dict:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await _create_context(browser)
        page = await context.new_page()

        try:
            await page.goto(TIMESHEET_URL, wait_until="networkidle", timeout=30000)
            await _login(page)
            await _dismiss_modal(page)
            await _navigate_to_presences(page)
            await _wait_for_map(page)
            result = await _submit_presence(page, action)

            DATA_DIR.mkdir(parents=True, exist_ok=True)
            await context.storage_state(path=str(COOKIES_PATH))
            return result

        except Exception as e:
            screenshot = await _save_screenshot(page, f"fail_{action.lower().replace(' ', '_')}")
            error_msg = f"{action} failed: {e}"
            logger.error(f"[clocker] {error_msg} — screenshot: {screenshot}")
            return {"success": False, "message": error_msg}

        finally:
            await browser.close()


async def clock_in() -> dict:
    return await _with_retries("Clock In")


async def clock_out() -> dict:
    return await _with_retries("Clock Out")


async def test_login() -> dict:
    """Login, navigate to Presences, check GPS — but don't clock in."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await _create_context(browser)
        page = await context.new_page()

        try:
            await page.goto(TIMESHEET_URL, wait_until="networkidle", timeout=30000)
            await _login(page)
            await _dismiss_modal(page)
            await _navigate_to_presences(page)
            await _wait_for_map(page)

            coords = await _get_detected_location(page)
            screenshot = await _save_screenshot(page, "test")

            DATA_DIR.mkdir(parents=True, exist_ok=True)
            await context.storage_state(path=str(COOKIES_PATH))

            return {
                "success": True,
                "message": "Login + Presences OK",
                "coords": coords,
                "screenshot": screenshot,
            }

        except Exception as e:
            screenshot = await _save_screenshot(page, "test_fail")
            return {"success": False, "message": f"Test failed: {e}", "screenshot": screenshot}

        finally:
            await browser.close()


import asyncio

async def _with_retries(action: str) -> dict:
    result = {}
    for attempt in range(1, MAX_RETRIES + 1):
        logger.info(f"[clocker] {action} attempt {attempt}/{MAX_RETRIES}")
        result = await _run_action(action)

        if result["success"]:
            return result

        if attempt == 1 and COOKIES_PATH.exists():
            logger.info("[clocker] Clearing stale cookies for retry")
            COOKIES_PATH.unlink()

        if attempt < MAX_RETRIES:
            logger.info(f"[clocker] Retrying in {RETRY_DELAY}s...")
            await asyncio.sleep(RETRY_DELAY)

    return result
