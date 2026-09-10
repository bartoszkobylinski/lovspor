#!/usr/bin/env bash
# Provision a fresh Ubuntu 24.04 droplet to run the lovspor hosted MCP.
#
# Safe to re-run on a box THIS script provisioned. It REFUSES a box that is
# already serving from the pre-envelope layout — an existing /etc/caddy/Caddyfile
# with no release marker — because a re-run there overwrites that live Caddyfile
# with no backup. Moving such a box onto the release envelope is
# `lovspor release migrate` (README, First migration), never this script.
# A box provisioned but never published trips the same rule; that one legitimate
# repair run is LOVSPOR_PROVISION_FORCE=1.
#
# Two passes, because the lovspor repo is PRIVATE:
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
CADDYFILE=/etc/caddy/Caddyfile
MARKER=/var/www/lovspor-releases/ACTIVE
KEYS_URL="https://github.com/bartoszkobylinski/lovspor/settings/keys"
SWAP_GB=2

log()  { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
warn() { printf '\n\033[1;33m!!  %s\033[0m\n' "$*"; }
[ "$(id -u)" -eq 0 ] || { echo "run as root: sudo bash provision.sh"; exit 1; }

# --- 0. Never provision over a box that is already serving ---
# On a droplet serving lovspor.no from the pre-envelope layout, step 13 replaces
# the live Caddyfile with the socket-admin one and writes NO .pre-envelope
# backup, while step 11 installs an ExecReload= line naming a socket that does
# not exist yet — so the next `systemctl restart caddy` serves the 503
# placeholder instead of the site, and there is nothing on disk to go back to.
# The release marker is what tells the two boxes apart: past the first migration
# this file is already this repository's own, and a re-run installs what is
# there. Before it, the only supported move is `lovspor release migrate`.
if [ -f "$CADDYFILE" ] && [ ! -f "$MARKER" ] && [ "${LOVSPOR_PROVISION_FORCE:-0}" != 1 ]; then
	echo "refusing: $CADDYFILE exists and no release is live ($MARKER is absent)."
	echo "This box may already serve lovspor.no; provisioning would overwrite its Caddyfile"
	echo "with no backup and point ExecReload= at a socket that does not exist yet."
	echo "Migrate it instead: 'lovspor release migrate' — see deploy/digitalocean/README.md,"
	echo "First migration (once, on the existing droplet)."
	echo "If this box was provisioned by this script and has never published, that repair"
	echo "run is: LOVSPOR_PROVISION_FORCE=1 sudo -E bash provision.sh"
	exit 1
fi

# --- 1. Swap (DO droplets ship with none; smooths the ~1.24 GB startup warm peak) ---
SWAP_ACTIVE="$(swapon --show=NAME --noheadings || true)"
if printf '%s\n' "$SWAP_ACTIVE" | grep -qx '/swapfile'; then
	: # already active
elif [ -e /swapfile ] || [ -L /swapfile ]; then
	# Do not clobber/follow an unexpected path — only reuse a plain regular file.
	if [ -L /swapfile ] || [ ! -f /swapfile ]; then
		echo "refusing: /swapfile exists but is not a regular file"; exit 1
	fi
	log "Reusing existing /swapfile"
	chmod 600 /swapfile
	swapon /swapfile 2>/dev/null || { mkswap /swapfile >/dev/null && swapon /swapfile; }
	grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >>/etc/fstab
else
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
	# --batch --yes so a keyring left by an interrupted earlier run is overwritten
	# without an interactive prompt in the non-interactive SSH flow.
	curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
		| gpg --batch --yes --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
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
KNOWN="$APP_HOME/.ssh/known_hosts"
sudo -u "$APP_USER" mkdir -p "$APP_HOME/.ssh"
chmod 700 "$APP_HOME/.ssh"

# Pin GitHub's PUBLISHED Ed25519 host key. Do NOT trust ssh-keyscan — it accepts
# whatever the network returns, so a provisioning-time MITM could serve a malicious
# repo whose systemd unit is later installed by root. Idempotent and run every pass
# (repairs an interrupted first run). Source: https://api.github.com/meta
GH_HOSTKEY='github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl'
if ! sudo -u "$APP_USER" grep -qF "$GH_HOSTKEY" "$KNOWN" 2>/dev/null; then
	log "Pinning GitHub host key"
	printf '%s\n' "$GH_HOSTKEY" | sudo -u "$APP_USER" tee -a "$KNOWN" >/dev/null
fi

if [ ! -f "$KEY" ]; then
	log "Generating read-only deploy key"
	sudo -u "$APP_USER" ssh-keygen -t ed25519 -N '' -f "$KEY" -C "lovspor-droplet-deploy" >/dev/null
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

# --- 9. Corpus (public repo, no auth; ~2.2 GB with full history, first run is slow) ---
# --full-history: temporal tools (get_law_at/diff_law_versions) walk git log;
# a shallow clone would limit them to post-provisioning dates (ADR-0003).
log "Fetching lovverk corpus (full history)"
sudo -u "$APP_USER" sh -c "cd '$APP_DIR' && LOVVERK_CORPUS_PATH='$CORPUS_DIR' '$APP_DIR/.venv/bin/lovspor' fetch-corpus --full-history"

# --- 10. Secrets env file (placeholder; NEVER commit real keys) ---
mkdir -p /etc/lovspor
if [ ! -f "$ENV_FILE" ]; then
	log "Creating $ENV_FILE (fill in OPENAI_API_KEY)"
	printf '%s\n' \
		'# lovspor MCP secrets — read by systemd at start (restart after editing).' \
		'# Required for semantic_search; the other 15 tools work without it.' \
		'OPENAI_API_KEY=' \
		'' \
		'# Optional: self-service OAuth (ChatGPT/Claude.ai connectors) via WorkOS.' \
		'# Give BOTH a value or NEITHER — one alone and the server exits on start.' \
		'# (Present but empty counts as unset, i.e. plain opaque-token mode.)' \
		'# LOVSPOR_PUBLIC_URL is this host public /mcp URL, not the WorkOS one.' \
		'#LOVSPOR_AUTHKIT_DOMAIN=https://your-project.authkit.app' \
		'#LOVSPOR_PUBLIC_URL=https://lovspor.example.com/mcp' >"$ENV_FILE"
fi
# Enforce ownership + mode every run (never overwrite contents) so a rerun repairs
# a loosened secret file rather than leaving it world-readable.
chown root:"$APP_USER" "$ENV_FILE"
chmod 640 "$ENV_FILE"
# Ensure the service's writable dirs exist (ReadWritePaths requires them at start).
sudo -u "$APP_USER" mkdir -p "$APP_HOME/.cache" "$APP_HOME/.config"

# --- 11. Caddy: the domain, the admin socket's group and the service drop-in ---
if [ ! -f /etc/default/caddy-lovspor ]; then
	echo 'LOVSPOR_DOMAIN=lovspor.example.com' >/etc/default/caddy-lovspor
fi

# The admin API is what makes a release live and the only thing that can say what
# Caddy actually serves, so it is bound to a Unix socket created 0660 in this
# group instead of the default localhost:2019, where every process on the box
# could rewrite the running configuration (ADR-0014 Decision 6). Root — the
# release and reconcile identity — is in the group; User=lovspor, the
# network-facing MCP service, deliberately is not.
groupadd --system --force lovspor-release
usermod -aG lovspor-release root

# The drop-in a fresh box gets is the whole thing, including the ExecReload=
# pair: it starts Caddy on the socket, so every `systemctl reload caddy` reaches
# the address the release commands address. (The first migration of a box that
# is already serving on TCP installs this in two phases instead — the pair last,
# after the cutover — which is `lovspor release migrate`'s business, not this
# script's.) RuntimeDirectory= recreates /run/caddy at every start and the
# ExecStartPre chgrp gives it the group, so a recreated socket inherits it
# through the setgid bit.
mkdir -p /etc/systemd/system/caddy.service.d
cat >/etc/systemd/system/caddy.service.d/lovspor.conf <<'DROP_IN'
# Written by lovspor (ADR-0014 Decision 6): provisioning and the first migration.
[Service]
EnvironmentFile=/etc/default/caddy-lovspor
RuntimeDirectory=caddy
RuntimeDirectoryMode=2770
ExecStartPre=+/usr/bin/chgrp lovspor-release /run/caddy
ExecReload=
ExecReload=/usr/bin/caddy reload --config /etc/caddy/Caddyfile --force --address unix//run/caddy/admin.sock
DROP_IN

# The release probe's credential (docs/mcp.md § Release probe and drift check).
# The directory only: the token is issued once at go-live and written 0600 by
# the operator, so it is never in this script and never in the repository.
install -d -m 700 /etc/lovspor/credentials

# --- 12. Verify the checkout before root installs anything from it ---
# The next step copies unit files into /etc/systemd/system as root, but the checkout
# is owned by the network-facing service user. Confirm origin + cleanliness + that HEAD
# matches upstream, so a locally tampered unit can never be installed with root rights.
# (The deploy key is read-only, so an attacker cannot push tampered content upstream.)
log "Verifying checkout integrity before install"
ORIGIN="$(sudo -u "$APP_USER" git -C "$APP_DIR" remote get-url origin)"
[ "$ORIGIN" = "$REPO_SSH" ] || { warn "unexpected origin: $ORIGIN"; exit 1; }
[ -z "$(sudo -u "$APP_USER" git -C "$APP_DIR" status --porcelain)" ] \
	|| { warn "working tree at $APP_DIR is dirty — refusing to install units from it"; exit 1; }
sudo -u "$APP_USER" git -C "$APP_DIR" fetch -q origin
LOCAL_HEAD="$(sudo -u "$APP_USER" git -C "$APP_DIR" rev-parse HEAD)"
UPSTREAM_HEAD="$(sudo -u "$APP_USER" git -C "$APP_DIR" rev-parse '@{upstream}')"
[ "$LOCAL_HEAD" = "$UPSTREAM_HEAD" ] \
	|| { warn "HEAD ($LOCAL_HEAD) != upstream ($UPSTREAM_HEAD) — refusing"; exit 1; }

# --- 13. Install units + Caddyfile from the (verified) repo ---
log "Installing systemd units and Caddyfile"
install -m644 "$APP_DIR/deploy/digitalocean/lovspor-mcp.service" /etc/systemd/system/
install -m644 "$APP_DIR/deploy/digitalocean/lovspor-fetch-corpus.service" /etc/systemd/system/
install -m644 "$APP_DIR/deploy/digitalocean/lovspor-fetch-corpus.timer" /etc/systemd/system/
install -m644 "$APP_DIR/deploy/digitalocean/lovspor-publish.service" /etc/systemd/system/
install -m644 "$APP_DIR/deploy/digitalocean/lovspor-site-drift.service" /etc/systemd/system/
install -m644 "$APP_DIR/deploy/digitalocean/lovspor-site-drift.timer" /etc/systemd/system/
install -d /etc/caddy
install -m644 "$APP_DIR/deploy/digitalocean/Caddyfile" /etc/caddy/Caddyfile
# The Caddyfile serves everything outside /mcp through a plain `import` of the
# active release fragment, so that file has to exist before `caddy validate`
# can pass — a glob matching nothing would leave the site block empty and 404
# every request with no error recorded anywhere, which is the wrong reading of
# "no release is published". This placeholder is that reading, said out loud,
# and it declares no lovspor_release var, so `lovspor release live` answers
# `none` until the first envelope is committed. Written only when absent: from
# then on this path is the live release's own fragment.
if [ ! -f /etc/caddy/lovspor-release.caddy ]; then
	cat >/etc/caddy/lovspor-release.caddy <<'FRAGMENT'
handle {
	respond "lovspor: no release published yet" 503
}
FRAGMENT
fi
# ADR-0014 release envelopes; the build runs as the app user, Caddy only reads.
# There is no flat site root: the site is built into the release beside the
# corpus and served from it.
install -d -o "$APP_USER" -g "$APP_USER" -m 755 /var/www/lovspor-releases
systemctl daemon-reload
# mcp stays enable-only (it refuses to start until a credential is issued at go-live);
# the timers are enabled AND started now (enable alone won't activate them this boot).
# The drift check fails hourly until the first release publishes the document it
# compares against — a visible, truthful state, not a reason to leave it off.
systemctl enable lovspor-mcp.service >/dev/null
systemctl enable --now lovspor-fetch-corpus.timer >/dev/null
systemctl enable --now lovspor-site-drift.timer >/dev/null

log "Base provisioning complete. Finish going live (see README.md § Go live):"
cat <<EOF

  1. OpenAI key:  sudo nano $ENV_FILE            # set OPENAI_API_KEY=...
  2. Beta token:  sudo -u $APP_USER $APP_DIR/.venv/bin/lovspor tokens issue --label "you@beta"
  3. Probe token: sudo -u $APP_USER $APP_DIR/.venv/bin/lovspor tokens issue --label site-probe --expires-in-days 30
                  sudo sh -c 'umask 077 && printf "%s\n" "<that token>" > /etc/lovspor/credentials/site-probe'
                  # then set its tiny limits — docs/mcp.md § Release probe and drift check
  4. Domain:      echo 'LOVSPOR_DOMAIN=lovspor.yourdomain.com' | sudo tee /etc/default/caddy-lovspor
  5. DNS:         point an A record for that domain at this droplet's public IP.
  6. Start:       sudo systemctl restart caddy lovspor-mcp
  7. Verify:      curl -fsS https://lovspor.yourdomain.com/healthz && echo ' OK'
  8. First site:  sudo systemctl start lovspor-publish    # until it runs, / answers 503
EOF
