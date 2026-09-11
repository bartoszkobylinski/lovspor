# Captured `caddy adapt` output

Real output of a real `caddy adapt`, captured once and committed — like
`tests/fixtures/`'s Lovdata XML. Regenerate only when
`deploy/digitalocean/Caddyfile`, `migrate_fixtures.OLD_CADDYFILE` or
`envelope.fragment_text` changes.

    uv run python scripts/capture_caddy_adapt.py --caddy /path/to/caddy

Provenance of the committed files: **Caddy v2.8.4** (`caddy_2.8.4_mac_arm64`,
`caddy version` → `v2.8.4 h1:q3pe0wpBj1OcHFZ3n/1nl4V4bxBrYoSoab7rL9BMYNk=`),
adapted over the world `tests/unit/staged_fixtures.build_world` builds in a
temporary directory, with `LOVSPOR_DOMAIN=lovspor.test`. The capture is
deterministic: two consecutive runs produce byte-identical files.

The only edit the script makes to Caddy's output is a path rewrite, so that
a fixture reads like the droplet and no temporary directory is committed:
the world's `www/` becomes `/var/www` and the world root — where both
Caddyfiles stand, and which Caddy writes into every `file_server` `hide`
list — becomes `/etc/caddy`. `staged_fixtures.rehost` puts a real
`tmp_path` back in their place when a test loads one.

`tests/unit/caddy_fakes.toy_adapt` cannot stand in for these: it models no
URL matching at all, and the staged rehearsal's whole subject is which URL
each configuration answers.

| file | the Caddyfile and fragment it is |
| --- | --- |
| `previous.json` | the pre-envelope Caddyfile: the corpus through the `lovspor-current` symlink, everything else from the hand-written landing page |
| `proposed.json` | the migration's Caddyfile with the release's own fragment: the corpus from `<release>/corpus`, everything else from `<release>/site` |
| `proposed-drops-robots.json` | the same, with `/robots.txt` gone from `@lovspor_corpus` — a corpus URL the old answers and the new does not |
| `proposed-wrong-root.json` | the same, with the corpus root left on the old flat release — the right bytes from the wrong tree |
| `proposed-symlinked-root.json` | the same, with both roots reached through a symlink — identical answers today, a moved symlink away from serving something else |
