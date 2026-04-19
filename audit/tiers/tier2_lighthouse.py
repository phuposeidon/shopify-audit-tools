"""
Tier 2 — Lighthouse: Performance, SEO, Accessibility, Best Practices.
Calls the lighthouse CLI as a subprocess (JSON output).
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from audit.core.models import CWV

LIGHTHOUSE_BIN = os.environ.get("LIGHTHOUSE_PATH", shutil.which("lighthouse") or "lighthouse")
CHROME_PATH    = os.environ.get("CHROME_PATH", shutil.which("chromium") or shutil.which("google-chrome") or "chromium")
TIMEOUT_S      = 120  # Lighthouse can take 60-90s


@dataclass
class LighthouseResult:
    performance: int    = 0   # 0-100
    accessibility: int  = 0
    seo: int            = 0
    best_practices: int = 0
    cwv: CWV            = field(default_factory=CWV)
    failing_audits: list[dict] = field(default_factory=list)
    available: bool     = True
    errors: list[str]   = field(default_factory=list)


async def run_tier2(url: str, device: str = "mobile") -> LighthouseResult:
    """Run Lighthouse CLI and parse JSON output."""
    if not _lighthouse_available():
        return LighthouseResult(
            available=False,
            errors=["lighthouse not found — install with: npm install -g lighthouse"],
        )

    out_file = Path(tempfile.mkdtemp()) / "lh-result.json"

    form_factor    = "desktop" if device == "desktop" else "mobile"
    emulation_flag = "--preset=desktop" if device == "desktop" else ""

    cmd = [
        LIGHTHOUSE_BIN, url,
        "--output=json",
        f"--output-path={out_file}",
        "--only-categories=performance,accessibility,seo,best-practices",
        f"--form-factor={form_factor}",
        "--chrome-flags=--headless=new --no-sandbox --disable-setuid-sandbox "
        "--disable-dev-shm-usage --disable-gpu",
        "--quiet",
        "--no-enable-error-reporting",
    ]
    if CHROME_PATH:
        cmd.append(f"--chrome-path={CHROME_PATH}")
    if emulation_flag:
        cmd.append(emulation_flag)

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=TIMEOUT_S)

        if proc.returncode not in (0, None):
            err = stderr.decode()[:300]
            return LighthouseResult(errors=[f"lighthouse exit {proc.returncode}: {err}"])

        if not out_file.exists():
            return LighthouseResult(errors=["lighthouse produced no output file"])

        lhr = json.loads(out_file.read_text())
        return _parse_lhr(lhr)

    except asyncio.TimeoutError:
        return LighthouseResult(errors=[f"lighthouse timeout ({TIMEOUT_S}s)"])
    except Exception as exc:
        return LighthouseResult(errors=[f"lighthouse: {exc}"])
    finally:
        try:
            out_file.unlink(missing_ok=True)
        except Exception:
            pass


# ── LHR parser ────────────────────────────────────────────────────

def _parse_lhr(lhr: dict) -> LighthouseResult:
    cats   = lhr.get("categories", {})
    audits = lhr.get("audits", {})

    def score(key: str) -> int:
        s = cats.get(key, {}).get("score")
        return round(s * 100) if s is not None else 0

    def metric(key: str) -> Optional[float]:
        v = audits.get(key, {}).get("numericValue")
        return v if v is not None else None

    failing = [
        {
            "id":    audit_id,
            "title": a.get("title", "")[:60],
            "score": a.get("score"),
            "value": a.get("displayValue", "")[:30],
        }
        for audit_id, a in audits.items()
        if a.get("score") is not None and a["score"] < 0.9
    ]
    failing.sort(key=lambda x: (x["score"] or 0))

    return LighthouseResult(
        performance=score("performance"),
        accessibility=score("accessibility"),
        seo=score("seo"),
        best_practices=score("best-practices"),
        cwv=CWV(
            lcp=metric("largest-contentful-paint"),
            cls=metric("cumulative-layout-shift"),
            inp=metric("interaction-to-next-paint"),
            fcp=metric("first-contentful-paint"),
            tbt=metric("total-blocking-time"),
        ),
        failing_audits=failing[:10],
    )


def _lighthouse_available() -> bool:
    return shutil.which(LIGHTHOUSE_BIN) is not None or Path(LIGHTHOUSE_BIN).exists()
