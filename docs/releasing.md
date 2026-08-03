# Releasing `lovspor` to PyPI

> **Status (2026-08-03): publishing resumed.** PyPI releases were suspended
> 2026-07-14 during the commercial pivot and the then-published versions were
> removed; the open-infrastructure decision ([`decisions.md`](decisions.md) §15)
> supersedes that pivot, and this release process is live again. Two lasting
> consequences of the removal:
>
> 1. **The PyPI project `lovspor` no longer exists** (the page 404s), so the
>    Trusted Publishing registration must be redone as a **pending publisher**
>    before the next release — see the one-time setup below.
> 2. **Versions `0.2.0`–`0.3.0` are permanently burned.** PyPI never allows a
>    filename to be reused, even after the project is deleted. The first
>    re-release is therefore `0.4.0`, and no burned version can ever be
>    re-published.

`lovspor` publishes to [PyPI](https://pypi.org/project/lovspor/) via **Trusted
Publishing** (OpenID Connect). GitHub Actions mints a short-lived OIDC token that
PyPI trusts for this exact repo + workflow — **no API token is ever stored** in
the repo, in Actions secrets, or in anyone's `~/.pypirc`. The publish pipeline
lives in [`.github/workflows/release.yml`](../.github/workflows/release.yml) and
runs only when a GitHub Release is *published*: a `build` job builds and
validates the sdist + wheel, then a separate `pypi-publish` job — the only one
with `id-token: write`, gated on the `pypi` GitHub environment — uploads them
with the official [`pypa/gh-action-pypi-publish`](https://github.com/pypa/gh-action-pypi-publish)
action. Ordinary pushes and PRs never trigger it.

## One-time setup (must be redone — the old registration died with the project)

Trusted Publishing has to be registered on PyPI **before** the project exists —
this is a "pending publisher". You need a PyPI account; no token, no `twine`.

1. Sign in at [pypi.org](https://pypi.org) → **Account settings** →
   **[Publishing](https://pypi.org/manage/account/publishing/)**.
2. Under **"Add a new pending publisher"** (GitHub tab), fill in exactly:
   - **PyPI Project Name:** `lovspor`
   - **Owner:** `bartoszkobylinski`
   - **Repository name:** `lovspor`
   - **Workflow name:** `release.yml`
   - **Environment name:** `pypi`
3. Save.

Note: a pending publisher only expresses intent — the project name is not
reserved until the first release actually publishes.

In the GitHub repo, create the matching **Environment** named `pypi`
(**Settings → Environments → New environment**):

- Add yourself under **Required reviewers** — this is the manual approval gate
  between "Release published" and "package goes live". Recommended and assumed
  by this process, since a PyPI version can never be reused once published.
- **Deployment branches and tags** → restrict to tags matching `v*` (and/or
  branch `main`), so no other ref can ever reach the publish job.

The environment name in the workflow (`pypi`) must match both the PyPI
pending-publisher form and the GitHub environment.

## Cutting a release

1. **Bump the version** in `pyproject.toml` (`[project] version`) on a branch,
   e.g. `0.4.0` → `0.4.1`, then **re-lock** with `uv lock` and commit both
   `pyproject.toml` and `uv.lock` (the lockfile pins the package's own version;
   CI runs `uv sync --frozen` and fails if they disagree). `__version__` is
   derived from package metadata, so there is no second copy to edit. Open a PR,
   get the Codex pass, merge to `main`. (Versioning is [SemVer](https://semver.org/):
   breaking → major, features → minor, fixes → patch. Pre-1.0, minor is fine for
   anything.)
2. **Create the GitHub Release.** Tag `v<version>` (the leading `v` is stripped;
   `v0.4.0` → package `0.4.0`). Target `main` at the merged bump commit. Write
   release notes. Publish.
3. The **Release** workflow runs: the `build` job runs `uv build`, asserts the
   built version equals the tag, and validates metadata with `twine check`;
   then the `pypi-publish` job **waits for your approval on the `pypi`
   environment** and, once approved, uploads over OIDC. If the tag and the
   `pyproject.toml` version disagree, the build fails *before* anything can
   publish.
4. **Verify:** the package appears at
   [pypi.org/project/lovspor](https://pypi.org/project/lovspor/), and a clean
   `uvx lovspor@<version> mcp --help` resolves it from PyPI.

Publishing never happens merely because `main` changed — it takes an explicit
GitHub Release by the owner *and* an approval on the `pypi` environment.

## Why Trusted Publishing (and not an API token)

- **Nothing to leak.** There is no long-lived secret in the repo or CI. The OIDC
  token is minted per run and expires in minutes.
- **Nothing to rotate.** No token expiry to track, no revoke-and-reissue dance.
- **Scoped.** PyPI only accepts a token from this repo, this workflow file, this
  environment — a fork or a different workflow cannot publish `lovspor`.

See the [PyPI Trusted Publishers guide](https://docs.pypi.org/trusted-publishers/)
for the underlying mechanics.
