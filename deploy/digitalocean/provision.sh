#!/usr/bin/env bash
# Provision a fresh Ubuntu 24.04 droplet to run the lovspor hosted MCP.
#
# Idempotent — safe to re-run. Two passes, because the lovspor repo is PRIVATE:
#   pass 1 generates a read-only deploy key and prints it, then stops;
#   pass 2 (after you add that key to GitHub) clones the app and finishes.
#
# Run as root on the droplet:
#   sudo bash provision.sh
#
# Full runbook: deploy/digitalocean/README.md
set -euo pipefail

APP_USER=lovspor
APP_HOME=/opt/lovspor
APP_DIR="$APP_HOME/app"
CORPUS_DIR="$APP_HOME/.cache/lovverk"
ENV_FILE=/etc/lovspor/lovspor.env
REPO_SSH="git@github.com:bartoszkobylinski/lovspor.git"
KEYS_URL="https://github.com/bartoszkobylinski/lovspor/settings/keys"
SWAP_GB=2

log()  { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
warn() { printf '\n\033[1;33m!!  %s\033[0m\n' "$*"; }
[ "$(id -u)" -eq 0 ] || { echo "run as root: sudo bash provision.sh"; exit 1; }

# --- 1. Swap (DO droplets ship with none; smooths the ~1.24 GB startup warm peak) ---
if ! swapon --show=NAME --noheadings | grep -q '/swapfile'; then
	log "Creating ${SWAP_GB}G swapfile"
	fallocate -l "${SWAP_GB}G" /swapfile
	chmod 600 /swapfile
	mkswap /swapfile >/dev/null
	swapon /swapfile
	grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >>/etc/fstab
fi

# --- 2. Base packages ---
log "Installing base packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git curl ca-certificates gnupg debian-keyring debian-archive-keyring apt-transport-https

# --- 3. Caddy (official apt repo) ---
if ! command -v caddy >/dev/null; then
	log "Installing Caddy"
	curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
		| gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
	curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
		>/etc/apt/sources.list.d/caddy-stable.list
	apt-get update -qq
	apt-get install -y -qq caddy
fi

# --- 4. Service user ---
if ! id "$APP_USER" >/dev/null 2>&1; then
	log "Creating service user $APP_USER"
	useradd --system --create-home --home-dir "$APP_HOME" --shell /usr/sbin/nologin "$APP_USER"
fi

# --- 5. uv (for the service user) ---
UV="$APP_HOME/.local/bin/uv"
if [ ! -x "$UV" ]; then
	log "Installing uv"
	sudo -u "$APP_USER" sh -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'
fi

# --- 6. Deploy key (repo is PRIVATE) ---
KEY="$APP_HOME/.ssh/id_ed25519"
if [ ! -f "$KEY" ]; then
	log "Generating read-only deploy key"
	sudo -u "$APP_USER" mkdir -p "$APP_HOME/.ssh"
	sudo -u "$APP_USER" ssh-keygen -t ed25519 -N '' -f "$KEY" -C "lovspor-droplet-deploy" >/dev/null
	sudo -u "$APP_USER" sh -c "ssh-keyscan -t ed25519 github.com >> '$APP_HOME/.ssh/known_hosts' 2>/dev/null"
fi
# `ssh -T git@github.com` exits 1 even on success, so capture output and test the
# message — piping straight into grep would trip pipefail on the benign exit code.
AUTH_OUT="$(sudo -u "$APP_USER" ssh -o BatchMode=yes -T git@github.com 2>&1 || true)"
if ! printf '%s' "$AUTH_OUT" | grep -q "successfully authenticated"; then
	warn "Deploy key is not authorized on GitHub yet."
	warn "Add this PUBLIC key as a read-only Deploy Key: $KEYS_URL"
	echo; cat "$KEY.pub"; echo
	warn "Then re-run: sudo bash provision.sh"
	exit 0
fi

# --- 7. Clone / update the app ---
if [ ! -d "$APP_DIR/.git" ]; then
	log "Cloning lovspor"
	sudo -u "$APP_USER" git clone "$REPO_SSH" "$APP_DIR"
else
	log "Updating lovspor"
	sudo -u "$APP_USER" git -C "$APP_DIR" pull --ff-only
fi

# --- 8. Python env ---
log "Installing dependencies (uv sync)"
sudo -u "$APP_USER" sh -c "cd '$APP_DIR' && '$UV' sync --frozen --no-dev"

# --- 9. Corpus (public repo, no auth; ~1.3 GB, first run is slow) ---
log "Fetching lovverk corpus"
sudo -u "$APP_USER" sh -c "cd '$APP_DIR' && LOVVERK_CORPUS_PATH='$CORPUS_DIR' '$APP_DIR/.venv/bin/lovspor' fetch-corpus"

# --- 10. Secrets env file (placeholder; NEVER commit real keys) ---
if [ ! -f "$ENV_FILE" ]; then
	log "Creating $ENV_FILE (fill in OPENAI_API_KEY)"
	mkdir -p /etc/lovspor
	printf '%s\n' \
		'# lovspor MCP secrets — read by systemd at start (restart after editing).' \
		'# Required for semantic_search; the other 15 tools work without it.' \
		'OPENAI_API_KEY=' >"$ENV_FILE"
	chmod 640 "$ENV_FILE"
	chown root:"$APP_USER" "$ENV_FILE"
fi

# --- 11. Caddy domain via env (keeps the committed Caddyfile domain-free) ---
if [ ! -f /etc/default/caddy-lovspor ]; then
	echo 'LOVSPOR_DOMAIN=lovspor.example.com' >/etc/default/caddy-lovspor
fi
mkdir -p /etc/systemd/system/caddy.service.d
printf '%s\n' '[Service]' 'EnvironmentFile=/etc/default/caddy-lovspor' \
	>/etc/systemd/system/caddy.service.d/lovspor.conf

# --- 12. Install units + Caddyfile from the repo ---
log "Installing systemd units and Caddyfile"
install -m644 "$APP_DIR/deploy/digitalocean/lovspor-mcp.service" /etc/systemd/system/
install -m644 "$APP_DIR/deploy/digitalocean/lovspor-fetch-corpus.service" /etc/systemd/system/
install -m644 "$APP_DIR/deploy/digitalocean/lovspor-fetch-corpus.timer" /etc/systemd/system/
install -d /etc/caddy
install -m644 "$APP_DIR/deploy/digitalocean/Caddyfile" /etc/caddy/Caddyfile
systemctl daemon-reload
systemctl enable lovspor-mcp.service lovspor-fetch-corpus.timer >/dev/null

log "Base provisioning complete. Finish going live (see README.md § Go live):"
cat <<EOF

  1. OpenAI key:  sudo nano $ENV_FILE            # set OPENAI_API_KEY=...
  2. Beta token:  sudo -u $APP_USER $APP_DIR/.venv/bin/lovspor tokens issue --label "you@beta"
  3. Domain:      echo 'LOVSPOR_DOMAIN=lovspor.yourdomain.com' | sudo tee /etc/default/caddy-lovspor
  4. DNS:         point an A record for that domain at this droplet's public IP.
  5. Start:       sudo systemctl restart caddy lovspor-mcp
  6. Verify:      curl -fsS https://lovspor.yourdomain.com/healthz && echo ' OK'
EOF
