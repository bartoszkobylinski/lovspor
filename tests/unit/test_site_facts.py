"""The fact mechanism: sources, kinds, ledger and template boundary (ADR-0014 Decision 4)."""

import inspect
import re

import jinja2
import pytest
from markupsafe import Markup
from pydantic import ValidationError

from lovspor.site import facts as facts_module
from lovspor.site.errors import SiteBuildError
from lovspor.site.facts import (
    FactLedger,
    FactRegistry,
    FactSource,
    KindMismatchError,
    LedgerEntry,
    Unobserved,
    fact_renderer,
)
from lovspor.site.templates import badge, page_globals, site_environment

CAPABILITIES = "deployment-capabilities.json"
MANIFEST = "corpus/site-manifest.json"


def _sources() -> tuple[FactSource, ...]:
    return (
        FactSource(
            id="corpus.documents", kind="corpus", artifact=MANIFEST, field="documents", value=5900
        ),
        FactSource(
            id="code.tool_surface.tool_count",
            kind="code",
            artifact="tool-surface@" + "a" * 40,
            field="tool_count",
            value=17,
        ),
        FactSource(
            id="hosted.transport.authenticated.served_tool_count",
            kind="hosted",
            artifact=CAPABILITIES,
            field="observation.transport.authenticated.served_tool_count",
            value=17,
        ),
        FactSource(
            id="hosted.process.tool_count",
            kind="hosted",
            artifact=CAPABILITIES,
            field="observation.process.tool_count",
            unobserved=Unobserved(reason="timeout", observed_at="2026-01-01T00:00:00Z"),
        ),
        FactSource(
            id="code.licence",
            kind="code",
            artifact="LICENSE",
            field="identifier",
            value='AGPL-3.0 <script>alert("x")</script>',
        ),
        FactSource(
            id="hosted.oauth",
            kind="hosted",
            artifact=CAPABILITIES,
            field="observation.process.oauth_configured",
            value=True,
        ),
    )


@pytest.fixture
def registry() -> FactRegistry:
    return FactRegistry(sources=_sources())


class TestFactSource:
    def test_hosted_reads_the_capability_document_only(self) -> None:
        with pytest.raises(ValidationError, match="hosted"):
            FactSource(id="x", kind="hosted", artifact=MANIFEST, field="documents", value=1)

    def test_corpus_reads_the_release_manifest_only(self) -> None:
        with pytest.raises(ValidationError, match="corpus"):
            FactSource(id="x", kind="corpus", artifact=CAPABILITIES, field="documents", value=1)

    @pytest.mark.parametrize(
        "artifact", [CAPABILITIES, MANIFEST, "/etc/passwd", "../x", "tool-surface@main"]
    )
    def test_code_reads_a_checkout_path_or_the_descriptor(self, artifact: str) -> None:
        with pytest.raises(ValidationError):
            FactSource(id="x", kind="code", artifact=artifact, field="f", value=1)

    def test_code_accepts_a_repository_relative_path_and_the_descriptor(self) -> None:
        FactSource(id="x", kind="code", artifact="docs/mcp.md", field="f", value="v")
        FactSource(
            id="y", kind="code", artifact="tool-surface@" + "b" * 40, field="names", value="a"
        )

    def test_a_source_has_a_value_or_an_unobserved_record_never_both_or_neither(self) -> None:
        with pytest.raises(ValidationError):
            FactSource(id="x", kind="hosted", artifact=CAPABILITIES, field="f")
        with pytest.raises(ValidationError):
            FactSource(
                id="x",
                kind="hosted",
                artifact=CAPABILITIES,
                field="f",
                value=1,
                unobserved=Unobserved(reason="timeout", observed_at="2026-01-01T00:00:00Z"),
            )

    def test_only_a_hosted_fact_can_be_unobserved(self) -> None:
        """Code and corpus facts come from the checkout and the manifest,
        always; only an observation can fail to observe."""
        with pytest.raises(ValidationError, match="hosted"):
            FactSource(
                id="x",
                kind="corpus",
                artifact=MANIFEST,
                field="documents",
                unobserved=Unobserved(reason="timeout", observed_at="2026-01-01T00:00:00Z"),
            )

    def test_values_keep_their_type(self) -> None:
        assert FactSource(id="a", kind="code", artifact="x", field="f", value=True).value is True
        assert FactSource(id="a", kind="code", artifact="x", field="f", value=5).value == 5
        assert FactSource(id="a", kind="code", artifact="x", field="f", value="5").value == "5"


