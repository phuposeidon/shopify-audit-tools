"""
Shopify GraphQL Adapter — queries Admin API for rich product data
that cannot be extracted via HTML crawling (GTIN, metafields, etc.)

Requires: SHOPIFY_ACCESS_TOKEN + SHOPIFY_SHOP_DOMAIN in environment.
Gracefully degrades (returns None) when credentials are absent.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

import httpx

TIMEOUT = httpx.Timeout(10.0)

# ── Product fields from GraphQL ────────────────────────────────────

@dataclass
class ShopifyProductData:
    """Enriched product data from Shopify Admin GraphQL."""
    product_id: str = ""
    title: str = ""
    vendor: str = ""                   # brand
    description_html: str = ""
    description_words: int = 0
    has_gtin: bool = False
    gtin: Optional[str] = None         # barcode from first variant
    has_return_policy: bool = False
    return_policy_text: Optional[str] = None
    storefront_mcp_available: bool = False
    metafields: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    available: bool = True             # False when no credentials


# ── GraphQL Queries ────────────────────────────────────────────────

_PRODUCT_QUERY = """
query GetProductByHandle($handle: String!) {
  productByHandle(handle: $handle) {
    id
    title
    vendor
    descriptionHtml
    variants(first: 1) {
      edges {
        node {
          barcode
          sku
        }
      }
    }
    metafields(
      identifiers: [
        { namespace: "global", key: "description_tag" },
        { namespace: "custom", key: "return_policy" },
        { namespace: "custom", key: "shipping_policy" }
      ]
    ) {
      namespace
      key
      value
    }
  }
}
"""

_RETURN_POLICY_QUERY = """
query GetReturnPolicy {
  shop {
    name
    refundPolicy {
      body
    }
    privacyPolicy {
      body
    }
  }
}
"""

_STOREFRONT_MCP_QUERY = """
query StorefrontMCPCheck {
  shop {
    name
    features {
      storefront: storefrontApiSecured
    }
  }
}
"""


# ── Client ─────────────────────────────────────────────────────────

class ShopifyGraphQLClient:
    """
    Thin async wrapper around Shopify Admin GraphQL API.
    Credentials loaded from environment or passed explicitly.
    """

    def __init__(
        self,
        shop_domain: Optional[str] = None,
        access_token: Optional[str] = None,
        api_version: str = "2025-01",
    ) -> None:
        self.shop_domain = (
            shop_domain or
            os.environ.get("SHOPIFY_SHOP_DOMAIN", "")
        ).strip().rstrip("/")
        self.access_token = (
            access_token or
            os.environ.get("SHOPIFY_ACCESS_TOKEN", "")
        ).strip()
        self.api_version = api_version

    @property
    def _available(self) -> bool:
        return bool(self.shop_domain and self.access_token)

    @property
    def _endpoint(self) -> str:
        domain = self.shop_domain
        if not domain.startswith("https://"):
            domain = f"https://{domain}"
        if not domain.endswith(".myshopify.com") and "." not in domain.split("//")[-1]:
            domain = f"{domain}.myshopify.com"
        return f"{domain}/admin/api/{self.api_version}/graphql.json"

    @property
    def _headers(self) -> dict:
        return {
            "X-Shopify-Access-Token": self.access_token,
            "Content-Type": "application/json",
        }

    async def _query(self, gql: str, variables: dict | None = None) -> dict:
        payload: dict = {"query": gql}
        if variables:
            payload["variables"] = variables
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.post(self._endpoint, json=payload, headers=self._headers)
            resp.raise_for_status()
        data = resp.json()
        if errors := data.get("errors"):
            raise RuntimeError(f"GraphQL errors: {errors[:2]}")
        return data.get("data", {})

    async def get_product(self, url: str) -> ShopifyProductData:
        """Fetch product data from URL. Returns ShopifyProductData (available=False if no creds)."""
        if not self._available:
            return ShopifyProductData(available=False, errors=["No SHOPIFY_ACCESS_TOKEN configured"])

        handle = _extract_handle(url)
        if not handle:
            return ShopifyProductData(available=False, errors=[f"Cannot extract product handle from: {url}"])

        result = ShopifyProductData()
        try:
            data = await self._query(_PRODUCT_QUERY, {"handle": handle})
            product = data.get("productByHandle")
            if not product:
                result.errors.append(f"Product not found: handle={handle}")
                return result

            result.product_id = product.get("id", "")
            result.title      = product.get("title", "")
            result.vendor     = product.get("vendor", "")

            # Description
            desc_html = product.get("descriptionHtml", "") or ""
            result.description_html  = desc_html
            result.description_words = _count_words(desc_html)

            # GTIN from first variant barcode
            variants = product.get("variants", {}).get("edges", [])
            if variants:
                barcode = variants[0].get("node", {}).get("barcode", "") or ""
                if barcode.strip():
                    result.has_gtin = True
                    result.gtin = barcode.strip()

            # Metafields
            for mf in product.get("metafields") or []:
                if mf:
                    key = f"{mf['namespace']}.{mf['key']}"
                    result.metafields[key] = mf["value"]

        except Exception as exc:
            result.errors.append(f"product_query: {exc}")
            return result

        # Return policy (separate query)
        try:
            shop_data = await self._query(_RETURN_POLICY_QUERY)
            refund = shop_data.get("shop", {}).get("refundPolicy") or {}
            body   = refund.get("body") or ""
            result.has_return_policy = bool(body.strip())
            if result.has_return_policy:
                result.return_policy_text = body[:500]
        except Exception as exc:
            result.errors.append(f"return_policy_query: {exc}")

        return result

    async def check_storefront_mcp(self) -> bool:
        """Check if Storefront API is available (proxy for MCP readiness)."""
        if not self._available:
            return False
        try:
            data = await self._query(_STOREFRONT_MCP_QUERY)
            features = data.get("shop", {}).get("features", {})
            # Also try HEAD check on Storefront MCP well-known URL
            return bool(features.get("storefront"))
        except Exception:
            return False


# ── URL → handle parser ───────────────────────────────────────────

def _extract_handle(url: str) -> Optional[str]:
    """
    Extract Shopify product handle from URL.
    Examples:
      https://store.com/products/cool-shoes-123  → cool-shoes-123
      https://store.com/collections/sale/products/tee → tee
    """
    path = urlparse(url).path
    m = re.search(r"/products/([^/?#]+)", path)
    return m.group(1) if m else None


def _count_words(html: str) -> int:
    """Strip HTML tags and count words."""
    clean = re.sub(r"<[^>]+>", " ", html)
    return len(clean.split())


# ── Convenience factory ───────────────────────────────────────────

def client_from_env() -> ShopifyGraphQLClient:
    """Create client from environment variables."""
    return ShopifyGraphQLClient()
