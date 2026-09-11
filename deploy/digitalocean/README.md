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

Copy the bootstrap up and run it. It runs in **two passes** because the lovspor
repo is private (`provision.sh` is self-contained — it doesn't need the repo
cloned to start). It is re-runnable on a box it provisioned, and **refuses** one
that is already serving from the pre-envelope layout — see the note under
[First migration](#first-migration-once-on-the-existing-droplet):

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

Restart and release are independent, and neither waits for the other. A release
run before the restart is not refused: it publishes `runtime_tree_match: false`
or `environment_match: false`, every `/connect/<client>/` page withholds its
hosted procedures naming that comparison, and the log says **restart
`lovspor-mcp`, then release again**. A restart after a pull without `uv sync` is
`environment_match: false`, and the log says **run `uv sync --frozen --no-dev`,
restart, then release again**. A site-only change needs no restart at all and
moves no comparison. These are advice, not ordered steps.

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

This repository's `deploy/digitalocean/Caddyfile` binds Caddy's admin API to
`unix//run/caddy/admin.sock|0660` and serves everything outside `/mcp` through
`import {$LOVSPOR_RELEASE_FRAGMENT:/etc/caddy/lovspor-release.caddy}`; it names
no release directory and no symlink of its own. Copying it into `/etc/caddy`
by hand on a box that has not migrated fails two ways at once: the import has
nothing to import until a fragment exists, and a `systemctl reload caddy`
through the stock unit line would move the admin endpoint to a socket while
the release commands are still addressing TCP. Putting it in place is the
first migration below. Until a box has been migrated, `lovspor release commit`
refuses there with "does the Caddyfile import the fragment?" and nothing
public changes.

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
sudo curl --unix-socket /run/caddy/admin.sock http://localhost/config/ | head -c 200; echo
```

That last line is Caddy's admin API, and it is the only way to ask what Caddy is
actually serving. Since the migration it answers on a Unix socket owned by the
release group, so it needs `sudo` (or membership of `lovspor-release`) and the
`--unix-socket` flag: **`curl localhost:2019` no longer answers anything**, on
purpose — on that port every process on the box, `User=lovspor` included, could
rewrite the running configuration without touching a file.

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

## First migration (once, on the existing droplet)

The droplet provisioned before the release envelope existed serves the corpus
through the symlink `/var/www/lovspor-current` and the landing pages from
`/var/www/lovspor`, with Caddy's admin API on the default `localhost:2019`.
Moving it onto the envelope is **one operator-run procedure, once** (ADR-0014
Migration): the ordinary release with one extra step, the new Caddyfile, and one
difference of address — the load that installs the new configuration is the load
that moves the admin endpoint onto the socket, so it can never be `systemctl
reload caddy` (the stock line derives the address from the file it hands over and
would reach nothing). It is `caddy reload --address localhost:2019`, delivered
explicitly to the endpoint that is still running, and its reverse is the previous
Caddyfile delivered explicitly to the socket.

`lovspor release migrate` performs it in the ADR's order — preflight, the
fragment staged, the previous Caddyfile kept as `/etc/caddy/Caddyfile.pre-envelope`,
the new file and the runtime directory installed, `caddy validate`, the reload to
`--address localhost:2019`, verification over the socket, the `ExecReload=` pair
**last** — and records every checkpoint, so a crash is resolved by `reconcile`
rather than by hand.

**Do not run `provision.sh` on the live droplet.** It writes the whole drop-in,
`ExecReload=` pair included, which on a box still answering on TCP points every
`systemctl reload caddy` at a socket that does not exist yet — and it installs
the socket-admin Caddyfile over the live one with no `.pre-envelope` backup, so
the next `systemctl restart caddy` serves the 503 placeholder instead of the
site. That two-phase split is the entire reason the migration is a command and
not a copy.

The script refuses this itself: `/etc/caddy/Caddyfile` present and
`/var/www/lovspor-releases/ACTIVE` absent is a box that has never published
through the envelope, and it exits 1 naming `lovspor release migrate`. The one
legitimate re-run that rule also catches — a box this script provisioned that
has not published yet, e.g. pass 2 interrupted — is
`LOVSPOR_PROVISION_FORCE=1 sudo -E bash provision.sh`.

Reach the box over the tailnet (`root@<DROPLET_TAILSCALE_IP>`); public SSH is
firewalled off. All commands below run as root unless they say otherwise.

### 1. Read the box first (changes nothing)

```bash
caddy version
systemctl show caddy -p ExecReload -p User -p Group
curl -fsS localhost:2019/config/ | head -c 200; echo    # TCP answers today
ls -la /run/caddy 2>&1                                  # must not exist yet, or be empty
cat /etc/systemd/system/caddy.service.d/lovspor.conf 2>&1   # absent, or the two lines below
ls -l /etc/systemd/system/caddy.service.d/              # no leftover *.pre-envelope backup
ls -ld /var/www/lovspor-current /var/www/lovspor; ls /var/www/lovspor-releases/ 2>&1
df -h /var/www
systemctl status lovspor-publish --no-pager | head -5
date -u    # stay clear of 05:30 UTC (corpus fetch) and minute :17 (drift timer)
```

The migration overwrites that drop-in and its rollback puts back the bytes it
found, kept as `lovspor.conf.pre-envelope` beside it (systemd reads `*.conf` out
of a drop-in directory and nothing else, so the backup is invisible to the unit).
Absence is a state too: a box with no drop-in gets none back. The preflight
refuses anything that is neither absent nor exactly

```
[Service]
EnvironmentFile=/etc/default/caddy-lovspor
```

so an edited drop-in is a decision for you, not a guess for the code: move it
aside and re-run.

The first envelope has no live release to hard-link against, so its `corpus/`
tree is a full second copy beside the old flat releases until step 8 retires
them — that is what `df -h /var/www` is for.

### 2. Deploy the app and install what provisioning would have

```bash
sudo -u lovspor git -C /opt/lovspor/app pull --ff-only
sudo -u lovspor sh -c 'cd /opt/lovspor/app && /opt/lovspor/.local/bin/uv sync --frozen --no-dev'
sudo -u lovspor git -C /opt/lovspor/app status --porcelain    # must print nothing: a build refuses a dirty tree
sudo install -m644 /opt/lovspor/app/deploy/digitalocean/lovspor-publish.service /etc/systemd/system/
sudo install -d -o lovspor -g lovspor -m 755 /var/www/lovspor-releases
sudo systemctl daemon-reload
```

### 3. Restart the MCP, so the first site describes the process that is running

```bash
sudo systemctl restart lovspor-mcp
sudo journalctl -u lovspor-mcp -n 40 --no-pager
curl -fsS http://127.0.0.1:8000/readyz | head -c 400; echo
```

Not a precondition — a release before the restart publishes
`runtime_tree_match: false` and says so — but doing it first is one fewer
comparison to explain on the first public page.

### 4. The socket's group, then the preflight

```bash
sudo groupadd --system --force lovspor-release
sudo usermod -aG lovspor-release root
sudo /opt/lovspor/app/.venv/bin/lovspor release migrate --check
```

`--check` moves nothing. It prints one line — the running configuration on
`localhost:2019` and its hash, the socket confirmed absent, the group and its
gid, and that this Caddy accepts the `|0660` creation-mode suffix — and refuses,
naming the reason, if the marker already exists, the socket is already there, a
backup Caddyfile or drop-in backup is already in place, the drop-in is neither
absent nor the pre-envelope one, a file already sits at the release fragment's
name or its `.next`, `/run/caddy` exists with anything in it or is not a
directory, Caddy is unreachable on TCP, the running configuration already names
a release, or the adapted configuration on disk does not match the running one.
Exit 0 or **stop here**; nothing has moved.

### 5. Build and cut over

```bash
sudo /opt/lovspor/app/deploy/digitalocean/publish-release.sh --migrate
```

About five minutes: ~4 min to render ~93k pages, then the link pass and a hash of
every page, then the cutover. It prints the three report lines —
`migrated: <id> (admin unix//run/caddy/admin.sock)`, the running configuration,
and the path of the kept previous Caddyfile.

This run has **no probe credential** (that is delivered by
`lovspor-publish.service`'s `LoadCredential=`, and this is a manual run), so the
first `deployment-capabilities.json` records `hosted_state: unknown` with
`probe_credential_missing`. That is a truthful reading, not a failure: the site
says "not attested (unobserved: …)" rather than claiming anything. Step 7
replaces it with a document a credentialed observation produced.

If the reload at the end is refused, the new Caddyfile is on disk and Caddy is
still running the old one on TCP: nothing public has changed, and the two ways
out are `publish-release.sh --reconcile --complete` (finish the cutover) and
`--reconcile --abandon` (put the previous Caddyfile back; no reload).

### 6. Verify

```bash
sudo /opt/lovspor/app/.venv/bin/lovspor release live
sudo /opt/lovspor/app/deploy/digitalocean/publish-release.sh --reconcile
sudo curl -fsS --unix-socket /run/caddy/admin.sock http://localhost/config/ | head -c 200; echo
curl -sS localhost:2019/config/; echo "exit=$? (connection refused is the pass)"
sudo stat -c '%a %U:%G %n' /run/caddy /run/caddy/admin.sock
sudo -u lovspor curl -sS --unix-socket /run/caddy/admin.sock http://localhost/config/; echo "exit=$? (permission denied is the pass)"
systemctl show caddy -p ExecReload      # must name --address unix//run/caddy/admin.sock
cat /var/www/lovspor-releases/ACTIVE
curl -fsS https://lovspor.no/ | head -c 300; echo
curl -fsS https://lovspor.no/observatory/ | head -c 300; echo
curl -fsS https://lovspor.no/lov/ | head -c 300; echo
curl -fsS https://lovspor.no/site-manifest.json | head -c 200; echo
curl -fsS https://lovspor.no/deployment-capabilities.json | head -c 400; echo
curl -fsSI https://lovspor.no/mcp | head -3
sudo tail -n 20 /var/log/caddy/lovspor.log
```

Four facts must hold: the socket exists, mode `660`, group `lovspor-release`;
root can read `/config/` through it; `lovspor` cannot; nothing answers on TCP.
Then prove the steady state — the ordinary reload path, and a restart, which
recreates the socket through `RuntimeDirectory=`:

```bash
sudo systemctl reload caddy && sudo /opt/lovspor/app/.venv/bin/lovspor release live
sudo systemctl restart caddy && sleep 2 && sudo stat -c '%a %U:%G %n' /run/caddy/admin.sock
sudo /opt/lovspor/app/.venv/bin/lovspor release live
```

A restart drops open connections for about a second, `/mcp` included — pick a
quiet minute.

### 7. The probe credential, then the first unit-driven release

```bash
sudo -u lovspor /opt/lovspor/app/.venv/bin/lovspor tokens issue --label site-probe --expires-in-days 30
# the lsp_… token prints ONCE — copy it now, and note the credential id
sudo install -d -m 700 -o root -g root /etc/lovspor/credentials
sudo sh -c 'umask 077 && printf "%s\n" "lsp_…" > /etc/lovspor/credentials/site-probe'
sudo stat -c '%a %U:%G %n' /etc/lovspor/credentials/site-probe   # 600 root:root
```

Then set that record's tiny limits — `max_in_flight` 1, `rate_per_minute` 6,
`rate_burst` 3, `daily_quota` 100, `paid_daily_quota` 1 — in
`/opt/lovspor/.config/lovspor/credentials.json`; the server re-reads the store on
change. Full rationale:
[`docs/mcp.md` § Release probe and drift check](../../docs/mcp.md#release-probe-and-drift-check).

```bash
sudo systemctl start lovspor-publish
sudo journalctl -u lovspor-publish -n 60 --no-pager
```

The observation now carries an authenticated call through the public path, so the
document's `state` — and with it the candidate's `release_key` — differs from the
first envelope's and the unit publishes a second one through the ordinary
transaction: `systemctl reload caddy` through the `ExecReload=` line, no
migration involved. That is the proof the steady state works end to end.

Install and enable the drift check in the same operator run:

```bash
sudo install -m644 /opt/lovspor/app/deploy/digitalocean/lovspor-site-drift.service /etc/systemd/system/
sudo install -m644 /opt/lovspor/app/deploy/digitalocean/lovspor-site-drift.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start lovspor-site-drift.service
sudo journalctl -u lovspor-site-drift -n 10 --no-pager     # exit 0, no drift
sudo systemctl enable --now lovspor-site-drift.timer
```

### 8. Retire the pre-envelope layout — separately, and last

`--retire` removes `/var/www/lovspor-current`, `/var/www/lovspor`, the old flat
release directories and — last, once every one of those is gone — the two
backups the rollback restores from:
`/etc/systemd/system/caddy.service.d/lovspor.conf.pre-envelope` and, the very
last path of all, `/etc/caddy/Caddyfile.pre-envelope`. It refuses unless the
host is reconciled and the marker exists, and it is
deliberately **not** part of the migration: until it is run, step 9's rollback
is still a working way back to the old site. Run it only after steps 6 and 7
have passed, and say out loud that there is no way back afterwards except a new
envelope release.

Run it once without `--yes`. That removes nothing: it prints the exact paths and
exits 1. Read them, then re-run with `--yes`.

```bash
sudo /opt/lovspor/app/deploy/digitalocean/publish-release.sh --retire
# read the list, then:
sudo /opt/lovspor/app/deploy/digitalocean/publish-release.sh --retire --yes
ls -la /var/www/ /etc/caddy/
```

The confirmation is a flag, never a prompt: this runs unattended often enough
that a missing `--yes` must fail closed rather than depend on a terminal.

### 9. Rollback, at any point before step 8

Once step 8 has run there is no way back: it removes
`/etc/caddy/Caddyfile.pre-envelope` with everything that Caddyfile serves, and
`--migrate-rollback` refuses the moment that file is absent.

Before the cutover's reload succeeded, the way back is the file restore —
`publish-release.sh --reconcile --abandon`, or `--migrate-rollback`, which does
the same thing when it finds Caddy still on TCP. After it succeeded:

```bash
sudo /opt/lovspor/app/deploy/digitalocean/publish-release.sh --migrate-rollback
curl -fsS localhost:2019/config/ | head -c 100; echo    # TCP answers again
ls /run/caddy/ 2>&1                                     # the socket is gone
systemctl show caddy -p ExecReload                      # back to the stock line
```

It delivers `/etc/caddy/Caddyfile.pre-envelope` explicitly to the socket — the
previous Caddyfile has no global options block, so the stock reload line would
derive TCP and reach nothing — then removes the marker, puts the files back and
the `ExecReload=` pair with them, and leaves the admin endpoint on TCP. It
refuses once a second envelope release has happened (the marker has a
`previous`): that is `publish-release.sh --rollback`, the ordinary one. It also
refuses the moment either `.pre-envelope` backup is gone — after step 8 there
is no way back to the old site, only a new envelope release.

#### Last resort: Caddy answers on neither address

Every command above reads Caddy's running configuration first, so all of them
refuse with `precondition Caddy admin reachable unmet` (exit 3) on a box that
will not load the configuration on disk — no socket, and nothing on TCP either.
That is the state with the fewest ways out, so it has an explicit one:

```bash
sudo /opt/lovspor/app/.venv/bin/lovspor release migrate --rollback --offline
```

It dials nothing. It removes the marker, restores `/etc/caddy/Caddyfile` from
`/etc/caddy/Caddyfile.pre-envelope`, puts the drop-in back from its own backup
(absent if it was absent) and runs `systemctl restart caddy`, which loads the
file on disk whole. Then verify by
hand — it reports what it did, it does not observe the result:

```bash
systemctl status caddy --no-pager | head -5
curl -fsS localhost:2019/config/ | head -c 100; echo    # TCP answers again
curl -fsS https://lovspor.no/ | head -c 200; echo
```

If the CLI itself is what is broken — a bad `uv sync`, a checkout mid-pull —
the same two files, by hand:

```bash
sudo cp /etc/caddy/Caddyfile.pre-envelope /etc/caddy/Caddyfile && sudo systemctl restart caddy
```

That leaves the marker and the release fragment behind, so afterwards run
`lovspor release migrate --rollback --offline` (or delete
`/var/www/lovspor-releases/ACTIVE`) before attempting the migration again — a
marker with the pre-envelope Caddyfile serving is a state `migrate` refuses.

## Probe credential rotation

The `site-probe` token is issued with `--expires-in-days 30`. The hour after it
lapses, `lovspor-site-drift.service` fails with `probe_credential_rejected` and
`systemctl --failed` shows it — the check names it, but a lapse is still an
avoidable failed unit.

Record the issue date here when step 7 runs, and rotate before it:

| Issued (UTC) | Credential id | Due |
|---|---|---|
| _record at step 7_ | _record at step 7_ | issued + 30 days |

To rotate: issue a new token with the same label, write the file the same way,
revoke the old id with `lovspor tokens revoke <id>`, then release again — the
served document must be one the new credential produced, or the next drift check
compares against a document only the old one could have made. The full
contract is in
[`docs/mcp.md` § Release probe and drift check](../../docs/mcp.md#release-probe-and-drift-check).

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
