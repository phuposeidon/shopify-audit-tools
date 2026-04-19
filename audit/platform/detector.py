"""
Platform Detector — fingerprint-based detection for Shopify, Magento 2,
WooCommerce, WordPress. Port of detector.server.ts.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

import httpx

from audit.core.models import Platform, PlatformResult

TIMEOUT = httpx.Timeout(10.0)

BLOCKED_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"admin\.shopify\.com"),  "Shopify Admin — not a storefront URL"),
    (re.compile(r"/wp-admin/"),           "WordPress admin panel"),
    (re.compile(r"/account/"),           "User account page"),
    (re.compile(r"/checkout/"),          "Checkout page"),
    (re.compile(r"/cart/"),              "Cart page"),
    (re.compile(r"localhost"),           "Localhost URL"),
]


def is_auditable_url(url: str) -> tuple[bool, Optional[str]]:
    if not url.startswith(("http://", "https://")):
        return False, "URL must use http or https"
    for pattern, reason in BLOCKED_PATTERNS:
        if pattern.search(url):
            return False, reason
    return True, None


@dataclass
class _Ctx:
    url: str
    html: str
    headers: dict[str, str]
    pathname: str


# (signal_name, weight, platform, test_fn)
_RULES: list[tuple[str, int, Platform]] = [
    ("cdn.shopify.com asset",           45, "shopify"),
    ("window.Shopify global",           40, "shopify"),
    ("shopify-checkout-api-token meta", 35, "shopify"),
    ("X-Shopify-Stage header",          50, "shopify"),
    ("myshopify.com domain",            50, "shopify"),
    ("/products/ URL path",             20, "shopify"),
    ("Powered by Shopify",             30, "shopify"),
    ("X-Magento-Cache-Id header",       50, "magento2"),
    ("X-Magento-Tags header",           45, "magento2"),
    ("/static/version asset path",      40, "magento2"),
    ("Mage. / require.config",          35, "magento2"),
    ("form_key Magento field",          25, "magento2"),
    ("Hyva theme",                      35, "magento2"),
    (".html + Magento signals",         15, "magento2"),
    ("wp-content/plugins/woocommerce",  45, "woocommerce"),
    ("woocommerce_params global",       40, "woocommerce"),
    ("wc-add-to-cart nonce",           35, "woocommerce"),
    ("X-WC-Store-API-Nonce header",     50, "woocommerce"),
    ("wp-content/themes (non-WC)",      30, "wordpress"),
    ("meta generator=WordPress",        25, "wordpress"),
    ("wp-json REST link",              20, "wordpress"),
]


def _check(signal: str, ctx: _Ctx) -> bool:
    h, html, url = ctx.headers, ctx.html, ctx.url
    match signal:
        case "cdn.shopify.com asset":           return "cdn.shopify.com" in html
        case "window.Shopify global":           return "window.Shopify" in html
        case "shopify-checkout-api-token meta": return "shopify-checkout-api-token" in html
        case "X-Shopify-Stage header":          return "x-shopify-stage" in h
        case "myshopify.com domain":            return "myshopify.com" in url
        case "/products/ URL path":             return "/products/" in ctx.pathname
        case "Powered by Shopify":             return "Powered by Shopify" in html
        case "X-Magento-Cache-Id header":       return "x-magento-cache-id" in h
        case "X-Magento-Tags header":           return "x-magento-tags" in h
        case "/static/version asset path":      return bool(re.search(r"/static/version\d+/frontend/", html))
        case "Mage. / require.config":          return "Mage." in html or "require.config" in html
        case "form_key Magento field":          return 'name="form_key"' in html
        case "Hyva theme":                      return "hyva" in html.lower()
        case ".html + Magento signals":
            return (ctx.pathname.endswith(".html")
                    and "wp-content" not in html
                    and "cdn.shopify.com" not in html
                    and any(s in html for s in ["Magento", "/static/version", "Mage."]))
        case "wp-content/plugins/woocommerce":  return "wp-content/plugins/woocommerce" in html
        case "woocommerce_params global":       return "woocommerce_params" in html
        case "wc-add-to-cart nonce":           return "wc-add-to-cart" in html
        case "X-WC-Store-API-Nonce header":     return "x-wc-store-api-nonce" in h
        case "wp-content/themes (non-WC)":
            return "wp-content/themes" in html and "woocommerce" not in html.lower()
        case "meta generator=WordPress":        return bool(re.search(r"generator.*WordPress", html, re.I))
        case "wp-json REST link":              return "wp-json" in html and "woocommerce" not in html.lower()
        case _: return False


_MAX_SCORE: dict[str, int] = {
    "shopify": 220, "magento2": 250, "woocommerce": 170, "wordpress": 75, "unknown": 1,
}


async def detect_platform(url: str) -> PlatformResult:
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
        html    = resp.text[:80_000]
        headers = {k.lower(): v for k, v in resp.headers.items()}
        path    = urlparse(url).path
    except Exception as exc:
        return PlatformResult(platform="unknown", confidence=0, signals=[f"fetch_error: {exc}"])

    ctx = _Ctx(url=url, html=html, headers=headers, pathname=path)
    scores: dict[str, int]       = {p: 0 for p in ("shopify", "magento2", "woocommerce", "wordpress")}
    signals: dict[str, list[str]] = {p: [] for p in scores}

    for name, weight, platform in _RULES:
        if _check(name, ctx):
            scores[platform] += weight
            signals[platform].append(name)

    best: Platform = max(scores, key=lambda p: scores[p])  # type: ignore
    best_score = scores[best]

    if best_score < 20:
        return PlatformResult(platform="unknown", confidence=0)

    confidence = min(99, round(best_score / _MAX_SCORE.get(best, 200) * 100))
    return PlatformResult(platform=best, confidence=confidence, signals=signals[best])
