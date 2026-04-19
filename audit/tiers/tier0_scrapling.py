"""
Tier 0 — Scrapling: bot-check, robots.txt, JSON-LD schema, Storefront MCP.
Runs as GPTBot + Google-Extended to simulate real AI agent access.
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Optional
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from audit.core.models import AIAccessStatus

# ── AI Bot user-agents ────────────────────────────────────────────
AI_BOTS: dict[str, str] = {
    "gptbot": "GPTBot/1.2",
    "google-extended": "Google-Extended/1.0",
    "perplexitybot": "PerplexityBot/1.0",
    "claudebot": "ClaudeBot/1.0",
    "bingbot": "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)",
}

TIMEOUT = httpx.Timeout(15.0)


async def run_tier0(url: str) -> tuple[AIAccessStatus, list[str]]:
    """
    Run Tier 0: simultaneous checks for bot access, robots.txt, schema, MCP.
    Returns (AIAccessStatus, errors[]).
    """
    errors: list[str] = []
    parsed = urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"

    # Run all checks in parallel
    results = await asyncio.gather(
        _check_robots_txt(base_url),
        _check_bot_access(url),
        _extract_schema(url),
        _check_storefront_mcp(base_url),
        return_exceptions=True,
    )

    robots_result   = results[0] if not isinstance(results[0], Exception) else {}
    bot_result      = results[1] if not isinstance(results[1], Exception) else {}
    schema_result   = results[2] if not isinstance(results[2], Exception) else {}
    mcp_result      = results[3] if not isinstance(results[3], Exception) else {}

    for i, r in enumerate(results):
        if isinstance(r, Exception):
            errors.append(f"tier0_check_{i}: {r}")

    # ── Calculate robots bonus ────────────────────────────────────
    bonus = 0
    if robots_result.get("gptbot_allowed"):     bonus += 2
    if robots_result.get("google_extended"):    bonus += 2
    if robots_result.get("perplexitybot"):      bonus += 1
    if robots_result.get("bingbot"):            bonus += 1
    if robots_result.get("claudebot"):          bonus += 1

    # ── Schema bonus ──────────────────────────────────────────────
    if schema_result.get("has_product_schema"): bonus += 3
    if schema_result.get("gtin"):               bonus += 3
    if schema_result.get("brand"):              bonus += 2

    # ── MCP bonus ─────────────────────────────────────────────────
    if mcp_result.get("detected"):              bonus += 5

    status = AIAccessStatus(
        gptbot_allowed=robots_result.get("gptbot_allowed"),
        google_extended_allowed=robots_result.get("google_extended"),
        perplexitybot_allowed=robots_result.get("perplexitybot"),
        mcp_detected=mcp_result.get("detected", False),
        mcp_endpoint=mcp_result.get("endpoint"),
        has_product_schema=schema_result.get("has_product_schema", False),
        gtin_in_schema=schema_result.get("gtin"),
        brand_in_schema=schema_result.get("brand"),
        price_in_schema=schema_result.get("price"),
        robots_bonus=bonus,
    )
    return status, errors


# ── robots.txt parser ─────────────────────────────────────────────

async def _check_robots_txt(base_url: str) -> dict:
    robots_url = f"{base_url}/robots.txt"
    result = {
        "gptbot_allowed": None,
        "google_extended": None,
        "perplexitybot": None,
        "claudebot": None,
        "bingbot": None,
    }
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(robots_url, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code != 200:
            return result
        content = resp.text

        for bot_key, ua in AI_BOTS.items():
            rp = RobotFileParser()
            rp.parse(content.splitlines())
            # Test against a typical product URL path
            allowed = rp.can_fetch(ua.split("/")[0], "/products/test")
            result_key = bot_key.replace("-", "_")
            result[result_key] = allowed

    except Exception as e:
        raise RuntimeError(f"robots.txt fetch failed: {e}") from e

    return result


# ── Bot access check (HTTP status as AI UA) ───────────────────────

async def _check_bot_access(url: str) -> dict:
    result = {"gptbot_blocked": False, "google_extended_blocked": False}
    checks = [
        ("gptbot_blocked",          AI_BOTS["gptbot"]),
        ("google_extended_blocked", AI_BOTS["google-extended"]),
    ]
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        for key, ua in checks:
            try:
                resp = await client.get(url, headers={"User-Agent": ua})
                blocked = resp.status_code in (403, 429, 503) or \
                          any(p in resp.text.lower() for p in [
                              "access denied", "checking your browser",
                              "ddos protection", "you are unable to access"
                          ])
                result[key] = blocked
            except Exception:
                pass
    return result


# ── JSON-LD schema extractor ──────────────────────────────────────

async def _extract_schema(url: str) -> dict:
    result = {
        "has_product_schema": False,
        "gtin": None,
        "brand": None,
        "price": None,
        "currency": None,
        "mpn": None,
    }
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1)"},
            )
        soup = BeautifulSoup(resp.text, "lxml")

        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                schemas = data if isinstance(data, list) else [data]
                for schema in schemas:
                    stype = schema.get("@type", "")
                    if stype not in ("Product", "https://schema.org/Product"):
                        # Check nested @graph
                        for item in schema.get("@graph", []):
                            if item.get("@type") == "Product":
                                schema = item
                                stype = "Product"
                                break
                    if stype in ("Product", "https://schema.org/Product"):
                        result["has_product_schema"] = True
                        # GTIN variants
                        for gtin_key in ("gtin", "gtin13", "gtin12", "gtin8", "gtin14"):
                            if schema.get(gtin_key):
                                result["gtin"] = str(schema[gtin_key])
                                break
                        # Brand
                        brand = schema.get("brand")
                        if isinstance(brand, dict):
                            result["brand"] = brand.get("name")
                        elif isinstance(brand, str):
                            result["brand"] = brand
                        # Price from offers
                        offers = schema.get("offers", {})
                        if isinstance(offers, list) and offers:
                            offers = offers[0]
                        if isinstance(offers, dict):
                            result["price"] = str(offers.get("price", ""))
                            result["currency"] = offers.get("priceCurrency")
                        result["mpn"] = schema.get("mpn")
                        break
            except (json.JSONDecodeError, AttributeError):
                continue

    except Exception as e:
        raise RuntimeError(f"Schema extraction failed: {e}") from e

    return result


# ── Storefront MCP detector ───────────────────────────────────────

async def _check_storefront_mcp(base_url: str) -> dict:
    """Check for Shopify Storefront MCP endpoint."""
    candidates = [
        f"{base_url}/.well-known/shopify-mcp.json",
        f"{base_url}/api/mcp",
        f"{base_url}/.well-known/mcp.json",
    ]
    async with httpx.AsyncClient(timeout=httpx.Timeout(5.0), follow_redirects=False) as client:
        for candidate in candidates:
            try:
                resp = await client.get(candidate)
                if resp.status_code == 200:
                    return {"detected": True, "endpoint": candidate}
            except Exception:
                continue
    return {"detected": False, "endpoint": None}
