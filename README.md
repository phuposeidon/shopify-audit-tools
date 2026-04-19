# shopify-audit-tools

> Standalone AI Visibility Audit Tool — CLI + TUI + Web UI

Audit any ecommerce product URL for AI shopping agent readiness — **no Shopify account required**. Detects how visible your products are to ChatGPT, Google Gemini, Perplexity, and Microsoft Copilot.

```
$ audit https://store.com/products/item

  Score: 60/100 — Grade C  Significant AI gaps

  ⚡ Core Web Vitals      19/25  ████████████████░░░░  76%
  🤖 AI Discoverability    7/35  ████░░░░░░░░░░░░░░░░  20%
  📋 Structured Data      18/20  ██████████████████░░  90%
  ✍️  Content Quality      14/15  ███████████████████░  93%
  📜 Policy                2/5   ████████░░░░░░░░░░░░  40%

  ✅ CWV: LCP 1.2s   🔴 CLS 0.45   ✅ INP 180ms   ✅ FCP 0.8s

  GPTBot: ✅  Google-Extended: ✅  MCP: ⚠  Schema: ✅  GTIN: 🔴

  🔴  NO_GTIN           -10  📖 Manual
  🔴  MISSING_H1        - 8  🔧 Theme
  🔴  HIGH_CLS          - 6  🔧 Theme
  🟠  IMAGES_INVISIBLE  - 5  🔧 Theme
  🟠  MISSING_BRAND     - 5  ✅ Auto
```

---

## Quick Start

```bash
git clone --recurse-submodules git@github.com:phuposeidon/shopify-audit-tools.git
cd shopify-audit-tools
bash setup.sh
```

`setup.sh` automatically:
1. Checks Docker is installed
2. Clones `Scrapling` + `agent-browser` submodules via SSH
3. Builds the Docker image (~5 min first run)
4. Smoke tests the CLI
5. Prints usage examples

---

## Usage

### CLI — single URL

```bash
# Full audit (Rich colored output)
docker compose run --rm audit https://store.com/products/item

# Fast mode — skip Lighthouse (~5s instead of ~30s)
docker compose run --rm audit https://store.com/products/item --no-lighthouse

# JSON output (pipe to jq, CI/CD)
docker compose run --rm audit https://store.com/products/item --json
docker compose run --rm audit https://url --json | jq .score.total

# Desktop device emulation
docker compose run --rm audit https://url --device desktop

# Save report to file
docker compose run --rm -v $(pwd)/reports:/app/reports \
  audit https://url --out /app/reports/result.json
```

### CLI — detect platform only

```bash
docker compose run --rm audit detect https://store.com/products/item
# → 🛒 shopify (97% confidence)
```

### CLI — batch audit

```bash
# Create URL list
cat > urls.txt << EOF
https://store1.com/products/item-a
https://store2.com/products/item-b
https://store3.com/products/item-c
EOF

docker compose run --rm \
  -v $(pwd)/urls.txt:/app/urls.txt \
  -v $(pwd)/reports:/app/reports \
  audit batch /app/urls.txt --workers 2 --out /app/reports/batch.csv
```

### Web UI — share with team

```bash
docker compose up web
# → Open http://localhost:8080
```

Paste any URL → click **▶ Audit** → live results with score breakdown, AI access status, and issues list.

### TUI — interactive terminal app

```bash
docker compose run --rm tui
```

### CI/CD — GitHub Actions example

```yaml
- name: Audit product page
  run: |
    docker compose run --rm audit https://store.com/products/item \
      --json --quiet > audit.json
    score=$(jq .score.total audit.json)
    [ "$score" -ge 60 ] || (echo "Score $score < 60 — failing build" && exit 1)
```

### Alias for native feel

```bash
# Add to ~/.bashrc or ~/.zshrc
alias audit="docker compose -f /path/to/shopify-audit-tools/docker-compose.yml run --rm audit"

# Then use directly
audit https://store.com/products/item
audit https://url --json | jq .score
audit batch urls.txt
```

---

## How It Works

Three audit tiers run **in parallel**:

```
URL
 │
 ├─── Tier 0: Scrapling ──────── bot-check, robots.txt, JSON-LD schema, Storefront MCP
 ├─── Tier 1: agent-browser ──── accessibility tree, H1/images/price visibility
 └─── Tier 2: Lighthouse ──────── CWV (LCP/CLS/INP), SEO score, Accessibility score
            │
            ▼
     Score Calculator
     ┌─────────────────────────────────────┐
     │  CWV            0–25 pts            │
     │  Discoverability 0–35 pts           │
     │  Structured Data 0–20 pts           │
     │  Content Quality 0–15 pts           │
     │  Policy          0–5  pts           │
     │  Scrapling Bonus 0–20 pts           │
     └─────────────────────────────────────┘
              Total: 0–100
              Grade: A / B / C / D / F
```

