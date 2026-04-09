# Inventory Crawler

Discovers and classifies URLs from official El Salvador legal sources. Does NOT bulk download or OCR.

## Quick start

```bash
cd C:\Users\10700K\Desktop\el-salvador-laws
npm install
node scripts/crawl.mjs --list          # see available sources
node scripts/crawl.mjs diario-archive  # crawl one source
node scripts/crawl.mjs --all           # crawl all sources
```

## Sources

| Source ID | Site | Method | Speed |
|-----------|------|--------|-------|
| `asamblea-year-archive` | Asamblea year browse | Playwright browser | ~2 min |
| `asamblea-search` | Asamblea search page | Playwright browser | ~1 min |
| `asamblea-annuals` | Asamblea annual compilations | Playwright browser | ~1 min |
| `diario-archive` | Diario Oficial archive | Direct API (fast) | ~3 min |
| `jurisprudencia` | Jurisprudencia.gob.sv | Playwright browser | ~1 min |
| `asamblea-library` | Biblioteca Asamblea | Playwright browser | ~1 min |

## Options

Set via environment variables:

```bash
MAX_YEARS=5 node scripts/crawl.mjs asamblea-year-archive
MAX_PAGES=10 node scripts/crawl.mjs asamblea-search
DATA_DIR=./data node scripts/crawl.mjs diario-archive
```

## Output

Each source writes NDJSON to `data/runs/<source>/inventory.ndjson`. Each line:

```json
{
  "source": "diario-archive",
  "discovered_at": "2026-04-06T17:04:28.123Z",
  "discovered_url": "https://www.diariooficial.gob.sv/seleccion/31425",
  "parent_url": "https://www.diariooficial.gob.sv/",
  "title": "03-01-2025.pdf",
  "document_type": "gazette-issue-page",
  "format": "pdf",
  "decree_no": null,
  "diario_oficial_no": null,
  "tomo": null,
  "year": "2025",
  "month": "01",
  "gazette_date": "2025-01-03",
  "filename": "03-01-2025.pdf",
  "needs_browser": false,
  "likely_needs_ocr": true,
  "status": "discovered"
}
```

## Utility scripts

```bash
node scripts/merge-inventory.mjs    # merge all runs -> data/inventory.ndjson
node scripts/inventory-stats.mjs    # print stats per source
```

## Resumability

Crawls are automatically resumable. Each source tracks seen URLs in its NDJSON file. Re-running skips already-discovered URLs.

## Storage layout

- `C:\Users\10700K\Desktop\el-salvador-laws\` - code, metadata, inventory
- `B:\el-salvador-laws\raw\` - raw PDFs (for future download phase)
- `B:\el-salvador-laws\derived\` - extracted text (for future OCR phase)

## Test results (2026-04-06)

| Source | Items discovered | Notes |
|--------|-----------------|-------|
| asamblea-year-archive | 97 | 91 year index pages + a few decree pages. Year pages need pagination to discover all decrees per year. |
| diario-archive | 3,115 | 3,114 gazette PDFs via API. Covers 13 sampled years. Historical years dump all issues under month 1. |
| jurisprudencia | 43 | Navigation links + 5 direct PDFs. The site uses JS-heavy search that needs more targeted scraping. |
| **Total** | **3,255** | |

## Known limitations per source

### Asamblea year-archive
- Year pages use pagination (`/0`, `/1`, etc.) but only the first page is crawled
- Need to detect and follow pagination within each year
- Decree count per year is likely 50-200+, but only ~6 visible per page

### Asamblea search
- Search form is JS-rendered, needs specific form interaction
- Best approach may be programmatic search by decree number ranges
- Not fully tested yet

### Asamblea annuals
- Structure depends on how the site organizes annual compilations
- Not fully tested yet

### Diario Oficial
- API-based, very reliable and fast
- Historical years (pre-2000) return all data under month 1 as one large batch
- Some historical months return malformed JSON (large responses get truncated)
- Full crawl of all 175 years would yield 30,000+ issues
- `/seleccion/{id}` returns PDF directly (no intermediate page)

### Jurisprudencia
- Heavy JavaScript site with dynamic search interface
- Current crawler captures navigation structure but needs deeper scraping
- The legislation search at `/busqueda/busquedaLeg.php?id=2` has categorized browsing
- Categories include: Decretos Ejecutivos, Leyes, Codigos, Reglamentos, etc.

### Asamblea library
- Catalog-based system, needs specific catalog queries
- Not fully tested yet

## What to build next

1. **Deepen Asamblea year-archive**: Add pagination support per year page
2. **Asamblea search by decree number**: Iterate decree numbers programmatically
3. **Jurisprudencia category drill-down**: Navigate each category and paginate
4. **Diario Oficial full crawl**: Run all 175 years (expect 30k+ PDFs)
5. **Cross-reference**: Match Asamblea decrees with Diario Oficial publications
