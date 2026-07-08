# Releasing `lovspor` to PyPI

`lovspor` publishes to [PyPI](https://pypi.org/project/lovspor/) via **Trusted
Publishing** (OpenID Connect). GitHub Actions mints a short-lived OIDC token that
PyPI trusts for this exact repo + workflow — **no API token is ever stored** in
the repo, in Actions secrets, or in anyone's `~/.pypirc`. The publish job lives
in [`.github/workflows/release.yml`](../.github/workflows/release.yml) and runs
only when a GitHub Release is *published*.

## One-time setup (do this once, before the first release)

Trusted Publishing has to be registered on PyPI **before** the project exists —
this is a "pending publisher". You need a PyPI account; no token, no `twine`.

1. Sign in at [pypi.org](https://pypi.org) → **Account settings** →
   **[Publishing](https://pypi.org/manage/account/publishing/)**.
2. Under **"Add a new pending publisher"**, fill in exactly:
   - **PyPI Project Name:** `lovspor`
   - **Owner:** `bartoszkobylinski`
   - **Repository name:** `lovspor`
   - **Workflow name:** `release.yml`
   - **Environment name:** `pypi`
3. Save.

Optionally, in the GitHub repo, create a matching **Environment** named `pypi`
(**Settings → Environments → New environment**). Add yourself as a *required
reviewer* if you want a manual approval gate between "Release published" and
"package goes live" — recommended, since a PyPI version can never be reused once
published.

That's it. The environment name in the workflow (`pypi`) must match both the
PyPI pending-publisher form and the GitHub environment.

## Cutting a release

1. **Bump the version** in `pyproject.toml` (`[project] version`) on a branch,
   e.g. `0.1.0` → `0.2.0`. Open a PR, get the Codex pass, merge to `main`.
   (Versioning is [SemVer](https://semver.org/): breaking → major, features →
   minor, fixes → patch. Pre-1.0, minor is fine for anything.)
2. **Create the GitHub Release.** Tag `v<version>` (the leading `v` is stripped;
   `v0.2.0` → package `0.2.0`). Target `main` at the merged bump commit. Write
   release notes. Publish.
3. The **Release** workflow runs automatically: `uv build` → assert the built
   version equals the tag → `uv publish` over OIDC. If the tag and the
   `pyproject.toml` version disagree, the job fails *before* publishing.
4. **Verify:** the package appears at
   [pypi.org/project/lovspor](https://pypi.org/project/lovspor/), and a clean
   `uvx lovspor@<version> mcp --help` resolves it from PyPI.

## After the first successful publish

Once `lovspor` is on PyPI, the MCP quickstart in `README.md` and `docs/mcp.md`
can drop the `--from git+https://github.com/bartoszkobylinski/lovspor.git` form
in favour of a plain `uvx lovspor mcp --corpus-path …` — versioned, immutable,
and immune to the `uvx`-from-git cache-staleness that the git form is prone to.

## Why Trusted Publishing (and not an API token)

- **Nothing to leak.** There is no long-lived secret in the repo or CI. The OIDC
  token is minted per run and expires in minutes.
- **Nothing to rotate.** No token expiry to track, no revoke-and-reissue dance.
- **Scoped.** PyPI only accepts a token from this repo, this workflow file, this
  environment — a fork or a different workflow cannot publish `lovspor`.

See the [PyPI Trusted Publishers guide](https://docs.pypi.org/trusted-publishers/)
and the [uv publishing docs](https://docs.astral.sh/uv/guides/package/#publishing-your-package)
for the underlying mechanics.
