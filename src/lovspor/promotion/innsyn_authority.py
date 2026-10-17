"""The authority block of a received text, from the innsyn population (ADR-0016 1c, A1).

A received text is attributed to the authority its request was sent to, and
that authority is named as SSB KLASS names it in the latest population
snapshot: never typed in by hand, and not from the observatory's source
register, which need not list an authority that was only asked.

The block names the authority in its Norwegian form, the part before KLASS's
first `` - `` (``Oslo - Oslove`` -> ``Oslo``), and without the type KLASS
appends to a fylkeskommune's name (``Vestland fylkeskommune`` -> ``Vestland``):
the rendering adds the type itself (``source_provider``).

The snapshot carries no classification version, only the date it was read
at, so ``klass_version`` is the operator's, as on the observed path.
"""

from pydantic import ValidationError

from lovspor.errors import InnsynError, PromotionRefusedError
from lovspor.innsyn.population import Authority as KlassAuthority
from lovspor.innsyn.population import latest_snapshot
from lovspor.innsyn.workspace import InnsynWorkspace
from lovspor.promotion.models import Authority, AuthorityType


def _klass_authority(workspace: InnsynWorkspace, authority_id: str) -> KlassAuthority:
    try:
        listed = latest_snapshot(workspace).by_id()
    except InnsynError as exc:
        raise PromotionRefusedError(f"the innsyn population cannot be read: {exc}") from exc
    authority = listed.get(authority_id)
    if authority is None:
        raise PromotionRefusedError(
            f"{authority_id} is not in the latest innsyn population snapshot"
        )
    return authority


def _name(authority: KlassAuthority) -> str:
    norwegian = authority.name.partition(" - ")[0].strip()
    return norwegian.removesuffix(f" {authority.kind}").strip()


def received_authority(
    workspace: InnsynWorkspace, authority_id: str, klass_version: str
) -> Authority:
    """The authority block for ``authority_id``, read from the latest population snapshot.

    Raises:
        PromotionRefusedError: no snapshot reads, the snapshot does not list
            ``authority_id``, or the block does not validate.
    """
    listed = _klass_authority(workspace, authority_id)
    try:
        return Authority(
            id=authority_id,
            type=AuthorityType(listed.kind),
            name=_name(listed),
            klass_version=klass_version,
        )
    except ValidationError as exc:
        msg = f"the authority block does not validate: {exc.errors()[0]['msg']}"
        raise PromotionRefusedError(msg) from exc
