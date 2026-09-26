"""Shared-secret header auth for HTTP routers.

Secrets are env-only (never a settings_registry row). An unset secret means
open, so a fresh clone works with zero config; set it on anything reachable.
"""

import secrets

from fastapi import Header, HTTPException, Request

import config


def is_set(config_attr: str) -> bool:
    return bool(getattr(config, config_attr, ""))


_LOOPBACK = ("127.0.0.1", "::1")
# A request carrying any of these came through a proxy or tunnel, so its loopback peer
# address says nothing about who sent it (a same-host cloudflared/nginx would otherwise
# make every visitor look local). Such requests never get loopback trust.
_FORWARDED_HEADERS = ("forwarded", "x-forwarded-for", "x-forwarded-host", "x-real-ip",
                      "cf-connecting-ip", "true-client-ip", "via")


def _is_direct_loopback(request: Request) -> bool:
    if any(h in request.headers for h in _FORWARDED_HEADERS):
        return False
    return bool(request.client) and request.client.host in _LOOPBACK


def require_key(config_attr: str, header: str, trust_loopback: bool = False):
    """FastAPI dependency: `header` must equal config.<config_attr> when that is set.

    config is read per request, so tests and late env loading both work.
    trust_loopback lets same-host services (sibling apps calling localhost) in without
    the key — for routes other local services already depend on. Never for /admin.
    If this port is ever published through a proxy that strips forwarding headers,
    drop trust_loopback: the peer address is the only signal it has.
    """
    async def _check(request: Request, provided: str | None = Header(default=None, alias=header)) -> None:
        expected = getattr(config, config_attr, "") or ""
        if trust_loopback and _is_direct_loopback(request):
            return
        if expected and not secrets.compare_digest((provided or "").encode(), expected.encode()):
            raise HTTPException(status_code=403, detail=f"bad or missing {header}")

    return _check
