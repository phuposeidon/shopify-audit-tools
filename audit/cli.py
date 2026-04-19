"""
CLI — Typer app with Rich output.
Entry point: `audit <url>`
"""
from __future__ import annotations

import asyncio
import csv
import json
import sys
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich import print as rprint
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table
from rich.text import Text

from audit import __version__
from audit.core.models import AuditResult, Issue
from audit.core.orchestrator import run_audit

app     = typer.Typer(name="audit", help="🤖 AI Visibility Audit for ecommerce", add_completion=False)
console = Console(stderr=True)

SEV_COLOR = {"CRITICAL": "red", "HIGH": "orange3", "MEDIUM": "yellow", "LOW": "dim", "INFO": "dim"}
SEV_ICON  = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "⚪", "INFO": "ℹ️"}
FIX_LABEL = {"auto": "[green]✅ Auto[/green]", "theme": "[blue]🔧 Theme[/blue]", "manual": "[dim]📖 Manual[/dim]"}


@app.command(name="audit")
def audit_url(
    url: Annotated[str, typer.Argument(help="Product URL to audit")],
    device: Annotated[str, typer.Option(help="mobile | desktop")] = "mobile",
    no_lighthouse: Annotated[bool, typer.Option("--no-lighthouse")] = False,
    no_browser: Annotated[bool, typer.Option("--no-browser")] = False,
    out: Annotated[Optional[Path], typer.Option(help="Save .json or .csv")] = None,
    output_json: Annotated[bool, typer.Option("--json")] = False,
    quiet: Annotated[bool, typer.Option("--quiet", "-q")] = False,
) -> None:
    """Audit a single product URL for AI readiness."""
    result = asyncio.run(_run_with_progress(url, device, no_lighthouse, no_browser, quiet))

    if output_json:
        print(json.dumps(result.to_dict(), indent=2))
    elif not quiet:
        _print_result(result)
    else:
        print(result.score.total)

    if out:
        _save(result, out)
    raise typer.Exit(0 if not result.bot_blocked else 1)


