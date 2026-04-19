"""
Tier 1 — agent-browser: accessibility tree snapshot + CWV browser-side.
Wraps the agent-browser CLI as a subprocess.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from audit.core.models import CWV

AGENT_BROWSER = os.environ.get("AGENT_BROWSER_PATH", shutil.which("agent-browser") or "agent-browser")
TIMEOUT_S = 60


@dataclass
class BrowserResult:
    h1_count: int = 0
    image_count: int = 0
    missing_alt_count: int = 0
    has_price: bool = False
    has_breadcrumb: bool = False
    has_js_hidden_content: bool = False
    snapshot_chars: int = 0
    snapshot_text: str = ""
    cwv: CWV = field(default_factory=CWV)
    screenshot_path: Optional[str] = None
    errors: list[str] = field(default_factory=list)
    available: bool = True


async def run_tier1(url: str) -> BrowserResult:
    """Run agent-browser audit: a11y snapshot + CWV measurement."""
    if not _agent_browser_available():
        return BrowserResult(
            available=False,
            errors=["agent-browser not found — install with: npm install -g @modelcontextprotocol/agent-browser"],
        )

    session_id = f"audit-{os.getpid()}"
    screenshot_path = Path(tempfile.mkdtemp()) / f"{session_id}.png"
    result = BrowserResult()

    try:
        # Open + wait for networkidle
        await _run_ab(["open", url, "--session", session_id])
        await _run_ab(["wait", "--load", "networkidle", "--session", session_id], timeout=45)

        # Snapshot
        snap_raw = await _run_ab(["snapshot", "-i", "-c", "--json", "--session", session_id])
        snapshot = _parse_json(snap_raw)
        snap_text: str = snapshot.get("data", {}).get("snapshot", "") or ""
        refs: dict = snapshot.get("data", {}).get("refs", {})

        result.snapshot_text  = snap_text
        result.snapshot_chars = len(snap_text)
        result.h1_count       = len(re.findall(r'heading "[^"]+"\s*\[level=1', snap_text))
        result.has_price      = bool(re.search(r"\$[\d,]+", snap_text)) or "price" in snap_text.lower()
        result.has_breadcrumb = bool(re.search(r'navigation "[^"]*breadcrumb[^"]*"', snap_text, re.I))

        imgs = [r for r in refs.values() if isinstance(r, dict) and r.get("role") == "img"]
        result.image_count       = len(imgs)
        result.missing_alt_count = sum(1 for r in imgs if not (r.get("name") or "").strip())

        # JS-hidden content heuristic: few chars but page otherwise loaded
        result.has_js_hidden_content = result.snapshot_chars < 800 and result.h1_count > 0

        # CWV via JS eval
        cwv_script = """new Promise(r => {
            const n = performance.getEntriesByType('navigation')[0] || {};
            const p = performance.getEntriesByName('first-contentful-paint')[0];
            const res = {
                ttfb: n.responseStart ? Math.round(n.responseStart - n.requestStart) : null,
                fcp:  p ? Math.round(p.startTime) : null,
                lcp:  null
            };
            const obs = new PerformanceObserver(l => {
                const es = l.getEntries();
                if (es.length) res.lcp = Math.round(es[es.length-1].startTime);
            });
            try { obs.observe({type:'largest-contentful-paint',buffered:true}); } catch(e) {}
            setTimeout(() => { obs.disconnect(); r(JSON.stringify(res)); }, 3000);
        })"""
        cwv_raw = await _run_ab(["eval", cwv_script, "--session", session_id], timeout=15)
        cwv_data = _parse_json(_parse_json(cwv_raw) if isinstance(_parse_json(cwv_raw), str)
                               else json.dumps(_parse_json(cwv_raw)))
        if isinstance(cwv_data, dict):
            result.cwv = CWV(
                lcp=cwv_data.get("lcp"),
                fcp=cwv_data.get("fcp"),
                ttfb=cwv_data.get("ttfb"),
            )

        # Screenshot (best-effort)
        try:
            await _run_ab(["screenshot", str(screenshot_path), "--session", session_id])
            if screenshot_path.exists():
                result.screenshot_path = str(screenshot_path)
        except Exception:
            pass

    except Exception as exc:
        result.errors.append(f"tier1: {exc}")
    finally:
        try:
            await _run_ab(["close", "--session", session_id])
        except Exception:
            pass

    return result


# ── Helpers ───────────────────────────────────────────────────────

def _agent_browser_available() -> bool:
    return shutil.which(AGENT_BROWSER) is not None or Path(AGENT_BROWSER).exists()


async def _run_ab(args: list[str], timeout: int = TIMEOUT_S) -> str:
    proc = await asyncio.create_subprocess_exec(
        AGENT_BROWSER, *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError(f"agent-browser timeout ({timeout}s): {' '.join(args)}")
    if proc.returncode not in (0, None):
        raise RuntimeError(f"agent-browser error (exit {proc.returncode}): {stderr.decode()[:200]}")
    return stdout.decode()


def _parse_json(raw: str) -> any:
    try:
        return json.loads(raw.strip())
    except Exception:
        return {}
