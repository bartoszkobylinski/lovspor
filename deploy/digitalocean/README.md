# Deploy: lovspor hosted MCP on a DigitalOcean droplet

The named blocker for the hosted MCP is **TLS** (the transport is plaintext, so a
bearer token on an open port is cleartext on the wire). This recipe terminates TLS
in **Caddy** in front of the localhost-bound app — matching the documented
"TLS terminated upstream" design — and Caddy obtains + renews the Let's Encrypt
certificate automatically.

```
Internet ──HTTPS/443──▶ Caddy (auto Let's Encrypt) ──HTTP──▶ 127.0.0.1:8000
                         TLS terminates here            lovspor mcp-http
                                                        (bearer auth + quotas)
```

## Why a 2 GB droplet

Measured on the real corpus (85,426 sections × 3072-dim int8 embeddings):
**~672 MB steady-state, ~1.24 GB peak during startup warm.** The **$12 / 2 GB**
plan fits both with headroom; 1 GB dies on the warm peak. See the memory cap in
`lovspor-mcp.service`. Re-measure with `deploy/digitalocean/../../` tooling if the
corpus grows a lot (linear in section count) — that's your cue to move to 4 GB.

---

## 1. Create the droplet (your account, your card — this step is yours)

**Console:** Create → Droplet → Ubuntu 24.04 (LTS) → **Basic / Regular / $12 (2 GB /
1 vCPU / 50 GB)** → region **`fra1`** (Frankfurt, EU) → add your SSH key → Create.

**Or `doctl`** (after `doctl auth init` with your token):

```bash
doctl compute ssh-key list            # copy your key's fingerprint
doctl compute droplet create lovspor-mcp \
  --region fra1 \
  --image ubuntu-24-04-x64 \
  --size s-1vcpu-2gb \
  --ssh-keys <YOUR_SSH_KEY_FINGERPRINT> \
  --wait
doctl compute droplet get lovspor-mcp --format PublicIPv4 --no-header   # note the IP
```

## 2. Provision the box

Copy the bootstrap up and run it. It is **idempotent** and runs in **two passes**
because the lovspor repo is private (`provision.sh` is self-contained — it doesn't
need the repo cloned to start):

```bash
# from your Mac:
scp deploy/digitalocean/provision.sh root@<DROPLET_IP>:/root/
ssh root@<DROPLET_IP> 'bash /root/provision.sh'
```

**Pass 1** installs swap + packages + Caddy + `uv`, creates the `lovspor` user, and
prints a **read-only deploy key**. Add it here:
`https://github.com/bartoszkobylinski/lovspor/settings/keys` (Deploy keys → Add,
read-only). Then run **pass 2** — note the `ssh` one-liner above already returned you
to your Mac, so SSH back in:

```bash
# from your Mac, after adding the deploy key on GitHub:
ssh root@<DROPLET_IP> 'bash /root/provision.sh'   # clones the app, uv sync, fetches
                                                  # the corpus, installs units + Caddyfile
```

## 3. Go live

```bash
# 1. OpenAI key (enables semantic_search; the other 15 tools work without it)
sudo nano /etc/lovspor/lovspor.env          # set OPENAI_API_KEY=sk-...
#    The same file carries the OPTIONAL self-service OAuth pair (commented out by
#    default). Give BOTH a value or NEITHER — one alone and lovspor-mcp exits on
#    start (present-but-empty counts as unset, i.e. opaque-token mode):
#      LOVSPOR_AUTHKIT_DOMAIN=https://your-project.authkit.app
#      LOVSPOR_PUBLIC_URL=https://lovspor.yourdomain.com/mcp   <- must match step 3

# 2. Issue a beta credential — the token prints ONCE, store it now
sudo -u lovspor /opt/lovspor/app/.venv/bin/lovspor tokens issue --label "you@beta"

# 3. Your hostname
echo 'LOVSPOR_DOMAIN=lovspor.yourdomain.com' | sudo tee /etc/default/caddy-lovspor

# 4. DNS: create an A record   lovspor.yourdomain.com -> <DROPLET_IP>
#    (Caddy grabs the cert automatically once this resolves.)

# 5. Start
sudo systemctl restart caddy lovspor-mcp

# 6. Verify
curl -fsS https://lovspor.yourdomain.com/healthz && echo ' OK'
```

## Update the landing pages

`deploy/digitalocean/site/` is the source for everything Caddy serves outside
`/mcp`. Provisioning copies the whole tree, so adding a page is a matter of
adding a file — no config change.

Between provisions, copy the changed pages straight over:

```bash
# from your Mac, in the repo root:
rsync -av --delete deploy/digitalocean/site/ root@<DROPLET_IP>:/var/www/lovspor/
```

