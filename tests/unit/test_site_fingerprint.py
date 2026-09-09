"""The publication toolchain and the two release identifiers (ADR-0014 Decisions 1 and 4)."""

import hashlib
import importlib.metadata
import json
import re
import tomllib
from pathlib import Path

import pytest

from lovspor.publish.companion import companion_json_bytes
from lovspor.runtime_identity import interpreter
from lovspor.site.capabilities import Checkout, Observation, derive_state, state_sha256
from lovspor.site.errors import SiteBuildError
from lovspor.site.fingerprint import (
    release_content_id,
    release_key,
    toolchain_fingerprint,
    write_release_content_id,
)
from tests.unit.site_fixtures import (
    available_observation,
    checkout_expectations,
    unobserved_transport,
)

_REPO = Path(__file__).resolve().parents[2]
_CHECKOUT = Checkout.model_validate(checkout_expectations())
_STATE = derive_state(Observation.model_validate(available_observation()), _CHECKOUT)
_OBSERVATION_UNKNOWN = Observation.model_validate(unobserved_transport("network"))
_TOOLCHAIN = toolchain_fingerprint(_REPO)


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


class TestJinja2IsACoreDependency:
    def test_jinja2_resolves_in_the_installed_environment(self) -> None:
        """The renderer participates in byte output, so it is a determinism
        input that must be installed, not assumed (ADR-0014 Decision 1)."""
        assert importlib.metadata.version("jinja2")

    def test_the_lock_names_jinja2_under_lovspor_itself(self) -> None:
        """Not a publication extra: `uv sync --frozen --no-dev` must build the
        site on the documented host, so the lock's own `lovspor` entry — not a
        transitive edge through torch — has to carry it."""
        lock = tomllib.loads((_REPO / "uv.lock").read_text(encoding="utf-8"))
        lovspor = next(package for package in lock["package"] if package["name"] == "lovspor")

        assert {dependency["name"] for dependency in lovspor["dependencies"]} >= {"jinja2"}
        assert any(
            requirement["name"] == "jinja2" and "marker" not in requirement
            for requirement in lovspor["metadata"]["requires-dist"]
        )


class TestToolchainFingerprint:
    def test_components_are_recomputable_from_the_environment(self) -> None:
        """The exact interpreter, the resolved Jinja2 and uv.lock at the
        checkout (ADR-0014 Decision 4, ADR:711)."""
        toolchain = toolchain_fingerprint(_REPO)

        assert toolchain.interpreter == interpreter()
        assert toolchain.jinja2_version == importlib.metadata.version("jinja2")
        assert toolchain.uv_lock_sha256 == _sha((_REPO / "uv.lock").read_bytes())

    def test_fingerprint_is_the_digest_of_its_components(self) -> None:
        toolchain = toolchain_fingerprint(_REPO)
        canonical = (
            f"interpreter\0{toolchain.interpreter.label}\n"
            f"jinja2\0{toolchain.jinja2_version}\n"
            f"uv.lock\0{toolchain.uv_lock_sha256}\n"
        )

        assert toolchain.fingerprint == _sha(canonical.encode("utf-8"))

    def test_moves_with_the_lock(self, tmp_path: Path) -> None:
        (tmp_path / "uv.lock").write_bytes(b"version = 1\n")

        assert (
            toolchain_fingerprint(tmp_path).fingerprint != toolchain_fingerprint(_REPO).fingerprint
        )

    def test_a_checkout_without_a_lock_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(SiteBuildError, match="uv.lock"):
            toolchain_fingerprint(tmp_path)


