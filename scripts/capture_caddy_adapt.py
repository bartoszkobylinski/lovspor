#!/usr/bin/env python3
"""Capture the `caddy adapt` JSON the staged rehearsal's tests are driven from.

The comparison the staged first-migration rehearsal makes (ADR-0014
Validation, "First-migration rehearsal (staged)") is route-by-route over
what Caddy's OWN adapter produces. `tests/unit/caddy_fakes.toy_adapt`
models no URL matching, which is exactly why the tests cannot use it, so
the fixtures under `tests/unit/fixtures/caddy_adapt/` are real output of
a real `caddy adapt`, captured once and committed — like every other
captured fixture in this repository, regenerated only when one of the two
Caddyfiles changes.

    uv run python scripts/capture_caddy_adapt.py --caddy /path/to/caddy

There is no `caddy` binary in CI and none is wanted here: this script is
run by hand, on a machine that has one, and its output is reviewed in the
diff. It builds `tests.unit.staged_fixtures.build_world` in a temporary
directory, adapts both Caddyfiles against it — the proposed one once per
release fragment, the real one and the three that each take away one
asserted property — and writes the JSON with the world's own root
rewritten back to the capture prefix, so the committed fixture reads like
the droplet and the tests rehost it onto `tmp_path`.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tests.unit.staged_fixtures import (  # noqa: E402
    DROPS_ROBOTS,
    FOREIGN_MAP,
    SYMLINKED_ROOT,
    WRONG_ROOT,
    WRONG_SITE_ROOT,
    World,
    build_world,
    capture_form,
    fixture_path,
)

FRAGMENT_ENV = "LOVSPOR_RELEASE_FRAGMENT"
DOMAIN = "lovspor.test"
"""A reserved TLD: the fixture never names a host anyone can reach."""

VARIANTS = {
    "proposed.json": None,
    "proposed-drops-robots.json": DROPS_ROBOTS,
    "proposed-wrong-root.json": WRONG_ROOT,
    "proposed-wrong-site-root.json": WRONG_SITE_ROOT,
    "proposed-foreign-map.json": FOREIGN_MAP,
    "proposed-symlinked-root.json": SYMLINKED_ROOT,
}
"""Each committed fixture, and the release fragment the proposed Caddyfile imports for it."""


def adapt(caddy: str, caddyfile: Path, fragment: Path) -> str:
    """Real `caddy adapt`, on absolute paths so the `hide` list is absolute too."""
    done = subprocess.run(  # noqa: S603
        [caddy, "adapt", "--config", str(caddyfile), "--adapter", "caddyfile", "--pretty"],
        env={"LOVSPOR_DOMAIN": DOMAIN, FRAGMENT_ENV: str(fragment), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        raise SystemExit(f"caddy adapt refused {caddyfile}: {done.stderr.strip()}")
    return done.stdout


def write_fixture(name: str, adapted: str, world: World) -> None:
    path = fixture_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(capture_form(adapted, world.root), encoding="utf-8")
    print(f"{path.relative_to(REPO)}: {len(json.loads(adapted)['apps']['http']['servers'])} server")


def capture(caddy: str, world: World) -> None:
    write_fixture("previous.json", adapt(caddy, world.previous, world.fragment), world)
    for name, variant in VARIANTS.items():
        fragment = world.fragment if variant is None else world.variant(variant)
        write_fixture(name, adapt(caddy, world.proposed, fragment), world)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--caddy", default="caddy", help="The caddy binary to capture with.")
    arguments = parser.parse_args()
    with tempfile.TemporaryDirectory() as temporary:
        capture(arguments.caddy, build_world(Path(temporary), REPO))


if __name__ == "__main__":
    main()
