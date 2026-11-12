"""The corpus's declared identity schema, ``identity.json`` (#871).

A corpus written before #871 has no slug registry, so the guards that need
one cannot run there. Whether they run is decided by an explicit, versioned
declaration at the corpus root, never by whether ``slugs.json`` happens to
exist:

* no ``identity.json``: identity schema 0, the named legacy mode. The sync
  runs without slug-ownership enforcement and says so.
* ``identity.json`` with ``"schema_version": 1``: the enforced schema. A
  readable ``slugs.json`` is required everywhere ownership is enforced.
* any other declaration (unreadable, extra keys, version 0 written out, a
  future version): refused.

The declaration is its own file rather than a field of ``manifest.json``:
the sync rewrites the manifest on every run and would have to carry the
field forward, so one missed copy would silently drop the corpus back to
legacy. The sync never writes ``identity.json``; only applying the offline
migration plan does, together with the first ``slugs.json``.
"""

import json
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict, StrictInt, ValidationError

from lovspor.atomic_io import atomic_write_text
from lovspor.errors import IdentitySchemaError

IDENTITY_FILENAME = "identity.json"
#: The schema of a corpus without a declaration: no slug registry is enforced.
LEGACY_IDENTITY_SCHEMA: Final = 0
#: The schema the #871 migration moves a corpus to: ``slugs.json`` is required.
ENFORCED_IDENTITY_SCHEMA: Final = 1


class IdentityDeclaration(BaseModel):
    """The content of ``identity.json``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: StrictInt


def parse_declaration(blob: bytes | None, where: str) -> int:
    """The identity schema ``blob`` declares; no blob is the legacy schema.

    Only the enforced schema may be written out: version 0 is the absence of
    a declaration, and a version this engine does not know is refused.
    """
    if blob is None:
        return LEGACY_IDENTITY_SCHEMA
    try:
        declared = IdentityDeclaration.model_validate(json.loads(blob.decode("utf-8")))
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as exc:
        raise IdentitySchemaError(f"{where}: unreadable identity declaration: {exc}") from exc
    if declared.schema_version != ENFORCED_IDENTITY_SCHEMA:
        raise IdentitySchemaError(
            f"{where}: unknown identity schema version {declared.schema_version}; this "
            f"engine knows {ENFORCED_IDENTITY_SCHEMA} (no {IDENTITY_FILENAME} is the legacy "
            f"schema {LEGACY_IDENTITY_SCHEMA})",
        )
    return declared.schema_version


def declared_schema(repo: Path) -> int:
    """The identity schema the corpus at ``repo`` declares."""
    path = repo / IDENTITY_FILENAME
    return parse_declaration(path.read_bytes() if path.exists() else None, str(path))


def declaration_json() -> str:
    """``identity.json`` declaring the enforced schema, as written."""
    data = IdentityDeclaration(schema_version=ENFORCED_IDENTITY_SCHEMA).model_dump()
    return json.dumps(data, sort_keys=True, indent=2) + "\n"


def write_declaration(repo: Path) -> Path:
    """Declare the enforced schema at ``repo``; only the migration's apply calls this."""
    path = repo / IDENTITY_FILENAME
    atomic_write_text(path, declaration_json())
    return path
