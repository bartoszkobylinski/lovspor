"""Legal-time replay pilot (#744): reconstruct an act's provisions at a legal date.

Reads Norsk Lovtidend avd. I announcements from the publicData archives and
applies the announced amendments in legal-effective order. Evidence only: it
never writes into ``lovverk``, never keys a commit on a legal date, and never
touches the recorded-time tools (``get_law_at``, ``recorded_at``) or the
event layer (``valid_at``). Anything it cannot prove stays ``unresolved``.
"""
