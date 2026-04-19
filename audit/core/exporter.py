"""
Export module — handles converting AuditResult to CSV, HTML, and PDF.
"""
import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader

from audit.core.models import AuditResult


def export_csv(results: list[AuditResult], path: Path) -> None:
    """Export one or more results to a CSV file."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "url", "platform", "score", "grade", "cwv", "discover",
            "schema", "content", "policy", "bonus", "bot_blocked", "lcp_ms", "cls"
        ])
        for r in results:
            w.writerow([
                r.url, r.platform.platform, r.score.total, r.score.grade,
                r.score.cwv, r.score.discover, r.score.schema, r.score.content,
                r.score.policy, r.score.bonus, r.bot_blocked, r.cwv.lcp, r.cwv.cls
            ])


def export_html(result: AuditResult, path: Optional[Path] = None) -> str:
    """
    Render HTML report using Jinja2 template.
    If path is provided, saves to file. Returns the HTML string.
    """
    template_dir = Path(__file__).parent.parent / "templates"
    env = Environment(loader=FileSystemLoader(template_dir))
    template = env.get_template("report.html")

    html_content = template.render(
        result=result,
        generation_date=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )

    if path:
        path.write_text(html_content, encoding="utf-8")
        
    return html_content


async def export_pdf(result: AuditResult, path: Path) -> None:
    """
    Render HTML report and print to PDF using Playwright Chromium.
    Requires playwright to be installed.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise RuntimeError("Playwright is required for PDF export. Please install it.")

    html_content = export_html(result)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"]
        )
        page = await browser.new_page()
        # Set content and wait for any remote assets (Tailwind CSS via CDN) to load
        await page.set_content(html_content, wait_until="networkidle")
        await page.pdf(
            path=str(path),
            format="A4",
            print_background=True,
            margin={"top": "10mm", "bottom": "10mm", "left": "10mm", "right": "10mm"}
        )
        await browser.close()
