"""The ADR-0017 legislative crosswalk producer (#610): contract, capture, build, check.

Offline-first: only the capture stage touches the network, and only Lovdata
publicData and data.stortinget.no `eksport/*`. Nothing here publishes.
"""
