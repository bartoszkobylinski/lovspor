"""The assembled ``innsyn`` app, mounted under ``lovspor observatory``.

Commands register themselves by decorating :data:`innsyn_app`, which only
happens when their module is imported; this module is the one place that
imports them all, as :mod:`lovspor.observatory.entrypoint` is for the
observatory. Mounted there rather than in :mod:`lovspor.cli`, which sits at
its size ratchet.
"""

from lovspor.innsyn import delivery_commands as _delivery_commands  # noqa: F401
from lovspor.innsyn import directory_commands as _directory_commands  # noqa: F401
from lovspor.innsyn import ledger_commands as _ledger_commands  # noqa: F401
from lovspor.innsyn import letter_commands as _letter_commands  # noqa: F401
from lovspor.innsyn import population_commands as _population_commands  # noqa: F401
from lovspor.innsyn import received_commands as _received_commands  # noqa: F401
from lovspor.innsyn import send_commands as _send_commands  # noqa: F401
from lovspor.innsyn.app import innsyn_app

__all__ = ["innsyn_app"]
