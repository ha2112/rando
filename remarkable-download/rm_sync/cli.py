"""
cli.py
======
Typer CLI wrapping the rm-rebuilder pipeline.

Commands
--------
rm-sync sync      Full pipeline: rsync → trash scan → render all
rm-sync pull      Rsync only (no render)
rm-sync render    Render from local cache only
rm-sync status    Show document change status
rm-sync init      Interactive config setup → ~/.config/rm-sync/config.toml
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import List, Optional

import typer
from rich.console import Console
from rich.table import Table

# Flat imports match the style of the existing codebase.
# rm_sync/__init__.py injects this directory into sys.path so they resolve.
from client import RsyncClient, FileDownloader
from config import _BASE_DIR, DONE_DIR, PROFILES, PROFILE_ORDER
from main import (
    RenderStateTracker,
    _process_document,
    _resolve_profile_auto,
    discover_cached_uuids,
)
from models import DocumentMeta

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = typer.Typer(
    name="rm-sync",
    help="Download and render reMarkable documents to annotated PDFs.",
    no_args_is_help=True,
)
console = Console()
log = logging.getLogger("rm-sync")

# ---------------------------------------------------------------------------
# Global callback — shared flags
# ---------------------------------------------------------------------------


@app.callback()
def callback(
    ctx: typer.Context,
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Show what would be done without making changes",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Bypass change detection; re-render every document",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Enable DEBUG-level logging",
    ),
) -> None:
    """reMarkable sync and render tool."""
    ctx.ensure_object(dict)
    ctx.obj["dry_run"] = dry_run
    ctx.obj["force"] = force
    ctx.obj["verbose"] = verbose

    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    else:
        logging.getLogger().setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_paths(
    cache: Optional[Path],
    output: Optional[Path],
) -> tuple[Path, Path]:
    """Return (cache_dir, output_base) with defaults from config."""
    cache_dir = Path(cache) if cache else _BASE_DIR / ".RM_FILES"
    output_base = Path(output) if output else DONE_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)
    output_base.mkdir(parents=True, exist_ok=True)
    return cache_dir, output_base


def _get_doc_name(cache_dir: Path, uuid: str) -> str:
    """Best-effort visible name from cached metadata, falling back to UUID prefix."""
    meta_path = cache_dir / f"{uuid}.metadata"
    try:
        with meta_path.open(encoding="utf-8") as fh:
            return json.load(fh).get("visibleName", uuid[:12] + "…")
    except Exception:
        return uuid[:12] + "…"


# ---------------------------------------------------------------------------
# sync — full pipeline
# ---------------------------------------------------------------------------


@app.command()
def sync(
    ctx: typer.Context,
    mode: Optional[str] = typer.Option(
        None,
        "--mode",
        help=f"SSH profile. One of: {', '.join(sorted(PROFILES))}. Auto-detected when omitted.",
    ),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Output directory for rendered PDFs"
    ),
    cache: Optional[Path] = typer.Option(
        None, "--cache", "-c", help="Persistent cache directory"
    ),
    uuids: Optional[List[str]] = typer.Option(
        None, "--uuids", "-u", help="Specific document UUIDs to process"
    ),
) -> None:
    """Full pipeline: rsync → trash scan → render."""
    dry_run: bool = ctx.obj["dry_run"]
    force: bool = ctx.obj["force"]
    cache_dir, output_base = _resolve_paths(cache, output)

    # ---- dry-run: show plan and bail ----
    if dry_run:
        console.print("[bold cyan]Dry run — no changes will be made.[/bold cyan]")
        console.print(f"  Cache:  {cache_dir}")
        console.print(f"  Output: {output_base}")
        console.print(f"  Mode:   {mode or 'auto-detect'}")
        console.print(f"  Force:  {force}")
        if uuids:
            console.print(f"  UUIDs:  {', '.join(u[:12] + '…' for u in uuids)}")
        else:
            console.print("  Strategy: full sync → full render")
        return

    # ---- resolve SSH alias ----
    try:
        alias = _resolve_profile_auto(mode, PROFILES, PROFILE_ORDER)
    except (ValueError, ConnectionError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)

    client = RsyncClient(cache_dir=cache_dir, alias=alias)
    console.print(f"[dim]SSH alias: {alias}  |  cache: {cache_dir}[/dim]")

    # ---- rsync ----
    console.print("[bold]Syncing…[/bold]")
    if uuids:
        console.print(f"  Selective sync — {len(uuids)} document(s)")
        FileDownloader(client=client, uuids=list(uuids)).download_all()
    else:
        client.full_sync()

    # ---- trash scan ----
    tracker = RenderStateTracker(cache_dir=cache_dir, output_base=output_base)
    console.print("[bold]Trash scan…[/bold]")
    tracker.scan_and_move_trash()

    # ---- force: purge .TRASH ----
    if force:
        console.print("[bold]Purging .TRASH/ (--force)…[/bold]")
        tracker.purge_trash()

    # ---- resolve render list ----
    if uuids:
        render_uuids = list(uuids)
    else:
        render_uuids = discover_cached_uuids(cache_dir)

    if not render_uuids:
        console.print("[yellow]No documents to render.[/yellow]")
        tracker.save()
        return

    # ---- render ----
    explicit = set(uuids) if uuids else set()
    console.print(f"[bold]Rendering {len(render_uuids)} document(s)…[/bold]")

    rendered = 0
    skipped = 0
    errors: list[tuple[str, str, Exception]] = []

    for doc_uuid in render_uuids:
        doc_name = _get_doc_name(cache_dir, doc_uuid)
        should_force = force or (doc_uuid in explicit)

        try:
            did_render = _process_document(
                doc_uuid=doc_uuid,
                cache_dir=cache_dir,
                client=client,
                output_base=output_base,
                tracker=tracker,
                force=should_force,
            )
            if did_render:
                rendered += 1
            else:
                skipped += 1
        except Exception as exc:
            console.print(f"[red]Failed:[/red] '{doc_name}' ({doc_uuid[:12]}…) — {exc}")
            log.exception("Error processing %s", doc_uuid)
            errors.append((doc_name, doc_uuid, exc))

    tracker.save()

    # ---- summary ----
    console.print()
    summary = Table(title="Sync Summary")
    summary.add_column("Metric", style="dim")
    summary.add_column("Count")
    summary.add_row("Rendered", str(rendered))
    summary.add_row("Skipped (unchanged)", str(skipped))
    if errors:
        summary.add_row("Failed", f"[red]{len(errors)}[/red]")
    console.print(summary)

    if errors:
        console.print("[red]Some documents failed:[/red]")
        for name, uuid, exc in errors:
            console.print(f"  ✗  {name}  [{uuid[:12]}…]  —  {exc}")
        raise typer.Exit(2)

    console.print(f"[green]Done. Output → {output_base}[/green]")


# ---------------------------------------------------------------------------
# pull — rsync only
# ---------------------------------------------------------------------------


@app.command()
def pull(
    ctx: typer.Context,
    mode: Optional[str] = typer.Option(
        None,
        "--mode",
        help=f"SSH profile. One of: {', '.join(sorted(PROFILES))}. Auto-detected when omitted.",
    ),
    cache: Optional[Path] = typer.Option(
        None, "--cache", "-c", help="Persistent cache directory"
    ),
    uuids: Optional[List[str]] = typer.Option(
        None, "--uuids", "-u", help="Specific document UUIDs to download"
    ),
) -> None:
    """Rsync files from the tablet — no rendering."""
    dry_run: bool = ctx.obj["dry_run"]
    cache_dir, output_base = _resolve_paths(cache, None)

    if dry_run:
        console.print("[bold cyan]Dry run — no changes will be made.[/bold cyan]")
        console.print(f"  Cache:  {cache_dir}")
        console.print(f"  Mode:   {mode or 'auto-detect'}")
        if uuids:
            console.print(f"  UUIDs:  {', '.join(u[:12] + '…' for u in uuids)}")
        else:
            console.print("  Strategy: full sync")
        return

    try:
        alias = _resolve_profile_auto(mode, PROFILES, PROFILE_ORDER)
    except (ValueError, ConnectionError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(1)

    client = RsyncClient(cache_dir=cache_dir, alias=alias)
    console.print(f"[dim]SSH alias: {alias}  |  cache: {cache_dir}[/dim]")

    console.print("[bold]Syncing…[/bold]")
    if uuids:
        console.print(f"  Selective sync — {len(uuids)} document(s)")
        FileDownloader(client=client, uuids=list(uuids)).download_all()
    else:
        client.full_sync()

    # trash scan
    tracker = RenderStateTracker(cache_dir=cache_dir, output_base=output_base)
    console.print("[bold]Trash scan…[/bold]")
    tracker.scan_and_move_trash()
    tracker.save()

    console.print("[green]Pull complete.[/green]")


# ---------------------------------------------------------------------------
# render — from cache only
# ---------------------------------------------------------------------------


@app.command()
def render(
    ctx: typer.Context,
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Output directory for rendered PDFs"
    ),
    cache: Optional[Path] = typer.Option(
        None, "--cache", "-c", help="Persistent cache directory"
    ),
    uuids: Optional[List[str]] = typer.Option(
        None, "--uuids", "-u", help="Specific document UUIDs to render"
    ),
) -> None:
    """Render documents from the local cache — no network required."""
    dry_run: bool = ctx.obj["dry_run"]
    force: bool = ctx.obj["force"]
    cache_dir, output_base = _resolve_paths(cache, output)

    if not cache_dir.exists():
        console.print("[yellow]No cache directory found. Run a pull first.[/yellow]")
        raise typer.Exit(1)

    tracker = RenderStateTracker(cache_dir=cache_dir, output_base=output_base)

    if force:
        tracker.purge_trash()

    if uuids:
        render_uuids = list(uuids)
    else:
        render_uuids = discover_cached_uuids(cache_dir)

    if not render_uuids:
        console.print("[yellow]No renderable documents found in cache.[/yellow]")
        return

    if dry_run:
        console.print("[bold cyan]Dry run — would render:[/bold cyan]")
        for doc_uuid in render_uuids:
            name = _get_doc_name(cache_dir, doc_uuid)
            needs = tracker.needs_render(doc_uuid)
            status = "[yellow]changed[/yellow]" if needs else "[dim]unchanged[/dim]"
            if force:
                status = "[cyan]forced[/cyan]"
            console.print(f"  {name}  ({doc_uuid[:12]}…)  {status}")
        return

    explicit = set(uuids) if uuids else set()
    console.print(f"[bold]Rendering {len(render_uuids)} document(s)…[/bold]")

    rendered = 0
    skipped = 0
    errors: list[tuple[str, str, Exception]] = []

    for doc_uuid in render_uuids:
        doc_name = _get_doc_name(cache_dir, doc_uuid)
        should_force = force or (doc_uuid in explicit)

        try:
            did_render = _process_document(
                doc_uuid=doc_uuid,
                cache_dir=cache_dir,
                client=None,  # no network
                output_base=output_base,
                tracker=tracker,
                force=should_force,
            )
            if did_render:
                rendered += 1
            else:
                skipped += 1
        except Exception as exc:
            console.print(f"[red]Failed:[/red] '{doc_name}' ({doc_uuid[:12]}…) — {exc}")
            log.exception("Error processing %s", doc_uuid)
            errors.append((doc_name, doc_uuid, exc))

    tracker.save()

    console.print()
    summary = Table(title="Render Summary")
    summary.add_column("Metric", style="dim")
    summary.add_column("Count")
    summary.add_row("Rendered", str(rendered))
    summary.add_row("Skipped (unchanged)", str(skipped))
    if errors:
        summary.add_row("Failed", f"[red]{len(errors)}[/red]")
    console.print(summary)

    if errors:
        for name, uuid, exc in errors:
            console.print(f"  ✗  {name}  [{uuid[:12]}…]  —  {exc}")
        raise typer.Exit(2)

    console.print(f"[green]Done. Output → {output_base}[/green]")


# ---------------------------------------------------------------------------
# status — show document change state
# ---------------------------------------------------------------------------


@app.command()
def status(
    ctx: typer.Context,
    cache: Optional[Path] = typer.Option(
        None, "--cache", "-c", help="Persistent cache directory"
    ),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Output directory for state tracking"
    ),
) -> None:
    """Show which cached documents are new, changed, unchanged, or deleted."""
    cache_dir, output_base = _resolve_paths(cache, output)

    if not cache_dir.exists():
        console.print("[yellow]No cache directory found. Run a sync first.[/yellow]")
        raise typer.Exit(1)

    tracker = RenderStateTracker(cache_dir=cache_dir, output_base=output_base)

    cached = set(discover_cached_uuids(cache_dir))
    known = set(tracker._state.keys())

    new = cached - known
    deleted = known - cached
    changed = {u for u in (cached & known) if tracker.needs_render(u)}
    unchanged = (cached & known) - changed

    if not (new or changed or unchanged or deleted):
        console.print("[dim]No documents in cache.[/dim]")
        return

    table = Table(title="Document Status", highlight=True)
    table.add_column("Name", style="bold")
    table.add_column("UUID", style="dim")
    table.add_column("Status")

    for uuid in sorted(new):
        table.add_row(_get_doc_name(cache_dir, uuid), uuid, "[cyan]new[/cyan]")
    for uuid in sorted(changed):
        table.add_row(_get_doc_name(cache_dir, uuid), uuid, "[yellow]changed[/yellow]")
    for uuid in sorted(unchanged):
        table.add_row(_get_doc_name(cache_dir, uuid), uuid, "[green]unchanged[/green]")
    for uuid in sorted(deleted):
        table.add_row(_get_doc_name(cache_dir, uuid), uuid, "[red]deleted[/red]")

    console.print(table)

    total = len(new) + len(changed) + len(unchanged) + len(deleted)
    console.print(
        f"\n[dim]{total} document(s): "
        f"[cyan]{len(new)} new[/cyan], "
        f"[yellow]{len(changed)} changed[/yellow], "
        f"[green]{len(unchanged)} unchanged[/green], "
        f"[red]{len(deleted)} deleted[/red][/dim]"
    )


# ---------------------------------------------------------------------------
# init — interactive config setup
# ---------------------------------------------------------------------------


@app.command()
def init() -> None:
    """Create ~/.config/rm-sync/config.toml interactively."""
    import os

    config_dir = Path.home() / ".config" / "rm-sync"
    config_path = config_dir / "config.toml"

    console.print("[bold]rm-sync configuration setup[/bold]\n")

    # ---- SSH profiles ----
    console.print("[bold]SSH Profile Aliases[/bold]")
    console.print("[dim]These should match Host entries in ~/.ssh/config[/dim]\n")

    profiles: dict[str, str] = {}
    for key in PROFILE_ORDER:
        existing = PROFILES.get(key, "")
        default = existing or f"remarkable-{key}"
        value = typer.prompt(
            f"  {key}",
            default=default,
        )
        if value:
            profiles[key] = value

    # ---- Paths ----
    console.print("\n[bold]Local Paths[/bold]\n")

    output_default = str(DONE_DIR)
    output_val = typer.prompt("  Output directory", default=output_default)
    cache_default = str(_BASE_DIR / ".RM_FILES")
    cache_val = typer.prompt("  Cache directory", default=cache_default)

    # ---- Profile order ----
    console.print("\n[bold]Profile Priority Order[/bold]")
    console.print("[dim]Comma-separated list. Earlier = tried first.[/dim]\n")
    order_val = typer.prompt(
        "  Order",
        default=",".join(PROFILE_ORDER),
    )
    profile_order = [p.strip() for p in order_val.split(",") if p.strip()]

    # ---- Build TOML ----
    lines: list[str] = []
    lines.append("# rm-sync configuration")
    lines.append("# Generated by: rm-sync init")
    lines.append("")

    lines.append("[profiles]")
    for key, value in profiles.items():
        lines.append(f'{key} = "{value}"')
    lines.append("")

    lines.append("# Profile auto-detection order")
    lines.append(f"profile_order = [{', '.join(repr(p) for p in profile_order)}]")
    lines.append("")

    lines.append("[paths]")
    lines.append(f'output_dir = "{output_val}"')
    lines.append(f'cache_dir = "{cache_val}"')
    lines.append("")

    toml_content = "\n".join(lines) + "\n"

    # ---- Write ----
    if config_path.exists():
        overwrite = typer.confirm(
            f"{config_path} already exists. Overwrite?", default=False
        )
        if not overwrite:
            console.print("[yellow]Aborted.[/yellow]")
            raise typer.Exit(0)

    config_dir.mkdir(parents=True, exist_ok=True)
    config_path.write_text(toml_content)
    console.print(f"\n[green]Config saved → {config_path}[/green]")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app()