### Agentic Score Dimensions

| Dimension | Max | What It Measures |
|---|---|---|
| ⚡ Core Web Vitals | 25 | LCP, CLS, INP, FCP, TBT |
| 🤖 AI Discoverability | 35 | H1 in a11y tree, images, price, GTIN, brand |
| 📋 Structured Data | 20 | JSON-LD schema, SEO score |
| ✍️ Content Quality | 15 | Accessibility score, description length |
| 📜 Policy Transparency | 5 | Machine-readable return policy |
| 🎁 Scrapling Bonus | +20 | robots.txt AI rules, Storefront MCP, schema GTIN/brand |

### Bot-Blocking Detection

If GPTBot or Google-Extended are blocked (WAF/Cloudflare or `robots.txt`), the store receives **Score 0 / Grade F** regardless of Lighthouse score.

---

## Platform Support

Auto-detected via HTTP fingerprinting — no configuration needed:

| Platform | Detection | Audit |
|---|---|---|
| **Shopify** | ✅ 97%+ accuracy | ✅ Full |
| **Magento 2** | ✅ REST headers | ✅ Audit-only |
| **WooCommerce** | ✅ wp-content signals | ✅ Audit-only |
| **WordPress** | ✅ Generator meta | ✅ Audit-only |
| **Other** | ⚠️ Best effort | ✅ Audit-only |

---

## Architecture

```
shopify-audit-tools/
├── audit/
│   ├── cli.py                  Typer CLI (audit/detect/batch/version)
│   ├── server.py               FastAPI REST API + HTML dashboard
│   ├── core/
│   │   ├── models.py           Dataclasses: Score, CWV, Issue, AuditResult
│   │   ├── orchestrator.py     asyncio.gather — parallel 3-tier runner
│   │   └── scorer.py           Score calculator (ported from agentic-readiness)
│   ├── tiers/
│   │   ├── tier0_scrapling.py  robots.txt, JSON-LD, MCP, bot-check
│   │   ├── tier1_browser.py    agent-browser subprocess wrapper
│   │   └── tier2_lighthouse.py Lighthouse CLI subprocess wrapper
│   └── platform/
│       └── detector.py         21-rule fingerprint detector
├── submodules/
│   ├── Scrapling/              git@github.com:phuposeidon/Scrapling.git
│   └── agent-browser/          git@github.com:phuposeidon/agent-browser.git
├── Dockerfile                  Multi-stage: builder → runtime → web
├── docker-compose.yml          Services: audit / tui / web
├── setup.sh                    One-command setup
└── pyproject.toml              Package: `audit` + `audit-ui` + `audit-web`
```

---

## Web API Reference

When running `docker compose up web` (`http://localhost:8080`):

```
GET  /              HTML dashboard
GET  /health        { "ok": true, "version": "0.1.0" }

POST /audit         { "url": "...", "device": "mobile" }
                    → { "job_id": "abc123", "status": "queued" }

GET  /audit/{id}    → { "status": "done", "result": {...} }

POST /audit/sync    → AuditResult JSON (waits ~30–90s)

POST /batch         { "urls": [...], "workers": 2 }
                    → [ AuditResult, ... ]

GET  /detect?url=   → { "platform": "shopify", "confidence": 97 }
```

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `CHROME_PATH` | `/usr/bin/chromium` | Chromium binary path |
| `AGENT_BROWSER_PATH` | `/usr/local/bin/agent-browser` | agent-browser binary |
| `LIGHTHOUSE_PATH` | auto-detect | Lighthouse binary |
| `PORT` | `8080` | Web server port |
| `MAX_CONCURRENT_AUDITS` | `2` | Parallel audits in web mode |

Copy `.env.example` → `.env` to override locally.

---

## Requirements

| Tool | Version | Required for |
|---|---|---|
| Docker | ≥ 24 | Everything |
| docker compose | v2 | Everything |
| SSH key | — | GitHub submodule access |

> SSH key must have read access to `github.com/phuposeidon` (Scrapling + agent-browser repos).

---

## Development (without Docker)

```bash
# Install Python deps (requires Python ≥ 3.10)
pip install -e "submodules/Scrapling[fetchers]"
pip install -e ".[dev]"

# Install Node tools
npm install -g lighthouse
cd submodules/agent-browser && npm install -g . && cd ../..

# Run directly
audit https://store.com/products/item
audit-web  # → http://localhost:8080
```

---

## License

Copyright (c) 2025-present phuposeidon. All rights reserved. See [LICENSE](LICENSE).
