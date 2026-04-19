"""
Score Calculator — port of score-calculator.server.ts.
All weights and thresholds match the Shopify app exactly.
"""
from __future__ import annotations

from audit.core.models import CWV, AIAccessStatus, Issue, Score
from audit.tiers.tier1_browser import BrowserResult
from audit.tiers.tier2_lighthouse import LighthouseResult

# ── Weights (must sum to 100) ─────────────────────────────────────
W_CWV      = 25
W_DISCOVER = 35
W_SCHEMA   = 20
W_CONTENT  = 15
W_POLICY   = 5

# ── CWV thresholds ────────────────────────────────────────────────
CWV_T = {
    "lcp":  (2500, 4000),   # ms  good / poor
    "cls":  (0.10, 0.25),
    "inp":  (200,  500),    # ms
    "fcp":  (1800, 3000),   # ms
    "tbt":  (200,  600),    # ms
}

BOT_KEYWORDS = [
    "sorry, you have been blocked", "you are unable to access",
    "access denied", "checking your browser", "ddos protection",
    "please complete the security check", "captcha", "403 forbidden",
]


def _pts(value: float, good: float, poor: float) -> float:
    """Linear interpolation between good and poor thresholds → 0.0–1.0."""
    if value <= good: return 1.0
    if value >= poor: return 0.0
    return 1.0 - (value - good) / (poor - good)


