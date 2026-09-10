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

## The site is part of the release

Everything Caddy serves outside `/mcp` — the landing page, `/observatory/`,
`/status/`, the EN twins — is built by `lovspor build-site` from the source
checkout and released **together with the corpus** as one envelope (ADR-0014
Decision 6, below). There is no separate site deploy and nothing to rsync: a
site-source change is a release, made live by the same configuration swap as
a corpus update. A box that has not published yet has no site at all: it serves
the placeholder release fragment `provision.sh` writes, a 503 saying so, until
the first envelope is committed — from then on the release's own `site/` tree
is what is served.

Public SSH is firewalled off the droplet by design, so the public IPv4 that
serves the site will time out on port 22. Reach the box over the tailnet
(`root@<DROPLET_TAILSCALE_IP>`), never the public address.

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

**Publish a release** (ADR-0014 Decision 6, refining ADR-0013) — corpus and
site as one envelope, made live by one Caddy configuration swap:

```bash
sudo systemctl start lovspor-publish          # HEAD of the corpus clone
sudo journalctl -u lovspor-publish -n 60 --no-pager
```

The unit runs `publish-release.sh`, a thin wrapper over `lovspor release`;
the logic and every crash case live in `src/lovspor/release/`, unit-tested.
In order: `lovspor release live` (root) establishes the **reconciled live
release** — Caddy's running configuration read from its admin socket (R),
the configuration adapted from `/etc/caddy/Caddyfile` with the active
fragment (D) and the marker `/var/www/lovspor-releases/ACTIVE` (M) must name
one release with equal configuration hashes; an unreconciled host, or one
whose admin socket cannot be reached, stops here. `lovspor release build`
(as `lovspor`) runs the release probe, computes the candidate's
`release_key` and stops with "already live" when it equals the live
release's; otherwise it builds `corpus/` and `site/` under
`/var/www/lovspor-releases/.build-<random>/`, hard-links unchanged files to
the live release (old mtimes, truthful `ETag`/`Last-Modified`), computes
`release_content_id` over both trees, writes `site-facts.json`'s id,
`release.json` and `release.caddy` (the per-release Caddy fragment), runs the
final `lovspor publish-check` on the temporary directory, and only then
renames it to `/var/www/lovspor-releases/<release_content_id>/` — so nothing
under an id name is ever incomplete. `lovspor release commit <id>` (root)
stages the release's fragment as `/etc/caddy/lovspor-release.caddy.next`,
runs `caddy validate` and `caddy adapt` on the composed configuration,
renames the fragment into place, `systemctl reload caddy`, reads the running
configuration back, and only then writes the marker. A reload failure puts
the previous release's fragment back and exits non-zero; the live release is
untouched throughout. `~4 min` to build ~93k pages, then the link pass and a
hash of every page.

Rollback is the previous release's own fragment through the same transaction
— the marker's `previous` is kept for exactly this; run it twice to roll
forward:

```bash
sudo /opt/lovspor/app/deploy/digitalocean/publish-release.sh --rollback
```

Publish a specific corpus commit, see what is live, or resolve a state a
crash left (`reconcile` names it — R, D and M — and offers `--complete` or
`--abandon` when there is a choice):

```bash
sudo /opt/lovspor/app/deploy/digitalocean/publish-release.sh --ref <sha>
sudo /opt/lovspor/app/deploy/digitalocean/publish-release.sh --reconcile
sudo /opt/lovspor/app/deploy/digitalocean/publish-release.sh --reconcile --complete
sudo /opt/lovspor/app/deploy/digitalocean/publish-release.sh --prune
curl -fsS https://lovspor.no/lov/ | head -c 300
```

`prune` runs only on a reconciled host and never removes the release named
by R, D, the marker's `active` or its `previous`. The commands read
`LOVSPOR_RELEASES_ROOT`, `LOVSPOR_CADDYFILE`, `LOVSPOR_RELEASE_FRAGMENT` and
`LOVSPOR_CADDY_ADMIN` (`unix//run/caddy/admin.sock`; TCP only during the
migration) — the script exports the droplet's values.

On a box provisioned before this existed, install the unit and the releases
root once — `provision.sh` does this on a fresh box, but is not re-run on a
live one:

```bash
sudo install -m644 /opt/lovspor/app/deploy/digitalocean/lovspor-publish.service /etc/systemd/system/
sudo install -d -o lovspor -g lovspor -m 755 /var/www/lovspor-releases
sudo systemctl daemon-reload
```

The Caddyfile is deliberately not in that list. This repository's
`deploy/digitalocean/Caddyfile` binds Caddy's admin API to
`unix//run/caddy/admin.sock|0660` and serves everything outside `/mcp` through
`import {$LOVSPOR_RELEASE_FRAGMENT:/etc/caddy/lovspor-release.caddy}`; it names
no release directory and no symlink of its own. Copying it into `/etc/caddy`
by hand on a box that has not migrated fails two ways at once: the import has
nothing to import until a fragment exists, and a `systemctl reload caddy`
through the stock unit line would move the admin endpoint to a socket while
the release commands are still addressing TCP. Putting it in place is the
first migration (ADR-0014 Migration), which `lovspor release migrate` performs
in the ADR's order — preflight, the fragment staged, the new file and the
runtime directory installed, the reload delivered explicitly to `--address
localhost:2019`, verified over the socket, the `ExecReload=` pair last — with
`lovspor release migrate --check` as the dry run and `--rollback` as the way
back. Until a box has been migrated, `lovspor release commit` refuses there
with "does the Caddyfile import the fragment?" and nothing public changes.

The unit `Conflicts=` with `lovspor-fetch-corpus.service`: a build must not read
a clone mid-fetch. There is no timer yet — publishing is an operator command
until the first releases have shown what a rebuild costs on this box.

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
