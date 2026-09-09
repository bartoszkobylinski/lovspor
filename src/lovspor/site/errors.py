"""Exceptions of the site build (ADR-0014).

A branch of the ``LovsporError`` hierarchy (``lovspor.errors``) so a
caller can catch the family without a bare ``except Exception``. Every
error here means the build cannot produce a tree it could stand behind:
the fix is in repository content, in the release procedure or in the
checkout — never in defaulting to a previous value.
"""

from lovspor.errors import LovsporError


class SiteBuildError(LovsporError):
    """The site build refuses to produce a tree.

    Raised for a work tree that is not a clean git checkout (no
    ``lovspor_commit`` to record), a template outside the fact contract,
    or a missing or invalid input — ADR-0014 Decisions 1 and 4.
    """


class CapabilityDocumentError(SiteBuildError):
    """``deployment-capabilities.json`` is absent or outside its closed schema.

    The document is the only artifact a hosted claim may trace to, so the
    build fails without it and never defaults to "off", "on" or the
    previous release's value (ADR-0014 Decision 4). A document exists in
    every state — both subjects ``unobserved`` included — so this is a
    check on the release procedure, not on the hosted service.
    """