def calculate_score(
    lh: LighthouseResult,
    browser: BrowserResult,
    ai: AIAccessStatus,
    *,
    has_gtin: bool = False,
    has_brand: bool = False,
    description_words: int = 0,
    has_return_policy: bool = False,
) -> tuple[Score, list[Issue], bool]:
    """
    Returns (Score, issues_sorted_by_points_lost, bot_blocked).
    Mirrors the TypeScript calculateAgenticScore() exactly.
    """
    issues: list[Issue] = []
    snap = browser.snapshot_text.lower()

    # ── Bot-block check ──────────────────────────────────────────
    bot_blocked = any(kw in snap for kw in BOT_KEYWORDS) or ai.bot_blocked or (
        browser.snapshot_chars < 500 and browser.h1_count == 0 and browser.image_count == 0
    )
    if bot_blocked:
        issues.append(Issue(
            type="BOT_BLOCKED", severity="CRITICAL", category="DISCOVERABILITY",
            title="Page blocked — AI agents see zero content",
            description="AI shopping agents are treated as bots. Products invisible regardless of SEO score.",
            recommendation="Allow GPTBot, Google-Extended, PerplexityBot in robots.txt and WAF/Cloudflare.",
            points_lost=100, fixable=False, fix_type="manual",
        ))
        return Score(cwv=0, discover=0, schema=0, content=0, policy=0), issues, True

    # ── 1. CWV (0-25) ───────────────────────────────────────────
    cwv = lh.cwv
    cwv_raw = W_CWV * (
        _pts(cwv.lcp or 0,  *CWV_T["lcp"]) * 0.40 +
        _pts(cwv.cls or 0,  *CWV_T["cls"]) * 0.25 +
        _pts(cwv.inp or 200,*CWV_T["inp"]) * 0.20 +
        _pts(cwv.fcp or 0,  *CWV_T["fcp"]) * 0.10 +
        _pts(cwv.tbt or 0,  *CWV_T["tbt"]) * 0.05
    )
    cwv_score = round(cwv_raw)

    if cwv.lcp and cwv.lcp > CWV_T["lcp"][0]:
        pl = round(W_CWV * (1 - _pts(cwv.lcp, *CWV_T["lcp"])) * 0.40)
        issues.append(Issue(
            type="SLOW_LCP", severity="CRITICAL" if cwv.lcp > CWV_T["lcp"][1] else "HIGH",
            category="CWV",
            title=f"LCP is {cwv.lcp/1000:.2f}s (target: <2.5s)",
            description="Slow LCP hurts Google ranking and causes AI agents to timeout.",
            recommendation="Preload hero image, use WebP, reduce server response time, defer non-critical JS.",
            points_lost=pl, fixable=False, fix_type="theme",
        ))

    if cwv.cls and cwv.cls > CWV_T["cls"][0]:
        pl = round(W_CWV * (1 - _pts(cwv.cls, *CWV_T["cls"])) * 0.25)
        issues.append(Issue(
            type="HIGH_CLS", severity="CRITICAL" if cwv.cls > CWV_T["cls"][1] else "HIGH",
            category="CWV",
            title=f"CLS is {cwv.cls:.3f} (target: <0.1)",
            description="Layout shifts confuse AI agents parsing page structure.",
            recommendation="Set explicit width/height on all images. Avoid injecting content above fold after load.",
            points_lost=pl, fixable=False, fix_type="theme",
        ))

    if cwv.tbt and cwv.tbt > CWV_T["tbt"][0]:
        pl = round(W_CWV * (1 - _pts(cwv.tbt, *CWV_T["tbt"])) * 0.05)
        issues.append(Issue(
            type="HIGH_TBT", severity="MEDIUM", category="CWV",
            title=f"Total Blocking Time {cwv.tbt:.0f}ms (target: <200ms)",
            description="Main thread blocked — AI agents may timeout before completing page parse.",
            recommendation="Remove unused apps/plugins, split JS bundles, defer non-critical scripts.",
            points_lost=pl, fixable=False, fix_type="theme",
        ))

    # ── 2. Discoverability (0-35) ────────────────────────────────
    discover = W_DISCOVER

    if browser.h1_count == 0:
        discover -= 8
        issues.append(Issue(
            type="MISSING_H1", severity="CRITICAL", category="DISCOVERABILITY",
            title="No H1 in accessibility tree — product name invisible to AI agents",
            description="AI agents read the a11y tree to find product names. H1 missing = AI doesn't know what the product is.",
            recommendation="Ensure product title renders as H1 in DOM, not hidden via CSS/JS.",
            points_lost=8, fixable=False, fix_type="theme",
        ))

    if browser.image_count == 0:
        discover -= 5
        issues.append(Issue(
            type="IMAGES_INVISIBLE", severity="HIGH", category="DISCOVERABILITY",
            title="Product images not found in accessibility tree",
            description="Images rendered only via JS without ARIA roles are invisible to AI vision.",
            recommendation="Add alt attributes to all product images. Check images load in initial HTML.",
            points_lost=5, fixable=False, fix_type="theme",
        ))
    elif browser.missing_alt_count > 0:
        pl = min(5, browser.missing_alt_count * 2)
        discover -= pl
        issues.append(Issue(
            type="MISSING_ALT_TEXT",
            severity="HIGH" if browser.missing_alt_count >= 3 else "MEDIUM",
            category="DISCOVERABILITY",
            title=f"{browser.missing_alt_count}/{browser.image_count} images missing alt text",
            description="Alt text tells AI what products look like. Missing alt reduces AI match confidence.",
            recommendation="Add descriptive alt text: product name, color, key features.",
            points_lost=pl, fixable=True, fix_type="auto",
        ))

    if not browser.has_price and browser.h1_count > 0:
        discover -= 4
        issues.append(Issue(
            type="PRICE_NOT_VISIBLE", severity="HIGH", category="DISCOVERABILITY",
            title="Product price not visible in accessibility tree",
            description="AI shopping agents need to see price for comparison and recommendation.",
            recommendation="Render price server-side with ARIA label (aria-label='Price: $X').",
            points_lost=4, fixable=False, fix_type="theme",
        ))

    if not has_gtin and not ai.gtin_in_schema:
        discover -= 10
        issues.append(Issue(
            type="NO_GTIN", severity="CRITICAL", category="DISCOVERABILITY",
            title="No GTIN/Barcode — product unidentifiable by AI shopping agents",
            description="ChatGPT Shopping, Google AI Overviews, Bing Copilot use GTIN to match products globally.",
            recommendation="Add UPC/EAN barcode to product variants. Contact supplier for barcode if needed.",
            points_lost=10, fixable=False, fix_type="manual",
        ))

    if not has_brand and not ai.brand_in_schema:
        discover -= 5
        issues.append(Issue(
            type="MISSING_BRAND", severity="HIGH", category="DISCOVERABILITY",
            title="Brand/Vendor field is empty",
            description="AI shopping agents group products by brand. Empty vendor reduces product authority.",
            recommendation="Set the Vendor/Brand field in product admin.",
            points_lost=5, fixable=True, fix_type="auto",
        ))

    if browser.has_js_hidden_content:
        discover -= 6
        issues.append(Issue(
            type="JS_HIDDEN_CONTENT", severity="HIGH", category="DISCOVERABILITY",
            title="Product description hidden by JavaScript",
            description="AI crawlers cannot read JS-rendered content in tabs/accordions.",
            recommendation="Render product description in initial HTML.",
            points_lost=6, fixable=False, fix_type="theme",
        ))

    discover = max(0, discover)

    # ── 3. Schema / SEO (0-20) ────────────────────────────────────
    schema = round(W_SCHEMA * (lh.seo / 100))

    if not browser.has_breadcrumb:
        schema = max(0, schema - 2)
        issues.append(Issue(
            type="NO_BREADCRUMB", severity="MEDIUM", category="SCHEMA",
            title="No breadcrumb navigation found",
            description="Breadcrumb structured data helps AI understand product category context.",
            recommendation="Add BreadcrumbList schema. Ensure breadcrumb renders in HTML.",
            points_lost=2, fixable=True, fix_type="auto",
        ))

    if not ai.has_product_schema:
        issues.append(Issue(
            type="NO_PRODUCT_SCHEMA", severity="HIGH", category="SCHEMA",
            title="No Product JSON-LD schema detected",
            description="AI agents rely on Product schema to understand price, brand, availability without JS.",
            recommendation="Inject Product JSON-LD via Script Tags API.",
            points_lost=8, fixable=True, fix_type="auto",
        ))

    # ── 4. Content / Accessibility (0-15) ────────────────────────
    content = max(0, round(W_CONTENT * (lh.accessibility / 100)))

    if lh.accessibility < 90:
        issues.append(Issue(
            type="LOW_ACCESSIBILITY",
            severity="HIGH" if lh.accessibility < 70 else "MEDIUM",
            category="CONTENT",
            title=f"Accessibility {lh.accessibility}/100 — AI comprehension impaired",
            description="Low accessibility directly reduces AI agent comprehension.",
            recommendation="Fix color contrast, add form labels, correct heading hierarchy, add ARIA landmarks.",
            points_lost=W_CONTENT - content, fixable=False, fix_type="theme",
        ))

    if description_words == 0:
        issues.append(Issue(
            type="NO_DESCRIPTION", severity="CRITICAL", category="CONTENT",
            title="No product description",
            description="AI agents have zero context to recommend this product.",
            recommendation="Write a detailed description: specifications, use cases, features.",
            points_lost=5, fixable=True, fix_type="auto",
        ))
    elif description_words < 100:
        issues.append(Issue(
            type="THIN_DESCRIPTION", severity="HIGH", category="CONTENT",
            title=f"Description too short ({description_words} words — target: 150+)",
            description="Short descriptions reduce AI match confidence.",
            recommendation="Expand with specifications, materials, use cases, dimensions, compatibility.",
            points_lost=4, fixable=True, fix_type="auto",
        ))

    # ── 5. Policy (0-5) ───────────────────────────────────────────
    policy = W_POLICY if has_return_policy else 2
    if not has_return_policy:
        issues.append(Issue(
            type="NO_RETURN_POLICY", severity="LOW", category="POLICY",
            title="No machine-readable return policy",
            description="AI shopping assistants check return policies to build recommendation confidence.",
            recommendation="Add return policy via structured data or metafield.",
            points_lost=W_POLICY - policy, fixable=True, fix_type="auto",
        ))

    # ── Scrapling bonus (robots + MCP + schema) ───────────────────
    bonus = ai.robots_bonus  # already calculated in tier0

    # Add robots.txt issues
    for bot_name, allowed in [
        ("GPTBot (ChatGPT)",           ai.gptbot_allowed),
        ("Google-Extended (Gemini)",   ai.google_extended_allowed),
        ("PerplexityBot",              ai.perplexitybot_allowed),
    ]:
        if allowed is False:
            issues.append(Issue(
                type=f"ROBOTS_BLOCKED_{bot_name.split()[0].upper()}",
                severity="CRITICAL", category="DISCOVERABILITY",
                title=f"{bot_name} blocked in robots.txt",
                description=f"robots.txt has Disallow rules for {bot_name}. AI agents cannot index products.",
                recommendation=f"Add to robots.txt:\nUser-agent: {bot_name.split()[0]}\nAllow: /",
                points_lost=10 if "GPTBot" in bot_name else 8,
                fixable=False, fix_type="manual",
            ))

    if not ai.mcp_detected:
        issues.append(Issue(
            type="NO_STOREFRONT_MCP", severity="MEDIUM", category="DISCOVERABILITY",
            title="Storefront MCP not detected",
            description="Storefront MCP allows AI agents to query products directly without HTML crawling.",
            recommendation="Install Shopify Storefront MCP integration from the App Store.",
            points_lost=0, fixable=False, fix_type="manual",
        ))

    issues_sorted = sorted(issues, key=lambda i: -i.points_lost)
    score = Score(
        cwv=cwv_score, discover=discover,
        schema=schema, content=content,
        policy=policy, bonus=bonus,
    )
    return score, issues_sorted, False
