"""cli-anything-cloudstream: CLI harness for CloudStream streaming application.

Provides both one-shot commands and an interactive REPL for searching,
browsing, and downloading content from CloudStream providers.

Usage:
    cli-anything-cloudstream                    # Enter REPL
    cli-anything-cloudstream search "inception"  # One-shot search
    cli-anything-cloudstream --json search "inception"  # JSON output
"""

import json
import sys

import click

from cli_anything.cloudstream import __version__
from cli_anything.cloudstream.core.session import Session


# ── Global state ──────────────────────────────────────────────────

pass_session = click.make_pass_decorator(Session, ensure=True)

# Shared JSON output flag
_json_output = False


def _output(data: dict, human_fn=None):
    """Output data as JSON or human-readable format."""
    if _json_output:
        click.echo(json.dumps(data, indent=2, ensure_ascii=False))
    elif human_fn:
        human_fn(data)
    else:
        click.echo(json.dumps(data, indent=2, ensure_ascii=False))


# ── Main CLI group ────────────────────────────────────────────────


@click.group(invoke_without_command=True)
@click.option("--json", "json_mode", is_flag=True, help="Output in JSON format")
@click.option("--session-path", type=click.Path(), default=None,
              help="Custom session file path")
@click.version_option(version=__version__, prog_name="cli-anything-cloudstream")
@click.pass_context
def cli(ctx, json_mode, session_path):
    """CLI harness for CloudStream — search, browse, and download streaming content."""
    global _json_output
    _json_output = json_mode

    ctx.ensure_object(dict)
    ctx.obj["session"] = Session(session_path)

    if ctx.invoked_subcommand is None:
        ctx.invoke(repl)


# ── Search command ────────────────────────────────────────────────


@cli.command()
@click.argument("query")
@click.option("-p", "--provider", multiple=True, help="Filter by provider name")
@click.option("--page", default=1, help="Page number for pagination")
@click.pass_context
def search(ctx, query, provider, page):
    """Search for movies, TV shows, and anime."""
    session = ctx.obj["session"]

    try:
        from cli_anything.cloudstream.core.search import search as do_search, format_results_table
        results = do_search(query, list(provider) if provider else None, page)

        # Store in session
        session.set_search_results(query, [r.to_dict() for r in results.results])

        def human_output(data):
            from cli_anything.cloudstream.utils.repl_skin import ReplSkin
            skin = ReplSkin("cloudstream", __version__)
            if not results.results:
                skin.warning(f"No results for '{query}'")
                return
            skin.info(f"Found {len(results.results)} results for '{query}'")
            headers, rows = format_results_table(results)
            skin.table(headers, rows)
            if results.has_next:
                skin.hint(f"More results available. Use --page {page + 1}")

        _output(results.to_dict(), human_output)

    except RuntimeError as e:
        _output({"error": str(e)})
        if not _json_output:
            click.echo(f"Error: {e}", err=True)


# ── Load command ──────────────────────────────────────────────────


@cli.command()
@click.argument("url_or_index")
@click.pass_context
def load(ctx, url_or_index):
    """Load content details from URL or search result index.

    URL_OR_INDEX can be a full URL or a number referencing the last search results.
    """
    session = ctx.obj["session"]

    # Resolve index to URL
    url = url_or_index
    if url_or_index.isdigit():
        result = session.get_search_result(int(url_or_index))
        if result:
            url = result.get("url", url_or_index)
        else:
            _output({"error": f"No search result at index {url_or_index}"})
            return

    try:
        from cli_anything.cloudstream.core.content import (
            load_content, format_content_info, format_episodes_table,
        )
        content = load_content(url)
        session.set_load_response(url, content.to_dict())

        def human_output(data):
            from cli_anything.cloudstream.utils.repl_skin import ReplSkin
            skin = ReplSkin("cloudstream", __version__)
            skin.info(f"Loaded: {content.name}")
            info = format_content_info(content)
            skin.status_block(info)
            if content.plot:
                skin.section("Synopsis")
                click.echo(f"  {content.plot[:300]}")
            if content.total_episodes > 0:
                headers, rows = format_episodes_table(content)
                skin.section(f"Episodes ({content.total_episodes})")
                skin.table(headers, rows[:20])
                if content.total_episodes > 20:
                    skin.hint(f"  ... and {content.total_episodes - 20} more")

        _output(content.to_dict(), human_output)

    except RuntimeError as e:
        _output({"error": str(e)})
        if not _json_output:
            click.echo(f"Error: {e}", err=True)


