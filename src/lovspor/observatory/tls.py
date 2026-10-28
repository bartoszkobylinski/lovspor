"""The TLS terms the observatory's HTTP clients connect under (issue #508).

Five Romerike municipalities (Nes, Gjerdrum, Nannestad, Eidsvoll, Hurdal; one
server, 194.60.75.82-86) serve TLS 1.2 only, and accept no key exchange but
RSA: of the AEAD suites, ``AES256-GCM-SHA384`` and ``AES128-GCM-SHA256``
alone. Python's default context offers ECDHE and DHE suites only, so there is
no common suite, and that server drops the TCP connection instead of sending
``handshake_failure`` -- ``Connection reset by peer`` at the handshake, which
``fetch.py`` reads as an unreadable access policy, which is a denial. curl
reaches the same hosts because SecureTransport still offers RSA key exchange.
Isolated one variable at a time on 2026-10-28 (issue #508).

So the observatory connects under Python's default list first -- every site
that can do forward secrecy still gets it, in the same order -- and the two
RSA-key-exchange AES-GCM suites last, as the only fallback. Nothing else
moves: TLS 1.2 stays the minimum, the certificate is verified against the
same bundle httpx uses, no CBC or legacy suite is added. The trade-off is
owned here: against such a server the session has no forward secrecy, for
public pages the archive reads and stores anyway; what matters there is
authenticity, and the alternative is the silent loss of those sources.
Only the observatory's clients connect this way; the site and release probes
keep httpx's defaults.
"""

from __future__ import annotations

import ssl
from typing import Any

import httpx

#: RSA-key-exchange AEAD suites, the last resort for a server that offers no ECDHE.
RSA_KEY_EXCHANGE_SUITES: tuple[str, ...] = ("AES256-GCM-SHA384", "AES128-GCM-SHA256")


def observatory_ssl_context() -> ssl.SSLContext:
    """httpx's default context with the RSA-key-exchange AEAD suites appended last."""
    context = httpx.create_ssl_context()
    default = [c["name"] for c in context.get_ciphers() if c["protocol"] != "TLSv1.3"]
    context.set_ciphers(":".join([*default, *RSA_KEY_EXCHANGE_SUITES]))
    return context


def observatory_client(**options: Any) -> httpx.Client:
    """An ``httpx.Client`` under the observatory's TLS terms; ``options`` are the client's."""
    return httpx.Client(verify=observatory_ssl_context(), **options)
