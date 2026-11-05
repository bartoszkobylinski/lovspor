"""Synthetic 1.0.0-rc crosswalk records for the contract tests (#610).

Shaped like the E4 pilot chain (LOV-2022-06-17-63 -> Lovvedtak 85 (2021-2022) ->
sak 89016); every digest is synthetic, so nothing here stands for captured bytes.
"""

import copy
import io
import tarfile
from hashlib import sha256
from typing import Any

ARCHIVE = "lovtidend-avd1-2001-2025.tar.bz2"
ARCHIVE_URL = f"https://api.lovdata.no/v1/publicData/get/{ARCHIVE}"
MEMBER = "lti/2022/nl-20220617-063.xml"
CASE_URL = "https://data.stortinget.no/eksport/sak?sakid=89016"
LIST_URL = (
    "https://data.stortinget.no/eksport/publikasjoner?publikasjontype=lovvedtak&sesjonid=2021-2022"
)
RETRIEVED = "2026-11-02T10:00:00+00:00"


def digest(label: str) -> str:
    return sha256(label.encode()).hexdigest()


def evidence(role: str, url: str, upstream: str, parser: str) -> dict[str, Any]:
    return {
        "role": role,
        "source_url": url,
        "capture_level": "raw_body_captured",
        "upstream_id": upstream,
        "body_sha256": digest(f"body {upstream}"),
        "parsed_identity_sha256": digest(f"identity {upstream} {role}"),
        "retrieved_at": RETRIEVED,
        "parser_version": parser,
    }


def archive_pin(name: str = ARCHIVE, basis: str = "stated_for_file") -> dict[str, Any]:
    return {
        "source_url": f"https://api.lovdata.no/v1/publicData/get/{name}",
        "sha256": digest(name),
        "size_bytes": 1000,
        "last_modified": "2026-10-01T00:00:00Z",
        "retrieved_at": "2026-11-02T09:00:00+00:00",
        "licence": "NLOD-2.0",
        "licence_basis": basis,
    }


_BASE: dict[str, Any] = {
    "schema_version": "1.0.0-rc",
    "release_state": "research_only",
    "amending_lov_id": "LOV-2022-06-17-63",
    "record_revision": 1,
    "record_state": "active",
    "observed_on": "2026-11-02",
    "status": "attested",
    "edge_status": {
        "law_identity": "attested",
        "law_decision": "attested",
        "decision_case": "attested",
    },
    "decision": {"kind": "lovvedtak", "session_id": "2021-2022", "number": 85},
    "sak_id": 89016,
    "sanction_events": [
        {"law_id": "LOV-2022-06-17-63", "kind": "unclassified_sanction", "source_url": ARCHIVE_URL}
    ],
    "evidence": [
        evidence("law_identity", ARCHIVE_URL, f"{ARCHIVE}!{MEMBER}", "lovtidend-identity-1"),
        evidence("law_decision_pair", ARCHIVE_URL, f"{ARCHIVE}!{MEMBER}", "lovtidend-identity-1"),
        evidence(
            "decision_publication", LIST_URL, "publikasjoner/2021-2022", "lovvedtak-discovery-2"
        ),
        evidence("decision_case", CASE_URL, "sak/89016", "sak-xml-2"),
    ],
    "source_pins": {
        "lovverk_commit": "a" * 40,
        "producer_commit": "b" * 40,
        "lovdata_archives": [archive_pin()],
    },
}


def attested() -> dict[str, Any]:
    return copy.deepcopy(_BASE)


def besl_o() -> dict[str, Any]:
    """LOV-2009-06-19-107 as the study read it: identity attested, Besl. O. not supported."""
    record = attested()
    member = "lti/2009/nl-20090619-107.xml"
    record.update(
        amending_lov_id="LOV-2009-06-19-107",
        status="unsupported",
        edge_status={
            "law_identity": "attested",
            "law_decision": "unsupported",
            "decision_case": "unsupported",
        },
        decision={"kind": "besl_o", "session_id": "2008-2009", "number": 98},
        sak_id=None,
        sanction_events=[],
        reason="the header cites Besl. O.; the reader types Lovvedtak only",
        reason_code="decision_kind_not_supported",
    )
    record["evidence"] = [
        evidence("law_identity", ARCHIVE_URL, f"{ARCHIVE}!{member}", "lovtidend-identity-1")
    ]
    return record


# --- synthetic source bytes, in the real shapes (never real Lovdata XML) -------------

NS = 'xmlns="http://data.stortinget.no"'


def document(law_id: str, misc: str) -> bytes:
    slug = law_id.removeprefix("LOV-")
    return (
        '<html lang="no"><body><header class="documentHeader"><dl>'
        f'<dd class="legacyID">{law_id}</dd><dd class="dokid">LTI/lov/{slug}</dd>'
        f'<dd class="miscInformation">{misc}</dd><dd class="refid">lov/{slug}</dd>'
        "</dl></header></body></html>"
    ).encode()


def tar_bytes(members: dict[str, bytes]) -> bytes:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:bz2") as tar:
        for name, body in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))
    return stream.getvalue()


def saker(*items: tuple[int, str]) -> bytes:
    found = "".join(f"<sak><id>{i}</id><henvisning>{h}</henvisning></sak>" for i, h in items)
    return f"<saker_oversikt {NS}><saker_liste>{found}</saker_liste></saker_oversikt>".encode()


def pubs(*ids: str) -> bytes:
    found = "".join(f"<publikasjon><id>{i}</id><type>lovvedtak</type></publikasjon>" for i in ids)
    return (
        f"<publikasjoner_oversikt {NS}><publikasjoner_liste>{found}"
        "</publikasjoner_liste></publikasjoner_oversikt>"
    ).encode()


def sak(sak_id: int, session: str, *decisions: tuple[str, int]) -> bytes:
    refs = "".join(
        f"<publikasjon_referanse><eksport_id>vedtak-{s[:4]}{s[-2:]}-{n:03d}</eksport_id>"
        f"<lenke_tekst>Lovvedtak {n} ({s})</lenke_tekst><type>lovvedtak</type>"
        "</publikasjon_referanse>"
        for s, n in decisions
    )
    return (
        f"<detaljert_sak {NS}><id>{sak_id}</id><sak_sesjon>{session}</sak_sesjon>"
        f"<publikasjon_referanse_liste>{refs}</publikasjon_referanse_liste></detaljert_sak>"
    ).encode()
