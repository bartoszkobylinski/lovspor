"""Rights of each source, as verified on 2026-11-02 (proposal items a and k).

Read by the build (to pin each archive's licence basis) and by the
release-candidate gate. A Lovtidend archive not named here is recorded as
`inferred_same_dataset`: a future year never inherits a predecessor's basis,
and that basis can never leave `research_only`.
"""

NLOD_2_0 = "NLOD-2.0"
STATED_FOR_FILE = "stated_for_file"
STATED_FOR_ENDPOINT = "stated_for_endpoint"
INFERRED_SAME_DATASET = "inferred_same_dataset"

ARCHIVE_LICENCE_BASIS = {
    # data.norge.no names this file under NLOD 2.0.
    "lovtidend-avd1-2001-2025.tar.bz2": STATED_FOR_FILE,
    # Lovdata's OpenAPI "Public data" tag states NLOD 2.0 for /v1/publicData/get/{filename}.
    "lovtidend-avd1-2026.tar.bz2": STATED_FOR_ENDPOINT,
}


def licence_basis(filename: str) -> str:
    return ARCHIVE_LICENCE_BASIS.get(filename, INFERRED_SAME_DATASET)
