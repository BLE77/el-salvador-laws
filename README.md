# El Salvador Laws

This project is a markdown-first research hub for building an AI-searchable corpus of Salvadoran law.

What is in the repo:

- `content/`: research notes that become the static website.
- `data/source-inventory.json`: machine-readable inventory of official and secondary sources.
- `corpus/`: placeholder folders and templates for normalized law markdown.
- `scripts/build-site.mjs`: zero-dependency static site builder.

Why this structure:

- Official publication status in El Salvador is split across the Asamblea Legislativa, Diario Oficial, and sectoral bodies.
- Many documents are PDF-first, but some already have searchable text layers.
- AI retrieval needs article-level markdown plus stable metadata, not just raw PDFs.

Quick start:

```bash
cd /Users/bless/el-salvador-laws
npm run build
python3 -m http.server 4173 -d dist
```

Open `http://localhost:4173`.

Important caveat:

- This is a research and infrastructure prototype, not legal advice.
- The authoritative legal effect of a norm still depends on official publication and version status.
