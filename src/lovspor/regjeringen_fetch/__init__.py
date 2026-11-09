"""Explicit-list fetch of regjeringen.no Prop. L / Prop. LS pages (#738).

The one network step of the forarbeider pipeline, kept outside the offline
``lovspor.forarbeider`` package so that package stays unable to reach the
network (``tests/unit/test_forarbeider_boundary.py``). It fetches only the
URLs the owner names: no discovery, no sitemap walk, no link following
(PR3B contract, Amendment 1, point 6).
"""
