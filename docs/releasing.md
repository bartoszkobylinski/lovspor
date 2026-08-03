# Releasing `lovspor` to PyPI

> **Status (2026-08-03): publishing resumed — Lovspor is distributed on PyPI.**
> PyPI releases were suspended 2026-07-14 during the commercial pivot and the
> then-published versions were removed; the open-infrastructure decision
> ([`decisions.md`](decisions.md) §15) supersedes that pivot. `0.4.0` published
> on 2026-08-03 and is live at
> [pypi.org/project/lovspor](https://pypi.org/project/lovspor/); `README.md` and
> [`mcp.md`](mcp.md) state the same, and the three must stay in step (an
> invariant test in `tests/unit/test_release_workflow.py` fails if they drift).
> One lasting consequence of the removal:
>
> **Versions `0.2.0`–`0.3.0` are permanently burned.** PyPI never allows a
> filename to be reused, even after a project is deleted. The re-release
> therefore started at `0.4.0`, and no burned version can ever be re-published.

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

## One-time setup (done — registered as a pending publisher, live since `0.4.0`)

Because the project did not exist on PyPI, Trusted Publishing was registered in
advance as a **pending publisher** (pypi.org → **Account settings** →
**[Publishing](https://pypi.org/manage/account/publishing/)**). Publishing
`0.4.0` created the project and converted it into an ordinary trusted publisher.

The registered identity — change any of these and publishing breaks, so keep the
workflow, its `environment:` block, and this list in agreement:

- **PyPI Project Name:** `lovspor`
- **Owner:** `bartoszkobylinski`
- **Repository name:** `lovspor`
- **Workflow name:** `release.yml`
- **Environment name:** `pypi`

The matching GitHub **Environment** `pypi` (**Settings → Environments**) carries:

- the owner under **Required reviewers** — the manual approval gate between
  "Release published" and "package goes live", which matters because a PyPI
  version can never be reused once published;
- **Deployment branches and tags** restricted to tags matching `v*` (and/or
  branch `main`), so no other ref can reach the publish job.

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

The project page's long description is a **snapshot of `README.md` taken at
build time** and is immutable for that version, so a README change reaches PyPI
only with the next upload. Editing docs after a release therefore leaves the
published page behind until then ([#6](https://github.com/bartoszkobylinski/lovspor/issues/6)).

Step 5 of the first release — swapping the "pending" wording in this file,
`README.md` and [`mcp.md`](mcp.md) for the distributed-on-PyPI wording — was
carried out on 2026-08-03 and does not recur. The release-state invariant in
`tests/unit/test_release_workflow.py` still holds the three files to one shared
answer, so they cannot drift apart on a later transition either.

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