# ── Links command ─────────────────────────────────────────────────


@cli.command()
@click.argument("data_or_index")
@click.option("--provider", default=None, help="Provider name for context")
@click.pass_context
def links(ctx, data_or_index, provider):
    """Extract streaming links from episode data or episode index.

    DATA_OR_INDEX can be an episode data string or a number referencing
    an episode from the last loaded content.
    """
    session = ctx.obj["session"]

    data = data_or_index
    if data_or_index.isdigit():
        # Try to get episode from last load response
        load_resp = session.state.last_load_response
        if load_resp:
            from cli_anything.cloudstream.core.content import ContentDetails
            content = ContentDetails.from_dict(load_resp)
            ep = content.get_episode(index=int(data_or_index) - 1)
            if ep:
                data = ep.data
            else:
                _output({"error": f"No episode at index {data_or_index}"})
                return

    try:
        from cli_anything.cloudstream.core.stream import (
            extract_links, format_links_table, format_subtitles_table,
        )
        result = extract_links(data, provider)
        session.set_links([l.to_dict() for l in result.links])

        def human_output(d):
            from cli_anything.cloudstream.utils.repl_skin import ReplSkin
            skin = ReplSkin("cloudstream", __version__)
            if not result.links:
                skin.warning("No streaming links found")
                return
            skin.info(f"Found {len(result.links)} links, {len(result.subtitles)} subtitles")
            headers, rows = format_links_table(result)
            skin.table(headers, rows)
            if result.subtitles:
                skin.section("Subtitles")
                sh, sr = format_subtitles_table(result)
                skin.table(sh, sr)

        _output(result.to_dict(), human_output)

    except RuntimeError as e:
        _output({"error": str(e)})
        if not _json_output:
            click.echo(f"Error: {e}", err=True)


# ── Download command ──────────────────────────────────────────────


@cli.command()
@click.argument("link_index_or_url")
@click.option("-o", "--output", "output_path", default=None,
              help="Output file path")
@click.option("-q", "--quality", default="1080", help="Preferred quality (e.g., 720, 1080, best)")
@click.option("--overwrite", is_flag=True, help="Overwrite existing files")
@click.pass_context
def download(ctx, link_index_or_url, output_path, quality, overwrite):
    """Download a stream by link index or direct URL.

    LINK_INDEX_OR_URL can be a number from the last links result,
    or a direct stream URL.
    """
    session = ctx.obj["session"]

    url = link_index_or_url
    referer = None
    headers = {}

    if link_index_or_url.isdigit():
        link_data = session.get_link(int(link_index_or_url))
        if link_data:
            url = link_data.get("url", link_index_or_url)
            referer = link_data.get("referer")
            headers = link_data.get("headers", {})
        else:
            _output({"error": f"No link at index {link_index_or_url}"})
            return

    if not output_path:
        output_path = "download.mp4"

    try:
        from cli_anything.cloudstream.utils.cloudstream_backend import ytdlp_download

        result = ytdlp_download(
            url=url,
            output=output_path,
            referer=referer,
            headers=headers if headers else None,
            quality=quality if quality != "best" else None,
        )

        def human_output(d):
            from cli_anything.cloudstream.utils.repl_skin import ReplSkin
            skin = ReplSkin("cloudstream", __version__)
            skin.success(f"Downloaded: {result.get('output', output_path)}")
            skin.status("Size", f"{result.get('file_size', 0):,} bytes")

        _output(result, human_output)

    except RuntimeError as e:
        _output({"error": str(e)})
        if not _json_output:
            click.echo(f"Error: {e}", err=True)


# ── Play command ──────────────────────────────────────────────────


@cli.command()
@click.argument("link_index_or_url")
@click.pass_context
def play(ctx, link_index_or_url):
    """Open a stream in the browser by link index or direct URL.

    LINK_INDEX_OR_URL can be a number from the last links result,
    or a direct embed URL.
    """
    import webbrowser

    session = ctx.obj["session"]

    url = link_index_or_url
    if link_index_or_url.isdigit():
        link_data = session.get_link(int(link_index_or_url))
        if link_data:
            url = link_data.get("url", link_index_or_url)
        else:
            _output({"error": f"No link at index {link_index_or_url}"})
            return

    webbrowser.open(url)

    result = {"status": "ok", "url": url, "action": "opened in browser"}

    if not _json_output:
        click.echo(f"  Opened in browser: {url[:80]}")
    else:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))


