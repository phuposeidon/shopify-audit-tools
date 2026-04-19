"""
Core data models — shared across CLI, TUI, and Web modes.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal, Optional


# ── Issue Types ───────────────────────────────────────────────────

Severity  = Literal["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]
Category  = Literal["CWV", "DISCOVERABILITY", "SCHEMA", "CONTENT", "POLICY"]
FixType   = Literal["auto", "theme", "manual"]
Platform  = Literal["shopify", "magento2", "woocommerce", "wordpress", "unknown"]


@dataclass
class Issue:
    type: str
    severity: Severity
    category: Category
    title: str
    description: str
    recommendation: str
    points_lost: int
    fixable: bool
    fix_type: FixType


# ── Score Breakdown ───────────────────────────────────────────────

@dataclass
class Score:
    cwv: int        # 0-25
    discover: int   # 0-35
    schema: int     # 0-20
    content: int    # 0-15
    policy: int     # 0-5
    bonus: int = 0  # Scrapling extras (robots + MCP + schema)

    @property
    def total(self) -> int:
        return min(100, self.cwv + self.discover + self.schema + self.content + self.policy + self.bonus)

    @property
    def grade(self) -> str:
        t = self.total
        if t >= 90: return "A"
        if t >= 75: return "B"
        if t >= 60: return "C"
        if t >= 40: return "D"
        return "F"

    @property
    def grade_label(self) -> str:
        return {
            "A": "AI-ready ✅",
            "B": "Minor fixes needed",
            "C": "Significant AI gaps",
            "D": "Poor AI visibility",
            "F": "Critical — AI blind",
        }[self.grade]

    @property
    def grade_color(self) -> str:
        """Rich color string."""
        return {"A": "green", "B": "bright_green", "C": "yellow", "D": "orange3", "F": "red"}[self.grade]


# ── CWV Metrics ───────────────────────────────────────────────────

@dataclass
class CWV:
    lcp: Optional[float] = None   # ms
    cls: Optional[float] = None
    inp: Optional[float] = None   # ms
    fcp: Optional[float] = None   # ms
    tbt: Optional[float] = None   # ms
    ttfb: Optional[float] = None  # ms

    def lcp_label(self) -> str:
        if self.lcp is None: return "—"
        return f"{self.lcp/1000:.2f}s"

    def lcp_status(self) -> str:
        if self.lcp is None: return "unknown"
        return "good" if self.lcp <= 2500 else "needs-work" if self.lcp <= 4000 else "poor"

    def cls_status(self) -> str:
        if self.cls is None: return "unknown"
        return "good" if self.cls <= 0.1 else "needs-work" if self.cls <= 0.25 else "poor"


# ── Platform Detection ────────────────────────────────────────────

@dataclass
class PlatformResult:
    platform: Platform
    confidence: int   # 0-100
    signals: list[str] = field(default_factory=list)
    version: Optional[str] = None

    @property
    def icon(self) -> str:
        return {"shopify": "🛒", "magento2": "🔷", "woocommerce": "🔵", "wordpress": "📝", "unknown": "❓"}[self.platform]


# ── Scrapling (Tier 0) ────────────────────────────────────────────

@dataclass
class AIAccessStatus:
    gptbot_allowed: Optional[bool] = None
    google_extended_allowed: Optional[bool] = None
    perplexitybot_allowed: Optional[bool] = None
    mcp_detected: bool = False
    mcp_endpoint: Optional[str] = None
    has_product_schema: bool = False
    gtin_in_schema: Optional[str] = None
    brand_in_schema: Optional[str] = None
    price_in_schema: Optional[str] = None
    robots_bonus: int = 0

    @property
    def bot_blocked(self) -> bool:
        return self.gptbot_allowed is False and self.google_extended_allowed is False


# ── Full Audit Result ─────────────────────────────────────────────

@dataclass
class AuditResult:
    url: str
    platform: PlatformResult
    score: Score
    cwv: CWV
    ai_access: AIAccessStatus
    issues: list[Issue]
    bot_blocked: bool
    duration_ms: int
    summary: str
    errors: list[str] = field(default_factory=list)

    # Lighthouse scores
    lh_performance: Optional[int] = None
    lh_seo: Optional[int] = None
    lh_accessibility: Optional[int] = None
    lh_best_practices: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "platform": {
                "name": self.platform.platform,
                "confidence": self.platform.confidence,
                "signals": self.platform.signals,
            },
            "score": {
                "total": self.score.total,
                "grade": self.score.grade,
                "cwv": self.score.cwv,
                "discover": self.score.discover,
                "schema": self.score.schema,
                "content": self.score.content,
                "policy": self.score.policy,
                "bonus": self.score.bonus,
            },
            "cwv": {
                "lcp_ms": self.cwv.lcp,
                "cls": self.cwv.cls,
                "inp_ms": self.cwv.inp,
                "fcp_ms": self.cwv.fcp,
                "tbt_ms": self.cwv.tbt,
            },
            "ai_access": {
                "gptbot_allowed": self.ai_access.gptbot_allowed,
                "google_extended_allowed": self.ai_access.google_extended_allowed,
                "mcp_detected": self.ai_access.mcp_detected,
                "has_product_schema": self.ai_access.has_product_schema,
                "gtin": self.ai_access.gtin_in_schema,
                "brand": self.ai_access.brand_in_schema,
            },
            "issues": [
                {
                    "type": i.type,
                    "severity": i.severity,
                    "title": i.title,
                    "recommendation": i.recommendation,
                    "points_lost": i.points_lost,
                    "fixable": i.fixable,
                }
                for i in self.issues
            ],
            "bot_blocked": self.bot_blocked,
            "lighthouse": {
                "performance": self.lh_performance,
                "seo": self.lh_seo,
                "accessibility": self.lh_accessibility,
                "best_practices": self.lh_best_practices,
            },
            "duration_ms": self.duration_ms,
            "errors": self.errors,
        }