class TestReleaseKey:
    def test_the_same_meaning_gives_the_same_key(self) -> None:
        assert release_key("c" * 40, "l" * 40, _STATE, _TOOLCHAIN) == release_key(
            "c" * 40, "l" * 40, _STATE, _TOOLCHAIN
        )

    def test_every_component_moves_the_key(self) -> None:
        base = release_key("c" * 40, "l" * 40, _STATE, _TOOLCHAIN)
        other_state = derive_state(_OBSERVATION_UNKNOWN, _CHECKOUT)
        other_toolchain = _TOOLCHAIN.model_copy(update={"fingerprint": "f" * 64})

        assert base.state_sha256 == state_sha256(_STATE)
        assert base.toolchain_fingerprint == _TOOLCHAIN.fingerprint
        assert base != release_key("d" * 40, "l" * 40, _STATE, _TOOLCHAIN)
        assert base != release_key("c" * 40, "m" * 40, _STATE, _TOOLCHAIN)
        assert base != release_key("c" * 40, "l" * 40, other_state, _TOOLCHAIN)
        assert base != release_key("c" * 40, "l" * 40, _STATE, other_toolchain)


def _release(root: Path, facts: dict[str, object] | None = None) -> tuple[Path, Path]:
    corpus = root / "corpus"
    site = root / "site"
    (corpus / "lov" / "x").mkdir(parents=True)
    (site / "en").mkdir(parents=True)
    (corpus / "lov" / "x" / "index.html").write_text("<p>x</p>", encoding="utf-8")
    (corpus / "site-manifest.json").write_text('{"documents": 1}', encoding="utf-8")
    (site / "index.html").write_text("<p>no</p>", encoding="utf-8")
    (site / "en" / "index.html").write_text("<p>en</p>", encoding="utf-8")
    payload = (
        facts if facts is not None else {"release_content_id": None, "lovspor_commit": "a" * 40}
    )
    (site / "site-facts.json").write_bytes(companion_json_bytes(payload))
    return corpus, site


