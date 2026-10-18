"""Innsyn requests under offentleglova to every Norwegian local authority (issue #509).

Lovdata publicData does not carry Norsk Lovtidend avd. II, so the text of a
local regulation the authority does not host itself is requested from the
authority under offentleglova. This package holds the operator's side of that:
the population of authorities (SSB KLASS), the recipient address of each with
the evidence it was read from, the request ledger and its § 32 deadlines, and
the letter generator, and ``send``, which delivers a wave over SMTP only when
the owner runs it with ``--send``.

All of it lives in an operator-chosen directory (``LOVSPOR_INNSYN_DIR`` or
``--dir``), never in a repository: the ledger names case officers' mailboxes
and holds correspondence metadata, which personopplysningsloven asks us to
keep minimal and out of anything published.
"""
