"""The assembled ``innsyn`` app, mounted under ``lovspor observatory``.

Commands register themselves by decorating :data:`innsyn_app`, which only
happens when their module is imported; this module is the one place that
imports them all, as :mod:`lovspor.observatory.entrypoint` is for the
observatory. Mounted there rather than in :mod:`lovspor.cli`, which sits at
its size ratchet.
"""

from lovspor.innsyn import population_commands as _population_commands  # noqa: F401
from lovspor.innsyn.app import innsyn_app

__all__ = ["innsyn_app"]
