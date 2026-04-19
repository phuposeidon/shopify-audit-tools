# ═══════════════════════════════════════════════════════════════════
# Stage 1: Python wheel builder
# ═══════════════════════════════════════════════════════════════════
FROM python:3.11-slim AS python-builder

WORKDIR /build
# Copy package definition + local Scrapling submodule
COPY pyproject.toml .
COPY audit/ ./audit/
# Copy Scrapling from submodule (private fork)
COPY submodules/Scrapling ./submodules/Scrapling

RUN pip install --no-cache-dir build wheel && \
    # Install Scrapling from local source (includes fetchers extras)
    pip install --no-cache-dir -e "./submodules/Scrapling[fetchers]" && \
    # Wheel the main package
    pip wheel --no-cache-dir --wheel-dir /wheels .

# ═══════════════════════════════════════════════════════════════════
# Stage 2: Full runtime image
#   Python 3.11 + Node.js 20 + Chromium + Playwright
# ═══════════════════════════════════════════════════════════════════
FROM python:3.11-slim AS runtime

LABEL org.opencontainers.image.title="shopify-audit-tools"
LABEL org.opencontainers.image.description="AI Visibility Audit for ecommerce — CLI + TUI + Web"
LABEL org.opencontainers.image.version="0.1.0"

# ── System packages ───────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Chromium (used by both Playwright + Lighthouse)
    chromium \
    # Node.js for Lighthouse CLI + agent-browser
    nodejs \
    npm \
    # Font rendering for Chromium
    fonts-liberation \
    fonts-noto-color-emoji \
    # Playwright/Chromium runtime deps
    libglib2.0-0 \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    # curl for health checks
    curl \
    # Process management
    tini \
    && rm -rf /var/lib/apt/lists/*

# ── Node.js tools (from local submodule) ─────────────────────────
# Copy agent-browser submodule and install it globally
COPY submodules/agent-browser /tmp/agent-browser
RUN cd /tmp/agent-browser && \
    npm install --omit=dev --no-audit --no-fund 2>/dev/null && \
    npm install -g . 2>/dev/null && \
    rm -rf /tmp/agent-browser && \
    # Also install lighthouse from npm registry (no private fork)
    npm install -g lighthouse@13 2>/dev/null || npm install -g lighthouse 2>/dev/null

# ── Python app + Scrapling from local submodule ───────────────────
WORKDIR /app
COPY --from=python-builder /wheels /wheels
COPY submodules/Scrapling ./submodules/Scrapling
COPY . .
RUN pip install --no-cache-dir -e "./submodules/Scrapling[fetchers]" && \
    pip install --no-cache-dir --no-index --find-links=/wheels shopify-audit-tools && \
    rm -rf /wheels

# ── Playwright browser for Scrapling fetchers ─────────────────────
# Use system Chromium to avoid duplicate download
ENV PLAYWRIGHT_BROWSERS_PATH=/usr/lib/chromium
ENV PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium
RUN python -m playwright install-deps chromium 2>/dev/null || true

# ── Environment defaults ──────────────────────────────────────────
ENV CHROME_PATH=/usr/bin/chromium
ENV AGENT_BROWSER_PATH=/usr/local/bin/agent-browser
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
# Terminal settings for Rich/Textual
ENV TERM=xterm-256color
ENV COLORTERM=truecolor

# Create reports directory
RUN mkdir -p /app/reports

# Non-root user for security
RUN useradd -m -u 1000 -s /bin/bash auditor && \
    chown -R auditor:auditor /app
USER auditor

# Healthcheck (only useful in web mode)
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8080/health 2>/dev/null || exit 0

ENTRYPOINT ["/usr/bin/tini", "--", "audit"]
CMD ["--help"]

# ═══════════════════════════════════════════════════════════════════
# Stage 3: Web server mode (extends runtime)
# ═══════════════════════════════════════════════════════════════════
FROM runtime AS web

USER root
# FastAPI + uvicorn already installed via pyproject.toml
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

USER auditor
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["audit-web", "--host", "0.0.0.0", "--port", "8080"]
