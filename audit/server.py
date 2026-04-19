"""
FastAPI Web server — REST API + simple HTML dashboard.
Entry point: `audit-web` or `docker compose up web`
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer
import uvicorn
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, HttpUrl

from audit import __version__
from audit.core.models import AuditResult
from audit.core.orchestrator import run_audit

# ── In-memory job store (replace with Redis for production) ───────
_JOBS: dict[str, dict] = {}
_MAX_CONCURRENT = int(os.environ.get("MAX_CONCURRENT_AUDITS", "2"))
_semaphore: Optional[asyncio.Semaphore] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _semaphore
    _semaphore = asyncio.Semaphore(_MAX_CONCURRENT)
    yield


app = FastAPI(
    title="Shopify Audit Tools",
    version=__version__,
    description="AI Visibility Audit REST API",
    lifespan=lifespan,
)


# ── Request/Response models ───────────────────────────────────────

class AuditRequest(BaseModel):
    url: str
    device: str = "mobile"
    skip_lighthouse: bool = False

class BatchRequest(BaseModel):
    urls: list[str]
    device: str = "mobile"
    workers: int = 2
    skip_lighthouse: bool = False


# ── Background audit runner ───────────────────────────────────────

async def _run_job(job_id: str, req: AuditRequest) -> None:
    assert _semaphore is not None
    _JOBS[job_id]["status"] = "running"
    async with _semaphore:
        try:
            result = await run_audit(req.url, device=req.device, skip_lighthouse=req.skip_lighthouse)
            _JOBS[job_id].update({"status": "done", "result": result.to_dict(), "completed_at": datetime.utcnow().isoformat()})
        except Exception as exc:
            _JOBS[job_id].update({"status": "error", "error": str(exc)})


# ── API routes ────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"ok": True, "version": __version__, "jobs": len(_JOBS)}


@app.post("/audit")
async def start_audit(req: AuditRequest, background_tasks: BackgroundTasks):
    """Start an async audit. Returns job_id to poll."""
    job_id = str(uuid.uuid4())[:8]
    _JOBS[job_id] = {"job_id": job_id, "url": req.url, "status": "queued", "created_at": datetime.utcnow().isoformat()}
    background_tasks.add_task(_run_job, job_id, req)
    return {"job_id": job_id, "url": req.url, "status": "queued"}


@app.post("/audit/sync")
async def audit_sync(req: AuditRequest):
    """Synchronous audit — waits for completion (max ~90s)."""
    result = await run_audit(req.url, device=req.device, skip_lighthouse=req.skip_lighthouse)
    return result.to_dict()


@app.get("/audit/{job_id}")
async def get_job(job_id: str):
    if job_id not in _JOBS:
        raise HTTPException(status_code=404, detail="Job not found")
    return _JOBS[job_id]


@app.post("/batch")
async def batch_audit(req: BatchRequest):
    """Batch audit — synchronous, respects workers limit."""
    sem = asyncio.Semaphore(req.workers)
    async def _one(url: str):
        async with sem:
            r = await run_audit(url, device=req.device, skip_lighthouse=req.skip_lighthouse)
            return r.to_dict()
    results = await asyncio.gather(*[_one(u) for u in req.urls], return_exceptions=True)
    return [r if not isinstance(r, Exception) else {"error": str(r)} for r in results]


@app.get("/detect")
async def detect(url: str):
    from audit.platform.detector import detect_platform
    p = await detect_platform(url)
    return {"platform": p.platform, "confidence": p.confidence, "signals": p.signals}


# ── HTML Dashboard ────────────────────────────────────────────────

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Shopify Audit Tools</title>
<style>
  :root{--bg:#0f1117;--card:#1a1d27;--border:#2a2d3a;--accent:#6366f1;--green:#10b981;--red:#ef4444;--yellow:#f59e0b;--text:#e2e8f0;--muted:#64748b}
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;min-height:100vh;padding:2rem}
  h1{font-size:1.75rem;font-weight:700;margin-bottom:.25rem}
  h1 span{color:var(--accent)}
  .sub{color:var(--muted);font-size:.9rem;margin-bottom:2rem}
  .card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:1.5rem;margin-bottom:1.5rem}
  .row{display:flex;gap:1rem;align-items:center}
  input[type=url]{flex:1;background:#0f1117;border:1px solid var(--border);border-radius:8px;color:var(--text);font-size:1rem;padding:.75rem 1rem;outline:none;transition:border-color .2s}
  input[type=url]:focus{border-color:var(--accent)}
  button{background:var(--accent);border:none;border-radius:8px;color:#fff;cursor:pointer;font-size:1rem;font-weight:600;padding:.75rem 1.5rem;transition:opacity .2s}
  button:hover{opacity:.85}
  button:disabled{opacity:.4;cursor:not-allowed}
  .grade{font-size:3rem;font-weight:800;line-height:1}
  .grade.A,.grade.B{color:var(--green)} .grade.C{color:var(--yellow)} .grade.D,.grade.F{color:var(--red)}
  .score-row{display:grid;grid-template-columns:1fr auto auto;gap:.5rem;align-items:center;padding:.4rem 0;border-bottom:1px solid var(--border)}
  .score-row:last-child{border:none}
  .bar-wrap{height:8px;background:#2a2d3a;border-radius:4px;overflow:hidden;width:140px}
  .bar-fill{height:100%;border-radius:4px;transition:width .6s ease}
  .issue{padding:.6rem 0;border-bottom:1px solid var(--border);display:grid;grid-template-columns:auto 1fr auto auto;gap:.75rem;align-items:start}
  .issue:last-child{border:none}
  .sev{font-size:.7rem;font-weight:700;padding:.2rem .5rem;border-radius:4px;text-transform:uppercase}
  .sev.CRITICAL{background:#7f1d1d;color:#fca5a5} .sev.HIGH{background:#7c2d12;color:#fdba74}
  .sev.MEDIUM{background:#713f12;color:#fde68a} .sev.LOW,.sev.INFO{background:#1e293b;color:var(--muted)}
  .tag{font-size:.75rem;padding:.2rem .5rem;border-radius:4px}
  .tag.auto{background:#064e3b;color:#6ee7b7} .tag.theme{background:#1e3a5f;color:#93c5fd} .tag.manual{background:#1e293b;color:var(--muted)}
  .ai-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:1rem}
  .ai-item label{font-size:.75rem;color:var(--muted);display:block;margin-bottom:.25rem}
  .ai-item .val{font-weight:600;font-size:.95rem}
  .ok{color:var(--green)} .bad{color:var(--red)} .warn{color:var(--yellow)}
  .spinner{display:inline-block;width:16px;height:16px;border:2px solid var(--border);border-top-color:var(--accent);border-radius:50%;animation:spin .7s linear infinite}
  @keyframes spin{to{transform:rotate(360deg)}}
  .hidden{display:none}
  pre{background:#0f1117;border:1px solid var(--border);border-radius:8px;padding:1rem;font-size:.8rem;overflow:auto;max-height:300px}
</style>
</head>
<body>
<h1>🤖 <span>Shopify Audit</span> Tools</h1>
<p class="sub">AI Visibility Audit for ecommerce — CLI + Web UI</p>

<div class="card">
  <div class="row">
    <input type="url" id="urlInput" placeholder="https://store.com/products/your-product" />
    <label><input type="checkbox" id="skipLH"> Skip Lighthouse</label>
    <button id="runBtn" onclick="startAudit()">▶ Audit</button>
  </div>
</div>

<div id="loading" class="card hidden">
  <div class="row"><div class="spinner"></div><span id="loadMsg" style="margin-left:.75rem;color:var(--muted)">Running audit…</span></div>
</div>

<div id="results" class="hidden">
  <div class="card" style="display:flex;gap:2rem;align-items:center">
    <div>
      <div class="grade" id="grade"></div>
      <div style="color:var(--muted);font-size:.85rem;margin-top:.25rem">Grade</div>
    </div>
    <div style="flex:1">
      <div style="font-size:2rem;font-weight:700" id="totalScore"></div>
      <div style="color:var(--muted);font-size:.85rem">/ 100 Agentic Score</div>
      <div style="color:var(--muted);font-size:.8rem;margin-top:.25rem" id="summaryText"></div>
    </div>
    <div style="text-align:right;color:var(--muted);font-size:.8rem">
      <div id="platformBadge"></div>
      <div id="durationBadge"></div>
    </div>
  </div>

  <div class="card">
    <h3 style="margin-bottom:1rem;color:var(--muted);font-size:.85rem;text-transform:uppercase;letter-spacing:.05em">Score Breakdown</h3>
    <div id="scoreBreakdown"></div>
  </div>

  <div class="card">
    <h3 style="margin-bottom:1rem;color:var(--muted);font-size:.85rem;text-transform:uppercase;letter-spacing:.05em">🤖 AI Access Status</h3>
    <div class="ai-grid" id="aiGrid"></div>
  </div>

  <div class="card">
    <h3 style="margin-bottom:1rem;color:var(--muted);font-size:.85rem;text-transform:uppercase;letter-spacing:.05em">Issues</h3>
    <div id="issuesList"></div>
  </div>
</div>

<script>
let pollTimer = null;

async function startAudit() {
  const url = document.getElementById('urlInput').value.trim();
  if (!url) return;
  const skipLH = document.getElementById('skipLH').checked;
  const btn = document.getElementById('runBtn');
  btn.disabled = true;
  document.getElementById('loading').classList.remove('hidden');
  document.getElementById('results').classList.add('hidden');
  document.getElementById('loadMsg').textContent = 'Queuing audit…';

  const resp = await fetch('/audit', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({url, skip_lighthouse: skipLH})
  });
  const {job_id} = await resp.json();
  document.getElementById('loadMsg').textContent = `Running (job: ${job_id})…`;
  poll(job_id, btn);
}

function poll(jobId, btn) {
  pollTimer = setInterval(async () => {
    const r = await fetch(`/audit/${jobId}`).then(r => r.json());
    if (r.status === 'done') {
      clearInterval(pollTimer);
      document.getElementById('loading').classList.add('hidden');
      btn.disabled = false;
      renderResult(r.result);
    } else if (r.status === 'error') {
      clearInterval(pollTimer);
      document.getElementById('loading').classList.add('hidden');
      document.getElementById('loadMsg').textContent = 'Error: ' + r.error;
      btn.disabled = false;
    }
  }, 2000);
}

function renderResult(r) {
  document.getElementById('results').classList.remove('hidden');
  const s = r.score;
  const grade = s.grade;
  const el = document.getElementById('grade');
  el.textContent = grade;
  el.className = 'grade ' + grade;
  document.getElementById('totalScore').textContent = s.total;
  document.getElementById('summaryText').textContent = r.summary;
  document.getElementById('platformBadge').textContent = `Platform: ${r.platform.name} (${r.platform.confidence}%)`;
  document.getElementById('durationBadge').textContent = `Duration: ${r.duration_ms}ms`;

  const dims = [
    ['⚡ Core Web Vitals', s.cwv, 25],
    ['🤖 AI Discoverability', s.discover, 35],
    ['📋 Structured Data', s.schema, 20],
    ['✍️ Content Quality', s.content, 15],
    ['📜 Policy', s.policy, 5],
  ];
  const colors = (pct) => pct >= 80 ? '#10b981' : pct >= 50 ? '#f59e0b' : '#ef4444';
  document.getElementById('scoreBreakdown').innerHTML = dims.map(([l, v, mx]) => {
    const pct = Math.round(v / mx * 100);
    return `<div class="score-row">
      <span>${l}</span>
      <span style="color:var(--muted);font-size:.85rem">${v}/${mx}</span>
      <div class="bar-wrap"><div class="bar-fill" style="width:${pct}%;background:${colors(pct)}"></div></div>
    </div>`;
  }).join('') + (s.bonus ? `<div class="score-row"><span>🎁 Bonus</span><span style="color:#10b981">+${s.bonus}</span><div class="bar-wrap"></div></div>` : '');

  const ai = r.ai_access;
  const aiItems = [
    ['GPTBot (ChatGPT)', ai.gptbot_allowed],
    ['Google-Extended', ai.google_extended_allowed],
    ['PerplexityBot', ai.perplexitybot_allowed],
    ['Storefront MCP', ai.mcp_detected],
    ['Product Schema', ai.has_product_schema],
    ['GTIN', ai.gtin ? ai.gtin : ai.gtin === null ? null : false],
    ['Brand', ai.brand ? ai.brand : ai.brand === null ? null : false],
  ];
  document.getElementById('aiGrid').innerHTML = aiItems.map(([l, v]) => {
    const cls = v === true || (typeof v === 'string' && v) ? 'ok' : v === false ? 'bad' : 'warn';
    const label = v === true ? '✅ Yes' : v === false ? '🔴 No' : (typeof v === 'string' && v) ? '✅ ' + v : '❓ Unknown';
    return `<div class="ai-item"><label>${l}</label><div class="val ${cls}">${label}</div></div>`;
  }).join('');

  const sevIcon = {CRITICAL:'🔴',HIGH:'🟠',MEDIUM:'🟡',LOW:'⚪',INFO:'ℹ️'};
  const fixTag = {auto:'auto',theme:'theme',manual:'manual'};
  document.getElementById('issuesList').innerHTML = (r.issues || []).slice(0, 15).map(i =>
    `<div class="issue">
      <span class="sev ${i.severity}">${i.severity}</span>
      <div>
        <div style="font-size:.9rem;font-weight:500">${i.title}</div>
        <div style="font-size:.8rem;color:var(--muted);margin-top:.2rem">${i.recommendation.slice(0,100)}</div>
      </div>
      <span style="color:#ef4444;font-size:.85rem">-${i.points_lost}pts</span>
      <span class="tag ${fixTag[i.fix_type] || 'manual'}">${i.fix_type}</span>
    </div>`
  ).join('') || '<div style="color:var(--muted)">No issues found 🎉</div>';
}
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def dashboard():
    return HTML


# ── CLI entrypoint for `audit-web` ────────────────────────────────

web_app = typer.Typer(name="audit-web", add_completion=False)


@web_app.command()
def main(
    host: str = "127.0.0.1",
    port: int = 8080,
    reload: bool = False,
) -> None:
    """Start the web API server."""
    uvicorn.run("audit.server:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    main()
