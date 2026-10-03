"""Lovdata local-regulation ids in captured page bytes (issue #509).

Every link form below was found in the archive by the 2026-10-03
classification scan; the fixtures are synthetic pages carrying those forms,
never captured bytes.
"""

import pytest

from lovspor.observatory.lf_refs import LovdataRef, extract_lovdata_refs


def _page(*hrefs: str) -> bytes:
    links = "".join(f'<a href="{href}">forskrift</a>' for href in hrefs)
    return f"<html><body>{links}</body></html>".encode()


class TestTheLinkFormsTheKommunerUse:
    @pytest.mark.parametrize(
        ("href", "expected"),
        [
            (
                "https://lovdata.no/dokument/LF/forskrift/2020-11-19-2630",
                LovdataRef(kind="LF", lf_id="2020-11-19-2630"),
            ),
            (
                "https://lovdata.no/dokument/LTII/forskrift/2026-02-26-329",
                LovdataRef(kind="LTII", lf_id="2026-02-26-329"),
            ),
            (
                "https://lovdata.no/forskrift/2022-05-05-810",
                LovdataRef(kind="forskrift", lf_id="2022-05-05-810"),
            ),
            (
                "https://lovdata.no/LTII/forskrift/2025-12-18-3023",
                LovdataRef(kind="LTII", lf_id="2025-12-18-3023"),
            ),
            (
                "https://lovdata.no/pro/#document/LF/forskrift/2017-06-20-879?ctx=1",
                LovdataRef(kind="LF", lf_id="2017-06-20-879"),
            ),
            (
                "https://www.lovdata.no/dokument/LF/forskrift/2016-12-01-1424/%C2%A74#%C2%A74",
                LovdataRef(kind="LF", lf_id="2016-12-01-1424"),
            ),
            (
                "http://lovdata.no/dokument/lf/forskrift/2020-09-30-2091?q=brenning",
                LovdataRef(kind="LF", lf_id="2020-09-30-2091"),
            ),
        ],
    )
    def test_a_link_yields_its_kind_and_id(self, href: str, expected: LovdataRef) -> None:
        assert extract_lovdata_refs(_page(href)) == (expected,)

    def test_the_host_is_read_in_any_case(self) -> None:
        page = _page("HTTPS://LOVDATA.NO/DOKUMENT/LF/FORSKRIFT/2020-11-19-2630")

        assert extract_lovdata_refs(page) == (LovdataRef(kind="LF", lf_id="2020-11-19-2630"),)

    def test_a_percent_encoded_redirect_link_is_read(self) -> None:
        safelink = (
            "https://eur01.safelinks.example/?url=https%3A%2F%2Flovdata.no%2Fdokument"
            "%2FLF%2Fforskrift%2F2024-06-19-1239%3Fq%3Dforskrift"
        )

        assert extract_lovdata_refs(_page(safelink)) == (
            LovdataRef(kind="LF", lf_id="2024-06-19-1239"),
        )

    def test_a_json_escaped_link_is_read(self) -> None:
        payload = (
            b'{"href":"https:\\/\\/lovdata.no\\/dokument\\/LTII\\/forskrift\\/2025-01-02-7\\"}'
        )

        assert extract_lovdata_refs(payload) == (LovdataRef(kind="LTII", lf_id="2025-01-02-7"),)

    def test_the_id_ends_where_its_digits_end(self) -> None:
        payload = b"lovdata.no/dokument/LF/forskrift/2024-12-19-3535\\"

        assert extract_lovdata_refs(payload) == (LovdataRef(kind="LF", lf_id="2024-12-19-3535"),)


class TestWhatIsNotALocalRegulation:
    @pytest.mark.parametrize(
        "href",
        [
            "https://lovdata.no/dokument/SF/forskrift/2002-03-22-313",
            "https://lovdata.no/dokument/NL/lov/2018-06-22-83",
            "https://lovdata.no/dokument/LTI/forskrift/2023-03-28-449",
            "https://lovdata.no/dokument/OV/forskrift/2015-05-20-936",
            "https://lovdata.no/lov/2005-06-17-64",
            "https://lovdata.no/dokument/LF/forskrift/2020-11",
            "https://notlovdata.no/dokument/LF/forskrift/2020-11-19-2630",
            "https://example.invalid/dokument/LF/forskrift/2020-11-19-2630",
        ],
    )
    def test_it_yields_nothing(self, href: str) -> None:
        assert extract_lovdata_refs(_page(href)) == ()


class TestTheAnswerIsAFunctionOfTheBytes:
    def test_repeats_collapse_and_the_order_is_fixed(self) -> None:
        page = _page(
            "https://lovdata.no/dokument/LTII/forskrift/2026-02-26-329",
            "https://lovdata.no/dokument/LF/forskrift/2020-11-19-2630",
            "https://lovdata.no/dokument/LF/forskrift/2020-11-19-2630?q=x",
            "https://lovdata.no/forskrift/2020-11-19-2630",
        )

        assert extract_lovdata_refs(page) == (
            LovdataRef(kind="LF", lf_id="2020-11-19-2630"),
            LovdataRef(kind="LTII", lf_id="2026-02-26-329"),
            LovdataRef(kind="forskrift", lf_id="2020-11-19-2630"),
        )

    def test_bytes_that_are_not_utf8_are_still_read(self) -> None:
        payload = (
            b"\xff\xfe<a href='https://lovdata.no/dokument/LF/forskrift/2019-01-01-1'>\xe6</a>"
        )

        assert extract_lovdata_refs(payload) == (LovdataRef(kind="LF", lf_id="2019-01-01-1"),)

    def test_an_empty_page_has_none(self) -> None:
        assert extract_lovdata_refs(b"") == ()
