"""
Async Orchestrator — runs all 3 tiers in parallel and assembles AuditResult.
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional

from audit.core.models import AuditResult, CWV
from audit.core.scorer import calculate_score
from audit.platform.detector import detect_platform, is_auditable_url
from audit.tiers.tier0_scrapling import run_tier0
from audit.tiers.tier1_browser import run_tier1
from audit.tiers.tier2_lighthouse import LighthouseResult, run_tier2


async def run_audit(
    url: str,
    device: str = "mobile",
    skip_lighthouse: bool = False,
    skip_browser: bool = False,
    # Product data (when available via API e.g. Shopify)
    has_gtin: bool = False,
    has_brand: bool = False,
    description_words: int = 0,
    has_return_policy: bool = False,
) -> AuditResult:
    """
    Full 3-tier parallel audit.
    Gracefully degrades when individual tiers fail.
    """
    # ── Validate URL ──────────────────────────────────────────────
    ok, reason = is_auditable_url(url)
    if not ok:
        from audit.core.models import Score, PlatformResult, AIAccessStatus, Issue
        return AuditResult(
            url=url,
            platform=PlatformResult(platform="unknown", confidence=0),
            score=Score(cwv=0, discover=0, schema=0, content=0, policy=0),
            cwv=CWV(),
            ai_access=AIAccessStatus(),
            issues=[Issue(
                type="INVALID_URL", severity="CRITICAL", category="DISCOVERABILITY",
                title=f"URL not auditable: {reason}",
                description="This URL cannot be audited.",
                recommendation="Provide a valid storefront product URL.",
                points_lost=100, fixable=False, fix_type="manual",
            )],
            bot_blocked=False,
            duration_ms=0,
            summary=f"Error: {reason}",
        )

    t0 = time.perf_counter()

    # ── Run all tiers in parallel ─────────────────────────────────
    tier0_task = asyncio.create_task(run_tier0(url))
    tier1_task = asyncio.create_task(run_tier1(url) if not skip_browser else _noop_browser())
    tier2_task = asyncio.create_task(run_tier2(url, device) if not skip_lighthouse else _noop_lighthouse())
    platform_task = asyncio.create_task(detect_platform(url))

    # Gather with independent error handling
    results = await asyncio.gather(
        tier0_task, tier1_task, tier2_task, platform_task,
        return_exceptions=True,
    )

    errors: list[str] = []
    from audit.tiers.tier0_scrapling import run_tier0 as _  # noqa: F401
    from audit.core.models import AIAccessStatus

    ai_status, t0_errors = (results[0] if not isinstance(results[0], Exception)
                             else (AIAccessStatus(), [str(results[0])]))
    if t0_errors:
        errors.extend(t0_errors)

    from audit.tiers.tier1_browser import BrowserResult
    browser = results[1] if not isinstance(results[1], Exception) else BrowserResult(errors=[str(results[1])])
    if isinstance(results[1], Exception):
        errors.append(f"tier1: {results[1]}")

    lh = results[2] if not isinstance(results[2], Exception) else LighthouseResult(errors=[str(results[2])])
    if isinstance(results[2], Exception):
        errors.append(f"tier2: {results[2]}")

    from audit.core.models import PlatformResult
    platform = results[3] if not isinstance(results[3], Exception) else PlatformResult(platform="unknown", confidence=0)
    if isinstance(results[3], Exception):
        errors.append(f"platform: {results[3]}")

    # ── Score calculation ─────────────────────────────────────────
    score, issues, bot_blocked = calculate_score(
        lh, browser, ai_status,
        has_gtin=has_gtin,
        has_brand=has_brand,
        description_words=description_words,
        has_return_policy=has_return_policy,
    )

    duration_ms = round((time.perf_counter() - t0) * 1000)
    bonus_info = f"robots +{ai_status.robots_bonus}pts | MCP: {'✓' if ai_status.mcp_detected else '—'} | schema: {'✓' if ai_status.has_product_schema else '—'}"

    return AuditResult(
        url=url,
        platform=platform,
        score=score,
        cwv=lh.cwv,
        ai_access=ai_status,
        issues=issues,
        bot_blocked=bot_blocked,
        duration_ms=duration_ms,
        summary=f"Score: {score.total}/100 Grade {score.grade} | {bonus_info}",
        errors=errors,
        lh_performance=lh.performance,
        lh_seo=lh.seo,
        lh_accessibility=lh.accessibility,
        lh_best_practices=lh.best_practices,
    )


# ── Fallback stubs ────────────────────────────────────────────────

async def _noop_browser():
    from audit.tiers.tier1_browser import BrowserResult
    return BrowserResult(available=False, errors=["skipped"])


async def _noop_lighthouse():
    return LighthouseResult(available=False, errors=["skipped"])
