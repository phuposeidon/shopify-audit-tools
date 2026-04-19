#!/usr/bin/env bash
# =============================================================================
#  shopify-audit-tools — One-Command Setup
#  Usage: bash setup.sh
#  Or:    curl -fsSL https://raw.githubusercontent.com/.../setup.sh | bash
# =============================================================================
set -euo pipefail

# ── Colors ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'
log()  { echo -e "${CYAN}${BOLD}[setup]${RESET} $*"; }
ok()   { echo -e "  ${GREEN}✓${RESET} $*"; }
warn() { echo -e "  ${YELLOW}⚠${RESET} $*"; }
fail() { echo -e "  ${RED}✗${RESET} $*"; exit 1; }

# ── Banner ────────────────────────────────────────────────────────────────────
echo -e "${CYAN}${BOLD}"
cat << 'EOF'
  ___                    _   _          _         _ _ _
 / _ \                  | | (_)        / \  _   _| | | |
/ /_\ \ __ _  ___ _ __ | |_ _  ___   / _ \| | | | | | |
|  _  |/ _` |/ _ \ '_ \| __| |/ __|  / ___ \ |_| | |_|_|
| | | | (_| |  __/ | | | |_| | (__  / /   \ \__,_|_(_|_)
\_| |_/\__, |\___|_| |_|\__|_|\___| \/     \/
        __/ |   Shopify Audit Tools — Docker Setup
       |___/
EOF
echo -e "${RESET}"

# ── Step 1: Check Docker ──────────────────────────────────────────────────────
log "Checking Docker..."
if ! command -v docker &>/dev/null; then
  fail "Docker not found. Install from https://docs.docker.com/get-docker/"
fi
DOCKER_VERSION=$(docker --version | grep -oP '\d+\.\d+\.\d+' | head -1)
ok "Docker $DOCKER_VERSION"

if ! command -v docker compose &>/dev/null && ! docker compose version &>/dev/null 2>&1; then
  fail "docker compose v2 not found. Upgrade Docker Desktop or install docker-compose-plugin."
fi
ok "docker compose $(docker compose version --short 2>/dev/null || echo 'v2')"

# ── Step 2: Check we're in the right directory ───────────────────────────────
log "Verifying project directory..."
if [[ ! -f "pyproject.toml" ]]; then
  fail "Run this script from the shopify-audit-tools/ directory."
fi
ok "Project root: $(pwd)"

# ── Step 3: Create .env if missing ───────────────────────────────────────────
log "Configuring environment..."
if [[ ! -f ".env" ]]; then
  cp .env.example .env
  ok "Created .env from .env.example"
  warn "Edit .env if you need custom settings (Chrome path, etc.)"
else
  ok ".env already exists — skipping"
fi

# ── Step 4: Create reports directory ─────────────────────────────────────────
mkdir -p reports
ok "reports/ directory ready"

# ── Step 5: Git submodules ─────────────────────────────────────────
log "Setting up git submodules..."

# Init git repo if not already a repo
if [[ ! -d ".git" ]]; then
  git init -q
  ok "Initialized git repository"
fi

# Register submodules if not yet registered
SUBMODULES=(
  "submodules/Scrapling|git@github.com:phuposeidon/Scrapling.git"
  "submodules/agent-browser|git@github.com:phuposeidon/agent-browser.git"
)

for entry in "${SUBMODULES[@]}"; do
  path="${entry%%|*}"
  repo="${entry##*|}"
  if [[ ! -d "$path/.git" ]]; then
    echo -e "  ${YELLOW}→${RESET} Cloning $repo → $path"
    git submodule add "$repo" "$path" 2>/dev/null || \
      git submodule add --force "$repo" "$path" 2>/dev/null || \
      true
  else
    ok "$path already cloned"
  fi
done

# Pull latest for all submodules
echo -e "  ${YELLOW}→${RESET} Updating submodules..."
if git submodule update --init --recursive --progress 2>&1 | grep -E "Cloning|done"; then
  ok "All submodules up to date"
else
  # Fallback: plain update without progress (older git)
  git submodule update --init --recursive 2>/dev/null && ok "All submodules up to date" \
    || warn "Submodule update had warnings — Docker build may still succeed"
fi

# Verify both directories are populated
for path in submodules/Scrapling submodules/agent-browser; do
  if [[ -z "$(ls -A $path 2>/dev/null)" ]]; then
    fail "$path is empty. Check SSH key access to github.com/phuposeidon"
  fi
  ok "$path ✓"
done

# ── Step 6: Build Docker image ───────────────────────────────────────────────
log "Building Docker image (this takes ~5 min on first run)..."
echo -e "  ${YELLOW}→${RESET} Downloading Python 3.11, Chromium, Node.js, Playwright..."
echo -e "  ${YELLOW}→${RESET} Grab a coffee ☕"
echo ""

if docker compose build --progress=plain 2>&1 | tail -20; then
  ok "Docker image built successfully"
else
  fail "Docker build failed. Check logs above."
fi

# ── Step 7: Smoke test ───────────────────────────────────────────────────────
log "Running smoke test..."
if docker compose run --rm audit --help &>/dev/null; then
  ok "CLI responds to --help"
else
  warn "Smoke test failed — image may still work, check manually"
fi

# ── Step 7: Print usage ───────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}════════════════════════════════════════════════════${RESET}"
echo -e "${GREEN}${BOLD}  ✅  Setup complete! Here's how to use the tool:${RESET}"
echo -e "${GREEN}${BOLD}════════════════════════════════════════════════════${RESET}"
echo ""
echo -e "${BOLD}── Quick Audit (CLI) ────────────────────────────────${RESET}"
echo -e "  docker compose run --rm audit https://store.com/products/item"
echo ""
echo -e "${BOLD}── JSON Output (for automation) ─────────────────────${RESET}"
echo -e "  docker compose run --rm audit https://url --json"
echo ""
echo -e "${BOLD}── Fast Mode (skip Lighthouse, ~5s) ─────────────────${RESET}"
echo -e "  docker compose run --rm audit https://url --no-lighthouse"
echo ""
echo -e "${BOLD}── Interactive TUI ──────────────────────────────────${RESET}"
echo -e "  docker compose run --rm -it audit-ui"
echo ""
echo -e "${BOLD}── Web UI (share with team) ─────────────────────────${RESET}"
echo -e "  docker compose up web"
echo -e "  # Open http://localhost:8080"
echo ""
echo -e "${BOLD}── Batch Audit ──────────────────────────────────────${RESET}"
echo -e "  echo 'https://url1' > urls.txt"
echo -e "  echo 'https://url2' >> urls.txt"
echo -e "  docker compose run --rm audit batch /app/urls.txt"
echo ""
echo -e "${YELLOW}  Tip: Add 'alias audit=\"docker compose run --rm audit\"'${RESET}"
echo -e "${YELLOW}       to ~/.bashrc for a native-feeling CLI experience.${RESET}"
echo ""