# ── Provider commands ─────────────────────────────────────────────


@cli.group()
def provider():
    """Manage content providers."""
    pass


@provider.command("list")
@click.option("--lang", default=None, help="Filter by language code")
@click.option("--type", "tv_type", default=None, help="Filter by content type")
@click.pass_context
def provider_list(ctx, lang, tv_type):
    """List available content providers."""
    from cli_anything.cloudstream.core.provider import filter_providers

    providers = filter_providers(lang=lang, tv_type=tv_type)

    data = {
        "providers": [p.to_dict() for p in providers],
        "total": len(providers),
    }

    def human_output(d):
        from cli_anything.cloudstream.utils.repl_skin import ReplSkin
        skin = ReplSkin("cloudstream", __version__)
        skin.info(f"{len(providers)} providers available")
        headers = ["Name", "URL", "Lang", "Type", "Types"]
        rows = [
            [p.name, p.main_url[:30], p.lang, p.provider_type,
             ", ".join(p.supported_types[:3])]
            for p in providers
        ]
        skin.table(headers, rows)

    _output(data, human_output)


@provider.command("info")
@click.argument("name")
def provider_info(name):
    """Show detailed info about a provider."""
    from cli_anything.cloudstream.core.provider import get_provider_info

    info = get_provider_info(name)
    if info:
        _output(info.to_dict())
    else:
        _output({"error": f"Provider '{name}' not found"})


# ── Extractor commands ────────────────────────────────────────────


@cli.group()
def extractor():
    """Manage link extractors."""
    pass


@extractor.command("list")
def extractor_list():
    """List available link extractors."""
    from cli_anything.cloudstream.core.provider import list_extractors

    extractors = list_extractors()

    data = {
        "extractors": [e.to_dict() for e in extractors],
        "total": len(extractors),
    }

    def human_output(d):
        from cli_anything.cloudstream.utils.repl_skin import ReplSkin
        skin = ReplSkin("cloudstream", __version__)
        skin.info(f"{len(extractors)} extractors available")
        headers = ["Name", "URL", "Referer Required"]
        rows = [[e.name, e.main_url[:35], "Yes" if e.requires_referer else "No"]
                for e in extractors]
        skin.table(headers, rows)

    _output(data, human_output)


# ── Session commands ──────────────────────────────────────────────


@cli.group()
def session():
    """Manage session state."""
    pass


@session.command("info")
@click.pass_context
def session_info(ctx):
    """Show current session info."""
    sess = ctx.obj["session"]
    _output(sess.info())


@session.command("history")
@click.option("--action", default=None, help="Filter by action type")
@click.option("--limit", default=20, help="Number of entries to show")
@click.pass_context
def session_history(ctx, action, limit):
    """Show session history."""
    sess = ctx.obj["session"]
    entries = sess.get_history(action=action, limit=limit)

    data = {"history": [e.to_dict() for e in entries], "total": len(entries)}

    def human_output(d):
        from cli_anything.cloudstream.utils.repl_skin import ReplSkin
        skin = ReplSkin("cloudstream", __version__)
        if not entries:
            skin.warning("No history entries")
            return
        headers = ["Action", "Query/URL", "Time"]
        rows = []
        import time
        for e in reversed(entries):
            ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(e.timestamp)) if e.timestamp else "-"
            rows.append([e.action, (e.query or e.url)[:40], ts])
        skin.table(headers, rows)

    _output(data, human_output)


@session.command("favorites")
@click.pass_context
def session_favorites(ctx):
    """List favorited content."""
    sess = ctx.obj["session"]
    favs = sess.list_favorites()

    data = {"favorites": [f.to_dict() for f in favs], "total": len(favs)}

    def human_output(d):
        from cli_anything.cloudstream.utils.repl_skin import ReplSkin
        skin = ReplSkin("cloudstream", __version__)
        if not favs:
            skin.warning("No favorites")
            return
        headers = ["#", "Name", "Type", "Provider"]
        rows = [[str(i + 1), f.name[:35], f.type, f.provider]
                for i, f in enumerate(favs)]
        skin.table(headers, rows)

    _output(data, human_output)


@session.command("reset")
@click.confirmation_option(prompt="Are you sure you want to reset the session?")
@click.pass_context
def session_reset(ctx):
    """Reset session to defaults."""
    sess = ctx.obj["session"]
    sess.reset()
    if not _json_output:
        from cli_anything.cloudstream.utils.repl_skin import ReplSkin
        skin = ReplSkin("cloudstream", __version__)
        skin.success("Session reset")
    else:
        _output({"status": "ok", "message": "Session reset"})


