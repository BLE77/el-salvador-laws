# Desktop Handoff

This package is the current starting point for the El Salvador law corpus project.

Target machine:

- Desktop with at least 1 TB free space
- GPU is optional for phase 1
- More important than GPU:
- sustained CPU performance
- RAM
- fast SSD
- reliable long-running jobs

## What this package contains

- `content/`: research docs that become the static site
- `data/source-inventory.json`: source catalog to drive discovery
- `corpus/`: markdown corpus layout and law template
- `scripts/build-site.mjs`: static site builder
- `dist/`: generated site output

## Immediate goal on the desktop

Do not start with full scraping or OCR.

Start with:

1. enumerate official source URLs
2. classify every discovered item
3. only then download and OCR what is necessary

## Phase 1 priorities

- Asamblea Legislativa
- Diario Oficial
- Jurisprudencia.gob.sv
- Asamblea library

## Storage strategy

Use a split layout:

- fast local SSD for code, metadata DB, markdown, queue state
- large data folder for raw downloads

Suggested layout:

```text
~/projects/el-salvador-laws/        # code and docs
~/data/el-salvador-laws/raw/        # raw PDFs and HTML snapshots
~/data/el-salvador-laws/derived/    # extracted text and markdown staging
~/data/el-salvador-laws/db/         # local database files if needed
```

If you later attach a larger drive, move `~/data/el-salvador-laws/raw/` there first.

## Recommended first installs

Core:

- Node.js 20+
- Python 3.11+
- Playwright browsers

Likely next:

- `tesseract`
- `ocrmypdf`
- `postgresql`

## Recommended first commands

```bash
cd ~/projects
unzip el-salvador-laws-desktop-handoff.zip
cd el-salvador-laws
npm run build
python3 -m http.server 4173 -d dist
```

Open:

- `http://localhost:4173`

## What to build next

The next implementation step should be an inventory-first crawler, not a downloader.

It should produce a table or JSON rows like:

- source
- discovered_url
- parent_url
- document_type
- format (`html`, `pdf`, `search`, `unknown`)
- title
- decree_no
- diario_oficial_no
- tomo
- year
- needs_browser
- likely_needs_ocr
- status

## Why the desktop is better

- 1 TB local storage is enough to start without micromanaging space
- better for long crawling and OCR batches
- better candidate for local database and raw archive storage
- GPU can help later if we use model-based OCR or local embeddings, but it is not required for phase 1

## Current project status

- research scaffold complete
- source catalog complete enough to start implementation
- site build verified locally
- no bulk crawler yet
- no PDF downloader yet
- no OCR pipeline wired yet

## Most important rule

Do not OCR or download everything blindly.

Enumerate first.
Classify second.
Download third.
OCR last.
