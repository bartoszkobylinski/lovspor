"""The ``promote`` command group with every subcommand registered.

Each command module adds its commands to :data:`~lovspor.promotion.commands.promote_app`
when it is imported, so the group the CLI mounts is the one these imports have filled.
"""

from lovspor.promotion import backfill_commands, batch_commands, migrate_commands
from lovspor.promotion.commands import promote_app

__all__ = ["backfill_commands", "batch_commands", "migrate_commands", "promote_app"]