class TestReleaseContentId:
    def test_equal_trees_give_equal_ids(self, tmp_path: Path) -> None:
        one = _release(tmp_path / "one")
        two = _release(tmp_path / "two")

        assert release_content_id(*one) == release_content_id(*two)

    def test_is_the_digest_of_the_release_relative_listing(self, tmp_path: Path) -> None:
        corpus, site = _release(tmp_path)
        facts_canonical = json.dumps(
            {"lovspor_commit": "a" * 40}, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        lines = sorted(
            [
                f"corpus/lov/x/index.html\0{_sha(b'<p>x</p>')}\n",
                f"corpus/site-manifest.json\0{_sha(b'{"documents": 1}')}\n",
                f"site/index.html\0{_sha(b'<p>no</p>')}\n",
                f"site/en/index.html\0{_sha(b'<p>en</p>')}\n",
                f"site/site-facts.json\0{_sha(facts_canonical.encode('utf-8'))}\n",
            ]
        )

        assert release_content_id(corpus, site) == _sha("".join(lines).encode("utf-8"))

    def test_moves_with_any_served_byte_or_path(self, tmp_path: Path) -> None:
        corpus, site = _release(tmp_path)
        base = release_content_id(corpus, site)
        seen = {base}

        (corpus / "lov" / "x" / "index.html").write_text("<p>y</p>", encoding="utf-8")
        seen.add(release_content_id(corpus, site))
        (site / "index.html").rename(site / "landing.html")
        seen.add(release_content_id(corpus, site))
        (site / "sitemap-site.xml").write_text("<urlset/>", encoding="utf-8")
        seen.add(release_content_id(corpus, site))

        assert len(seen) == 4

    def test_moves_with_a_fact_but_not_with_its_own_id_field(self, tmp_path: Path) -> None:
        """A file cannot carry its own hash — the one exclusion (ADR:1012-1020)."""
        corpus, site = _release(tmp_path)
        base = release_content_id(corpus, site)

        (site / "site-facts.json").write_bytes(
            companion_json_bytes({"release_content_id": "0" * 64, "lovspor_commit": "a" * 40})
        )
        assert release_content_id(corpus, site) == base

        (site / "site-facts.json").write_bytes(
            companion_json_bytes({"release_content_id": None, "lovspor_commit": "b" * 40})
        )
        assert release_content_id(corpus, site) != base

    def test_hashes_the_facts_file_in_canonical_form(self, tmp_path: Path) -> None:
        """Sorted keys, no whitespace, non-ASCII kept, the id field removed —
        and a facts file without the field hashes as it is (ADR:1012-1020)."""
        corpus, site = _release(tmp_path)
        facts = site / "site-facts.json"
        facts.write_text('{"z": "æøå", "a": 1, "release_content_id": null}', encoding="utf-8")
        canonical = json.dumps(
            {"a": 1, "z": "æøå"}, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        lines = sorted(
            [
                f"corpus/lov/x/index.html\0{_sha(b'<p>x</p>')}\n",
                f"corpus/site-manifest.json\0{_sha(b'{"documents": 1}')}\n",
                f"site/index.html\0{_sha(b'<p>no</p>')}\n",
                f"site/en/index.html\0{_sha(b'<p>en</p>')}\n",
                f"site/site-facts.json\0{_sha(canonical.encode('utf-8'))}\n",
            ]
        )
        expected = _sha("".join(lines).encode("utf-8"))

        assert canonical == '{"a":1,"z":"æøå"}'
        assert release_content_id(corpus, site) == expected

        facts.write_text(f'{{"a":1,"release_content_id":"{"0" * 64}","z":"æøå"}}', encoding="utf-8")
        assert release_content_id(corpus, site) == expected

        facts.write_text('{"a":1,"z":"æøå"}', encoding="utf-8")
        assert release_content_id(corpus, site) == expected

    def test_ignores_json_whitespace_of_the_facts_file_only(self, tmp_path: Path) -> None:
        corpus, site = _release(tmp_path)
        base = release_content_id(corpus, site)

        (site / "site-facts.json").write_text(
            '{"lovspor_commit":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","release_content_id":null}',
            encoding="utf-8",
        )
        assert release_content_id(corpus, site) == base

        (corpus / "site-manifest.json").write_text('{"documents":1}', encoding="utf-8")
        assert release_content_id(corpus, site) != base


class TestWriteReleaseContentId:
    def test_fills_the_placeholder_without_moving_the_id(self, tmp_path: Path) -> None:
        corpus, site = _release(tmp_path)
        content_id = release_content_id(corpus, site)

        write_release_content_id(site / "site-facts.json", content_id)

        facts = json.loads((site / "site-facts.json").read_text(encoding="utf-8"))
        assert facts["release_content_id"] == content_id
        assert facts["lovspor_commit"] == "a" * 40
        assert release_content_id(corpus, site) == content_id
        assert (site / "site-facts.json").read_bytes() == companion_json_bytes(facts)
        assert not (site / "site-facts.json.tmp").exists()

    def test_is_idempotent_and_refuses_another_id(self, tmp_path: Path) -> None:
        _corpus, site = _release(tmp_path)
        write_release_content_id(site / "site-facts.json", "1" * 64)
        write_release_content_id(site / "site-facts.json", "1" * 64)

        with pytest.raises(SiteBuildError, match="release_content_id"):
            write_release_content_id(site / "site-facts.json", "2" * 64)

    def test_refuses_a_file_without_the_placeholder(self, tmp_path: Path) -> None:
        path = tmp_path / "site-facts.json"
        path.write_bytes(companion_json_bytes({"lovspor_commit": "a" * 40}))

        with pytest.raises(SiteBuildError, match="release_content_id"):
            write_release_content_id(path, "1" * 64)


class TestNoClock:
    def test_the_site_package_calls_no_clock(self) -> None:
        """A site build is a function of exactly four inputs; the builder
        calls no clock (ADR-0014 Decision 3)."""
        clock = re.compile(
            r"\b(?:now|utcnow|today|time\.time|monotonic|perf_counter|fromtimestamp)\("
        )
        offenders = [
            path.relative_to(_REPO).as_posix()
            for path in sorted((_REPO / "src" / "lovspor" / "site").rglob("*.py"))
            if clock.search(path.read_text(encoding="utf-8"))
        ]

        assert offenders == []
