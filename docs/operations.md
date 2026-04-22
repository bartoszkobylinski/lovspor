# Operations

## Local runs

### Implemented

```bash
./scripts/bootstrap.sh         # one-time setup (uv sync + pre-commit install + checks)
uv run lovspor --help          # show CLI usage
uv run lovspor --version       # show version
uv run lovspor info            # show project info
```

### Planned (not yet implemented)

```bash
uv run lovspor seed            # initial corpus population
uv run lovspor sync            # incremental update against latest tarballs
uv run lovspor render          # re-render Markdown from cached XML
uv run lovspor validate        # validate manifest + front matter consistency
uv run lovspor stats           # show corpus counts and last sync timestamp
```

Each will land in its own PR.

## Scheduled runs (planned)

A GitHub Actions cron will run `lovspor sync` daily after Lovdata's nightly tarball drop (target: 06:00 CET). Workflow file `.github/workflows/sync.yml` will be added once `lovspor sync` is functional.

## Recovery

(To be defined when the sync pipeline exists.)

## Idempotency

`lovspor sync` will be idempotent — running twice on the same upstream state must result in zero file changes and zero git commits. Enforced by tests.