# ── Status command ────────────────────────────────────────────────


@cli.command()
def status():
    """Check backend component availability."""
    from cli_anything.cloudstream.utils.cloudstream_backend import check_backend_status

    result = check_backend_status()

    def human_output(d):
        from cli_anything.cloudstream.utils.repl_skin import ReplSkin
        skin = ReplSkin("cloudstream", __version__)
        skin.section("Backend Status")
        for name, info in result.items():
            available = info.get("available", False)
            if available:
                version = info.get("version", "")
                skin.success(f"{name}: {version}" if version else f"{name}: available")
            else:
                error = info.get("error", "not found")
                skin.error(f"{name}: {error[:60]}")

    _output(result, human_output)


# ── REPL command ──────────────────────────────────────────────────


@cli.command(hidden=True)
@click.pass_context
def repl(ctx):
    """Interactive REPL mode (default when no subcommand given)."""
    from cli_anything.cloudstream.utils.repl_skin import ReplSkin

    skin = ReplSkin("cloudstream", __version__)
    skin.print_banner()

    sess = ctx.obj["session"]
    pt_session = skin.create_prompt_session()

    commands = {
        "search <query>": "Search for content",
        "load <url|#>": "Load content details",
        "links <data|#>": "Extract streaming links",
        "download <#|url>": "Download a stream",
        "play <#|url>": "Open stream in browser",
        "provider list": "List providers",
        "extractor list": "List extractors",
        "session info": "Show session info",
        "session history": "Show history",
        "session favorites": "Show favorites",
        "status": "Check backend status",
        "help": "Show this help",
        "quit": "Exit the REPL",
    }

    while True:
        try:
            line = skin.get_input(pt_session, context=sess.state.last_search_query)
            if not line:
                continue

            parts = line.split(maxsplit=1)
            cmd = parts[0].lower()
            rest = parts[1] if len(parts) > 1 else ""

            if cmd in ("quit", "exit", "q"):
                skin.print_goodbye()
                break
            elif cmd == "help":
                skin.help(commands)
            elif cmd == "search" and rest:
                ctx.invoke(search, query=rest, provider=(), page=1)
            elif cmd == "load" and rest:
                ctx.invoke(load, url_or_index=rest)
            elif cmd == "links":
                ctx.invoke(links, data_or_index=rest or "1", provider=None)
            elif cmd == "download":
                ctx.invoke(download, link_index_or_url=rest or "1",
                          output_path=None, quality="1080", overwrite=False)
            elif cmd == "play":
                ctx.invoke(play, link_index_or_url=rest or "1")
            elif cmd == "provider":
                sub = rest.split()[0] if rest else "list"
                if sub == "list":
                    ctx.invoke(provider_list, lang=None, tv_type=None)
                elif sub == "info" and len(rest.split()) > 1:
                    ctx.invoke(provider_info, name=rest.split()[1])
            elif cmd == "extractor":
                ctx.invoke(extractor_list)
            elif cmd == "session":
                sub = rest.split()[0] if rest else "info"
                if sub == "info":
                    ctx.invoke(session_info)
                elif sub == "history":
                    ctx.invoke(session_history, action=None, limit=20)
                elif sub == "favorites":
                    ctx.invoke(session_favorites)
            elif cmd == "status":
                ctx.invoke(status)
            elif cmd == "favorite" and rest:
                # Quick favorite: favorite <index>
                if rest.isdigit():
                    result = sess.get_search_result(int(rest))
                    if result:
                        sess.add_favorite(
                            name=result.get("name", ""),
                            url=result.get("url", ""),
                            type=result.get("type", "Movie"),
                            poster_url=result.get("poster_url"),
                            provider=result.get("api_name", ""),
                        )
                        skin.success(f"Added to favorites: {result.get('name', '')}")
                    else:
                        skin.error(f"No search result at index {rest}")
            else:
                skin.warning(f"Unknown command: {cmd}. Type 'help' for commands.")

        except (KeyboardInterrupt, EOFError):
            skin.print_goodbye()
            break
        except Exception as e:
            skin.error(str(e))


# ── Entry point ───────────────────────────────────────────────────


def main():
    """Entry point for the CLI."""
    cli(obj={})


if __name__ == "__main__":
    main()
