"""Safe iteration of XML members in a .tar.bz2 archive.

We never call ``TarFile.extractall()`` or ``extract()`` — both would write
to disk based on member-provided paths and require ``tarfile.data_filter``
(CVE-2007-4559 mitigation). Instead we use ``extractfile()`` to stream
member bytes into memory, and we validate member names ourselves before
yielding them to the caller.

This sidesteps the classic tar-extraction attack surface entirely: no
filesystem writes occur based on member names. A hostile ``member.name``
(e.g. ``../etc/passwd``) only affects a string we return to the caller,
not a file we create. Callers are responsible for not using these names
as output paths without their own safety checks — see
``LovdataClient.download`` for the same defense pattern on the download
side.
"""

import tarfile
from collections.abc import Iterator
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from lovspor.errors import ExtractionError

_XML_SUFFIX = ".xml"
# Lovdata XML members run KB to a few MB. A member declaring far more than
# that is a corrupt archive or a decompression bomb, not a real law — refuse
# to read it into memory. 64 MiB is a generous ceiling well above any real doc.
_MAX_MEMBER_BYTES = 64 * 1024 * 1024


class TarballMember(BaseModel):
    """A single file member extracted from a tarball.

    ``name`` is the member's path inside the archive, as reported by
    ``TarInfo.name``. ``content`` is the full raw bytes of that member.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    content: bytes


def iter_tarball_xml(archive_path: Path) -> Iterator[TarballMember]:
    """Yield regular XML-suffixed members from a .tar.bz2 archive.

    Skipped silently:
    - non-regular-file members (symlinks, hardlinks, devices, directories)
    - members without an ``.xml`` suffix
    - members that extractfile() refuses to open

    Rejected with ``ExtractionError``:
    - member name contains a null byte
    - member name is absolute (starts with / or \\)
    - member name contains a ``..`` path component
    - member declares a size over the per-member cap (decompression bomb /
      corrupt archive guard)
    - archive is malformed or cannot be opened
    """
    try:
        with tarfile.open(archive_path, mode="r:bz2") as tar:
            for member in tar:
                _check_safe_name(member.name, archive_path)
                if not member.isfile() or not member.name.endswith(_XML_SUFFIX):
                    continue
                if member.size > _MAX_MEMBER_BYTES:
                    raise ExtractionError(
                        f"{archive_path}: member {member.name!r} declares "
                        f"{member.size} bytes, exceeding the "
                        f"{_MAX_MEMBER_BYTES}-byte per-member cap",
                    )
                fh = tar.extractfile(member)
                # Defensive: isfile() gate above already excludes the
                # member types that would make extractfile() return None.
                if fh is None:  # pragma: no cover
                    continue
                with fh:
                    yield TarballMember(name=member.name, content=fh.read())
    except tarfile.TarError as exc:
        raise ExtractionError(
            f"{archive_path}: malformed archive: {exc}",
        ) from exc
    except OSError as exc:
        raise ExtractionError(
            f"{archive_path}: cannot open archive: {exc}",
        ) from exc


def _check_safe_name(name: str, archive_path: Path) -> None:
    if "\x00" in name:
        raise ExtractionError(
            f"{archive_path}: member name contains null byte: {name!r}",
        )
    if name.startswith(("/", "\\")):
        raise ExtractionError(
            f"{archive_path}: member name is absolute: {name!r}",
        )
    if ".." in name.replace("\\", "/").split("/"):
        raise ExtractionError(
            f"{archive_path}: member name has parent reference: {name!r}",
        )
