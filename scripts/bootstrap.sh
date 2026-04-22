#!/usr/bin/env bash
set -euo pipefail

# Bootstrap the lovspor development environment.
# Run once after cloning. Idempotent.

cd "$(dirname "$0")/.."

if ! command -v uv >/dev/null 2>&1; then
    echo "ERROR: uv not found. Install: https://github.com/astral-sh/uv" >&2
    exit 1
fi

echo ">>> Installing dependencies with uv..."
uv sync

echo ">>> Installing pre-commit hooks..."
uv run pre-commit install

echo ">>> Verifying toolchain..."
uv run ruff check
uv run ruff format --check
uv run mypy src/
uv run pytest -q

echo ""
echo ">>> Bootstrap complete. Next:"
echo "    cp .env.example .env   # then edit"
echo "    uv run pytest          # run all tests"