Caddy serves from disk, so the change is live immediately — nothing to reload.

`/observatory/` is not decoration: the crawler's User-Agent advertises that
address to every site it visits, so it has to answer. A site administrator who
finds `lovspor-observatory` in their logs should land on a page telling them
what it does and how to block it.

## Connect a client

```
URL:    https://lovspor.yourdomain.com/mcp
Header: Authorization: Bearer <the token from step 2>
```

If you enabled the OAuth pair in step 1, chat-app connectors (ChatGPT, Claude.ai) can
instead add that same URL and log in through WorkOS — no token to paste. Confirm the
server is advertising it before pointing a connector at it:

```bash
curl -fsS https://lovspor.yourdomain.com/.well-known/oauth-protected-resource/mcp
# 200 + a JSON body naming your AuthKit domain => hosted OAuth is live.
# 404 => the pair is not set; the server is in opaque-token mode (paste-a-token only).
```

Hand-issued `lsp_…` tokens keep working either way — see
[`docs/mcp.md` § Authentication](../../docs/mcp.md#authentication-two-modes).

---

## Operating it

**Deploy an update** (after merging to `main`):

```bash
sudo -u lovspor git -C /opt/lovspor/app pull --ff-only
sudo -u lovspor sh -c 'cd /opt/lovspor/app && /opt/lovspor/.local/bin/uv sync --frozen --no-dev'
sudo systemctl restart lovspor-mcp
sudo journalctl -u lovspor-mcp -n 40 --no-pager
```

**Corpus refresh** is automatic — `lovspor-fetch-corpus.timer` runs daily at
05:30 UTC and the running server picks up changes on the next query (no restart).
Force one now: `sudo systemctl start lovspor-fetch-corpus`.

**Full git history is required** on this box: the hosted MCP exposes the
time-machine tools, so the fetch units run `fetch-corpus --full-history`
(~2.2 GB total). A shallow checkout would limit `get_law_at` /
`diff_law_versions` to post-provisioning dates (ADR-0003). Deepen a legacy
shallow checkout with: `sudo -u lovspor git -C /opt/lovspor/.cache/lovverk fetch --unshallow`.

**Logs / health:**

```bash
sudo journalctl -u lovspor-mcp -f                 # app
sudo tail -f /var/log/caddy/lovspor.log           # proxy / TLS
curl -fsS https://lovspor.yourdomain.com/readyz   # corpus present + reader ready
sudo systemctl status lovspor-mcp caddy
```

**Revoke a credential** — the server re-reads the store live:

```bash
sudo -u lovspor /opt/lovspor/app/.venv/bin/lovspor tokens list
sudo -u lovspor /opt/lovspor/app/.venv/bin/lovspor tokens revoke <id>
```

**Rollback** (emergency, on-box — detached HEAD is expected and temporary):

```bash
sudo -u lovspor git -C /opt/lovspor/app checkout <good-sha>
sudo -u lovspor sh -c 'cd /opt/lovspor/app && /opt/lovspor/.local/bin/uv sync --frozen --no-dev'
sudo systemctl restart lovspor-mcp
# return to the tip once the fix is in — leaves a clean, trackable branch for future pulls:
sudo -u lovspor git -C /opt/lovspor/app checkout main
sudo -u lovspor git -C /opt/lovspor/app pull --ff-only
```

The durable fix for a bad release is `git revert` on `main` + redeploy, not a
long-lived detached checkout.

---

## What was verified locally vs. on the droplet

Built and checked on a dev machine before any droplet exists:

- ✅ `lovspor mcp-http` (the exact command the unit runs) **warms, binds, and
  serves**; `/healthz` + `/readyz` return 200; the `/mcp` surface **401s without a
  bearer token and accepts a valid one** (auth + quota enforcement is live).
- ✅ Memory figures above are measured, not estimated.

Verified only on the live droplet (the well-trodden last mile):

- Let's Encrypt issuance (needs public IP + DNS), `apt` installs on fresh Ubuntu,
  systemd activation. Caddy's automatic HTTPS makes this the least fragile part.

## Notes

- **nginx + certbot alternative:** if you'd rather match your other boxes, drop the
  Caddyfile and put the app behind an nginx vhost with a certbot (`--nginx`) cert
  for the same `proxy_pass http://127.0.0.1:8000`. Caddy is the default here purely
  because auto-HTTPS on a clean public droplet is one file and zero cron.
- **Secrets never enter git.** `/etc/lovspor/lovspor.env` holds `OPENAI_API_KEY`;
  the credential store lives at `/opt/lovspor/.config/lovspor/credentials.json`.
- **Cost:** $12/mo droplet + $0 TLS. Egress for a text MCP stays well under any cap.
