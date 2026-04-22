# Architecture

(To be filled in as components are built.)

## High-level flow

```
Lovdata public API
        │
        ▼
download tar.bz2  ──► data/cache/  (gitignored)
        │
        ▼
extract + normalize XML
        │
        ▼
SHA256 per document
        │
        ▼
diff against manifest.json
        │
        ▼
render changed docs to Markdown
        │
        ▼
write to lovverk repo (sibling clone)
        │
        ▼
git commit per changed document
```

## Module map

(Populated when modules are implemented.)