class TestFactRegistry:
    def test_rejects_duplicate_ids(self) -> None:
        source = FactSource(id="x", kind="code", artifact="a", field="f", value=1)

        with pytest.raises(ValidationError, match="x"):
            FactRegistry(sources=(source, source))

    def test_unknown_fact_is_a_build_error(self, registry: FactRegistry) -> None:
        with pytest.raises(SiteBuildError, match="nope"):
            registry.get("nope")

    def test_get_returns_the_source(self, registry: FactRegistry) -> None:
        assert registry.get("corpus.documents").value == 5900


class TestFactRenderer:
    def test_renders_the_escaped_value_in_a_marked_span(self, registry: FactRegistry) -> None:
        fact = fact_renderer("/", "en", registry, FactLedger())

        html = fact("code.licence", kind="code")

        assert isinstance(html, Markup)
        assert html == (
            '<span data-fact="code.licence" data-kind="code">'
            "AGPL-3.0 &lt;script&gt;alert(&#34;x&#34;)&lt;/script&gt;</span>"
        )

    def test_formats_numbers_per_language(self, registry: FactRegistry) -> None:
        nb = fact_renderer("/", "nb", registry, FactLedger())
        en = fact_renderer("/en/", "en", registry, FactLedger())

        assert ">5\u00a0900<" in nb("corpus.documents", kind="corpus")
        assert ">5,900<" in en("corpus.documents", kind="corpus")

    def test_renders_booleans_verbatim(self, registry: FactRegistry) -> None:
        fact = fact_renderer("/status/", "nb", registry, FactLedger())

        assert ">true<" in fact("hosted.oauth", kind="hosted")

    def test_renders_false_booleans_lowercase(self) -> None:
        assert facts_module._format_value(False, "en") == "false"

    def test_kind_mismatch_fails_the_build(self, registry: FactRegistry) -> None:
        """A hosted value under a code label, or a code value under a hosted
        label, is a defect in repository content (ADR:747-752)."""
        fact = fact_renderer("/", "nb", registry, FactLedger())

        with pytest.raises(KindMismatchError, match="hosted"):
            fact("hosted.transport.authenticated.served_tool_count", kind="code")
        with pytest.raises(KindMismatchError, match="code"):
            fact("code.tool_surface.tool_count", kind="hosted")
        with pytest.raises(KindMismatchError):
            fact("corpus.documents", kind="code")
        assert issubclass(KindMismatchError, SiteBuildError)

    def test_kind_is_a_required_keyword(self, registry: FactRegistry) -> None:
        fact = fact_renderer("/", "nb", registry, FactLedger())

        with pytest.raises(TypeError):
            fact("corpus.documents")  # type: ignore[call-arg]
        with pytest.raises(TypeError):
            fact("corpus.documents", "corpus")  # type: ignore[misc]

    def test_a_mismatch_leaves_no_ledger_entry(self, registry: FactRegistry) -> None:
        ledger = FactLedger()
        fact = fact_renderer("/", "nb", registry, ledger)

        with pytest.raises(KindMismatchError):
            fact("corpus.documents", kind="hosted")

        assert ledger.entries == ()

    @pytest.mark.parametrize(
        ("lang", "wording"),
        [
            (
                "nb",
                "ikke attestert ved denne utgivelsen — uobservert (timeout), "
                "observert 2026-01-01T00:00:00Z",
            ),
            (
                "en",
                "not attested at this release — unobserved (timeout), "
                "observed 2026-01-01T00:00:00Z",
            ),
        ],
    )
    def test_a_hosted_fact_over_an_unobserved_record_degrades_in_the_page_language(
        self, registry: FactRegistry, lang: str, wording: str
    ) -> None:
        """Never the previous value, never blank, never zero (ADR:940-948)."""
        fact = fact_renderer("/status/", lang, registry, FactLedger())  # type: ignore[arg-type]

        html = fact("hosted.process.tool_count", kind="hosted")

        assert (
            html
            == f'<span data-fact="hosted.process.tool_count" data-kind="hosted">{wording}</span>'
        )
        assert not re.search(r"\d", re.sub(r"observert .*|observed .*", "", str(html)))

    def test_writes_one_ledger_entry_per_rendered_value(self, registry: FactRegistry) -> None:
        ledger = FactLedger()
        fact = fact_renderer("/en/status/", "en", registry, ledger)

        fact("hosted.transport.authenticated.served_tool_count", kind="hosted")
        fact("hosted.process.tool_count", kind="hosted")
        fact("hosted.transport.authenticated.served_tool_count", kind="hosted")

        assert ledger.entries == (
            LedgerEntry(
                page="/en/status/",
                artifact=CAPABILITIES,
                field="observation.transport.authenticated.served_tool_count",
                kind="hosted",
                value=17,
                unobserved=None,
            ),
            LedgerEntry(
                page="/en/status/",
                artifact=CAPABILITIES,
                field="observation.process.tool_count",
                kind="hosted",
                value=None,
                unobserved=Unobserved(reason="timeout", observed_at="2026-01-01T00:00:00Z"),
            ),
        )

    def test_the_ledger_keeps_pages_apart(self, registry: FactRegistry) -> None:
        ledger = FactLedger()
        fact_renderer("/", "nb", registry, ledger)("corpus.documents", kind="corpus")
        fact_renderer("/en/", "en", registry, ledger)("corpus.documents", kind="corpus")

        assert [entry.page for entry in ledger.entries] == ["/", "/en/"]
        assert all(entry.value == 5900 for entry in ledger.entries)