@app.command()
def detect(
    url: Annotated[str, typer.Argument(help="URL to fingerprint")],
    output_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Detect ecommerce platform from URL fingerprints."""
    from audit.platform.detector import detect_platform
    p = asyncio.run(detect_platform(url))
    if output_json:
        print(json.dumps({"platform": p.platform, "confidence": p.confidence, "signals": p.signals}))
    else:
        rprint(f"\n{p.icon}  [bold]{p.platform}[/bold] ({p.confidence}% confidence)")
        for s in p.signals:
            rprint(f"   [dim]→[/dim] {s}")
        print()


@app.command()
def batch(
    urls_file: Annotated[Path, typer.Argument(help="File with one URL per line")],
    workers: Annotated[int, typer.Option(help="Concurrent audits")] = 2,
    out: Annotated[Optional[Path], typer.Option()] = None,
    no_lighthouse: Annotated[bool, typer.Option("--no-lighthouse")] = False,
) -> None:
    """Audit multiple URLs from a file."""
    if not urls_file.exists():
        rprint(f"[red]File not found:[/red] {urls_file}")
        raise typer.Exit(1)
    urls = [u.strip() for u in urls_file.read_text().splitlines()
            if u.strip() and not u.startswith("#")]
    rprint(f"\n[cyan]Batch audit:[/cyan] {len(urls)} URLs (workers={workers})\n")
    results = asyncio.run(_batch_run(urls, workers, no_lighthouse))
    for r in results:
        c = r.score.grade_color
        rprint(f"  [{c}]{r.score.total:3d}/100 {r.score.grade}[/{c}]  {r.url[:70]}")
    if out:
        _save_batch(results, out)
        rprint(f"\n[dim]Saved → {out}[/dim]")


@app.command()
def version() -> None:
    """Print version."""
    rprint(f"shopify-audit-tools [cyan]{__version__}[/cyan]")


# ── Rich renderer ─────────────────────────────────────────────────

def _print_result(r: AuditResult) -> None:
    color = r.score.grade_color
    rprint()
    rprint(Panel(
        Text(f"  Score: {r.score.total}/100 — Grade {r.score.grade}  {r.score.grade_label}  ",
             style=f"bold {color} on grey15"),
        title=f"[bold]{r.platform.icon} {r.url[:65]}[/bold]",
        subtitle=f"[dim]{r.duration_ms}ms[/dim]",
        border_style=color,
    ))

    t = Table(show_header=False, box=None, padding=(0, 1))
    t.add_column("Dim", min_width=25)
    t.add_column("Score", justify="right", min_width=7)
    t.add_column("Bar", min_width=24)

    def bar(val: int, mx: int) -> str:
        f = round(val / mx * 20)
        pct = round(val / mx * 100)
        c = "green" if pct >= 80 else "yellow" if pct >= 50 else "red"
        return f"[{c}]{'█' * f}[/{c}][dim]{'░' * (20 - f)}[/dim] {pct}%"

    t.add_row("⚡ Core Web Vitals",     f"{r.score.cwv}/25",    bar(r.score.cwv, 25))
    t.add_row("🤖 AI Discoverability",  f"{r.score.discover}/35", bar(r.score.discover, 35))
    t.add_row("📋 Structured Data",     f"{r.score.schema}/20",  bar(r.score.schema, 20))
    t.add_row("✍️  Content Quality",    f"{r.score.content}/15", bar(r.score.content, 15))
    t.add_row("📜 Policy",              f"{r.score.policy}/5",   bar(r.score.policy, 5))
    if r.score.bonus:
        t.add_row("🎁 Bonus",           f"+{r.score.bonus}",    "[dim]robots + MCP + schema[/dim]")
    rprint(t)

    # CWV inline
    cwv = r.cwv
    rows = [
        ("LCP",  f"{cwv.lcp/1000:.2f}s" if cwv.lcp else "—",  cwv.lcp and cwv.lcp <= 2500),
        ("CLS",  f"{cwv.cls:.3f}"       if cwv.cls else "—",  cwv.cls and cwv.cls <= 0.1),
        ("INP",  f"{cwv.inp:.0f}ms"     if cwv.inp else "—",  cwv.inp and cwv.inp <= 200),
        ("FCP",  f"{cwv.fcp/1000:.2f}s" if cwv.fcp else "—",  cwv.fcp and cwv.fcp <= 1800),
    ]
    rprint("\n  " + "   ".join(
        f"{'✅' if g else '🔴'} {l}: [bold]{v}[/bold]" for l, v, g in rows
    ))

    # AI access
    ai = r.ai_access
    def _s(val: Optional[bool]) -> str:
        return "[green]✅[/green]" if val else ("[red]🔴 Blocked[/red]" if val is False else "[dim]❓[/dim]")
    rprint(f"\n  GPTBot: {_s(ai.gptbot_allowed)}  Gemini: {_s(ai.google_extended_allowed)}  "
           f"MCP: {'[green]✅[/green]' if ai.mcp_detected else '[yellow]⚠[/yellow]'}  "
           f"Schema: {'[green]✅[/green]' if ai.has_product_schema else '[red]🔴[/red]'}  "
           f"GTIN: {'[green]✅ ' + ai.gtin_in_schema + '[/green]' if ai.gtin_in_schema else '[red]🔴[/red]'}")

    # Issues table
    if r.issues:
        it = Table(show_header=True, header_style="bold dim", box=None, padding=(0, 1))
        it.add_column("", min_width=2)
        it.add_column("Issue", min_width=48)
        it.add_column("Pts", justify="right", min_width=4)
        it.add_column("Fix", min_width=12)
        for issue in r.issues[:12]:
            c = SEV_COLOR.get(issue.severity, "white")
            it.add_row(
                SEV_ICON.get(issue.severity, ""),
                f"[{c}]{issue.title[:55]}[/{c}]",
                f"[red]-{issue.points_lost}[/red]" if issue.points_lost else "[dim]0[/dim]",
                FIX_LABEL.get(issue.fix_type, ""),
            )
        rprint()
        rprint(it)
    rprint()


# ── Helpers ───────────────────────────────────────────────────────

async def _run_with_progress(url, device, no_lh, no_br, quiet) -> AuditResult:
    if quiet:
        return await run_audit(url, device=device, skip_lighthouse=no_lh, skip_browser=no_br)
    with Progress(SpinnerColumn(), TextColumn("{task.description}"),
                  TimeElapsedColumn(), console=Console(stderr=True), transient=True) as p:
        task = p.add_task(f"[cyan]Auditing[/cyan] {url[:60]}…", total=None)
        result = await run_audit(url, device=device, skip_lighthouse=no_lh, skip_browser=no_br)
        p.update(task, description=f"[green]Done[/green] {url[:60]}")
    return result


async def _batch_run(urls: list[str], workers: int, no_lh: bool) -> list[AuditResult]:
    sem = asyncio.Semaphore(workers)
    async def _one(u: str) -> AuditResult:
        async with sem:
            return await run_audit(u, skip_lighthouse=no_lh)
    return await asyncio.gather(*[_one(u) for u in urls])


def _save(r: AuditResult, path: Path) -> None:
    if str(path).endswith(".csv"):
        _save_batch([r], path)
    else:
        path.write_text(json.dumps(r.to_dict(), indent=2))
    rprint(f"[dim]Saved → {path}[/dim]")


def _save_batch(results: list[AuditResult], path: Path) -> None:
    if str(path).endswith(".json"):
        path.write_text(json.dumps([r.to_dict() for r in results], indent=2))
    else:
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["url", "platform", "score", "grade", "cwv", "discover",
                        "schema", "content", "policy", "bonus", "bot_blocked", "lcp_ms", "cls"])
            for r in results:
                w.writerow([r.url, r.platform.platform, r.score.total, r.score.grade,
                            r.score.cwv, r.score.discover, r.score.schema, r.score.content,
                            r.score.policy, r.score.bonus, r.bot_blocked, r.cwv.lcp, r.cwv.cls])


def main() -> None:
    if len(sys.argv) >= 2 and sys.argv[1].startswith("http"):
        sys.argv.insert(1, "audit")
    app()


if __name__ == "__main__":
    main()
