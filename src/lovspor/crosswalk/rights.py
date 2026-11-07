"""Rights of each source (proposal items a and k; lovspor-notebook ADR-0021).

Read by the build (to pin each archive's licence basis) and by the
release-candidate gate. A Lovtidend archive not named here is recorded as
`inferred_same_dataset`: a future year never inherits a predecessor's basis,
and that basis can never leave `research_only`.

`stated_for_endpoint` is what the 2026-11-02 reading found for the current-year
file, and R2 rejected it on 2026-11-06 (ADR-0019). The 2026-11-07 reading found
the dataset-level statements ADR-0021 rests on: Lovdata states NLOD 2.0 for the
data.norge.no dataset "Norsk Lovtidend, Avdeling I", whose one NLOD 2.0
distribution is fetched through `/list` and `/get/` as "en av de to delene av
datasettet", and `/list` gives both avd. I files the same dataset description.
"""

NLOD_2_0 = "NLOD-2.0"
STATED_FOR_FILE = "stated_for_file"
STATED_FOR_DATASET = "stated_for_dataset"
STATED_FOR_ENDPOINT = "stated_for_endpoint"
INFERRED_SAME_DATASET = "inferred_same_dataset"

ARCHIVE_LICENCE_BASIS = {
    # data.norge.no names this file under NLOD 2.0.
    "lovtidend-avd1-2001-2025.tar.bz2": STATED_FOR_FILE,
    # The current-year part of the same NLOD 2.0 dataset (ADR-0021).
    "lovtidend-avd1-2026.tar.bz2": STATED_FOR_DATASET,
}

# Where a dataset-level basis holds: the Avdeling I members of that year's part.
# A cited member outside it is not shown to be part of the dataset.
DATASET_MEMBERS = {"lovtidend-avd1-2026.tar.bz2": "lti/2026/"}


def licence_basis(filename: str) -> str:
    return ARCHIVE_LICENCE_BASIS.get(filename, INFERRED_SAME_DATASET)


def within_dataset(archive: str, member: str) -> bool:
    """Whether a cited member lies where the archive's dataset-level basis holds.

    The member must name that place, not only spell it: the schema admits `.`,
    `..` and empty segments, and `lti/2026/../2025/x.xml` is a 2025 path.
    """
    prefix = DATASET_MEMBERS.get(archive)
    if prefix is None:
        return True
    return member.startswith(prefix) and not {"", ".", ".."} & set(member.split("/"))