class TestBadge:
    @pytest.mark.parametrize(
        ("status", "lang", "label"),
        [
            ("current", "nb", "Gjeldende"),
            ("current", "en", "Current"),
            ("planned", "nb", "Planlagt"),
            ("planned", "en", "Planned"),
            ("research", "nb", "Forskning"),
            ("research", "en", "Research"),
            ("early_access", "nb", "Tidlig tilgang"),
            ("early_access", "en", "Early access"),
        ],
    )
    def test_renders_the_one_vocabulary(self, status: str, lang: str, label: str) -> None:
        html = badge(status, lang)  # type: ignore[arg-type]

        assert isinstance(html, Markup)
        assert html == f'<span class="tag" data-status="{status}">{label}</span>'

    def test_refuses_a_status_outside_the_vocabulary(self) -> None:
        with pytest.raises(SiteBuildError, match="coming_soon"):
            badge("coming_soon", "nb")  # type: ignore[arg-type]


class TestSiteEnvironment:
    def test_is_configured_for_deterministic_escaped_output(self) -> None:
        environment = site_environment()

        assert environment.autoescape is True
        assert environment.undefined is jinja2.StrictUndefined
        assert environment.keep_trailing_newline is True
        assert environment.trim_blocks is True
        assert environment.lstrip_blocks is True
        assert environment.auto_reload is False
        assert isinstance(environment.loader, jinja2.FileSystemLoader)
        assert "now" not in environment.globals
        assert "badge" not in environment.globals
        assert "fact" not in environment.globals

    def test_page_globals_bind_fact_and_badge_to_the_page(self, registry: FactRegistry) -> None:
        ledger = FactLedger()
        template = site_environment().from_string(
            '{{ fact("corpus.documents", kind="corpus") }} {{ badge("planned") }} {{ lang }}\n'
        )

        html = template.render(page_globals("/status/", "nb", registry, ledger))

        assert html == (
            '<span data-fact="corpus.documents" data-kind="corpus">5\u00a0900</span> '
            '<span class="tag" data-status="planned">Planlagt</span> nb\n'
        )
        assert [entry.page for entry in ledger.entries] == ["/status/"]

    def test_a_missing_value_fails_instead_of_rendering_blank(self, registry: FactRegistry) -> None:
        template = site_environment().from_string("{{ missing }}")

        with pytest.raises(jinja2.UndefinedError):
            template.render(page_globals("/", "nb", registry, FactLedger()))

    def test_a_kind_mismatch_in_a_template_surfaces_as_the_build_error(
        self, registry: FactRegistry
    ) -> None:
        template = site_environment().from_string('{{ fact("corpus.documents", kind="hosted") }}')

        with pytest.raises(KindMismatchError):
            template.render(page_globals("/", "nb", registry, FactLedger()))

    def test_context_values_are_escaped(self, registry: FactRegistry) -> None:
        template = site_environment().from_string("{{ title }}")

        assert template.render(title="<b>") == "&lt;b&gt;"

    def test_the_fact_signature_is_id_and_keyword_kind(self, registry: FactRegistry) -> None:
        fact = fact_renderer("/", "nb", registry, FactLedger())
        parameters = inspect.signature(fact).parameters

        assert list(parameters) == ["fact_id", "kind"]
        assert parameters["kind"].kind is inspect.Parameter.KEYWORD_ONLY
        assert parameters["kind"].default is inspect.Parameter.empty
