"""Operating the droplet: alerts when a lovspor systemd unit fails (issue #478).

``lovspor-alert@.service`` runs ``lovspor ops alert --unit %i`` for every unit
that names it in ``OnFailure=``. The facts come from systemd (``unit_facts``),
the message and its delivery live in ``alert``, and the command layer
(``commands``) is the one place that reads the clock, the host name and the
operator's configuration.
"""
