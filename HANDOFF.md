# El Salvador Law Agent — Full Handoff

## What We Built

A **law search agent** that answers real questions about Salvadoran law in English, grounded in 8,213 official legal documents from the Asamblea Legislativa. Four-layer RAG pipeline that finds the right law, explains it in plain English, and cites decreto numbers. Professional tone — no political opinions, no legal advice, always includes disclaimer.

### The Pipeline

```
Official Government PDFs (asamblea.gob.sv)
    ↓ crawl (Playwright)
8,371 PDFs downloaded (1847-2026)
    ↓ extract (PyMuPDF)
8,371 text files
    ↓ chunk (4K char pieces, article-aware)
82,187 searchable chunks
    ↓ index
SQLite FTS5 database (B:/el-salvador-laws/db/laws.db)
QMD collection (8,211 markdown docs, BM25 indexed)
47 pre-compiled wiki pages (top laws + topic pages in English)
    ↓ status tracking
374 laws tagged as active/repealed/amendment
    ↓ serve
FastAPI async server with web chat UI (serve_fastapi.py)
```

### Corpus Stats

| Metric | Value |
|--------|-------|
| Total documents | 8,213 |
| Searchable chunks | 82,187 |
| Unique decrees | 1,025 |
| Year range | 1847-2026 |
| Sources | asamblea-year-archive (5,251), diario-archive (2,956), jurisprudencia (5) |
| Wiki pages | 47 (36 decreto-specific + 6 topic pages + 5 legacy) |
| QMD docs | 8,211 markdown files, BM25 indexed |
| Database size | ~200MB SQLite |
| Status-tracked laws | 374 (103 repealed, 216 amendments, 16 active, 39 other) |

---

## File Locations

### Code (SSD)
```
C:\Users\10700K\Desktop\el-salvador-laws\
├── scripts/
│   ├── serve_fastapi.py           # ★ FINAL production server
│   ├── test_api.py                # API endpoint tests (10 tests)
│   ├── test_questions.json        # 76 test questions (v1)
│   ├── test_questions_v2.json     # 50 test questions (v2 — harder, new categories)
│   ├── test_results.json          # Latest v1 test results
│   ├── run_test_questions.py      # Test runner for v1
│   ├── run_test_questions_v2.py   # Test runner for v2
│   ├── crawl.mjs                  # Playwright crawler
│   ├── download.mjs               # PDF downloader
│   ├── extract.py                 # PyMuPDF text extraction
│   ├── translate-and-chunk.py     # Chunking + optional translation
│   ├── build-search-db.py         # SQLite FTS5 builder
│   ├── prepare-qmd.py             # QMD markdown prep
│   ├── build-wiki.py              # Wiki generator
│   └── legacy/
│       ├── serve_v1.py            # Old server (simple FTS5)
│       └── serve_v2.py            # Old server (wiki + FTS5, blocking QMD)
├── data/
│   ├── inventory.ndjson
│   ├── download-state.ndjson
│   ├── extract-state.ndjson
│   └── translate-state.ndjson
├── HANDOFF.md                     # This file
├── CLAUDE.md
├── requirements.txt               # Python deps (fastapi, uvicorn, httpx, PyMuPDF)
└── package.json                   # npm scripts (serve, test)
```

### Data (B: drive)
```
B:\el-salvador-laws\
├── raw\                    # Downloaded PDFs (~8,371 files)
│   ├── asamblea-year-archive\{year}\*.pdf
│   ├── diario-archive\{year}\*.pdf
│   ├── jurisprudencia\{year}\*.pdf
│   └── manual\             # Manually added PDFs (ISSS, AFP, Pension)
├── derived\
│   ├── text\               # Extracted text
│   └── chunks\             # JSON chunk files
├── db\
│   ├── laws.db             # SQLite FTS5 database (main search DB)
│   └── law-status.json     # Status scan results (5,248 entries)
├── qmd-docs\               # Markdown docs for QMD (8,211 files)
└── wiki\                   # 47 wiki pages
    ├── decreto-*.md         # 36 decreto-specific pages
    ├── topic-starting-a-business.md
    ├── topic-criminal-law-basics.md
    ├── topic-property-and-real-estate.md
    ├── topic-daily-life-and-consumer-rights.md
    ├── topic-employer-taxes-and-payroll.md
    └── topic-government-and-public-services.md
```

---

## How to Run

### Production Server
```bash
# Set API key (required — no hardcoded keys)
export ANTHROPIC_API_KEY="sk-ant-api03-..."

# Start server
cd C:\Users\10700K\Desktop\el-salvador-laws
python scripts/serve_fastapi.py --port 4200

# Open http://localhost:4200
# Health check: http://localhost:4200/healthz
```

### Run Tests
```bash
# API endpoint tests (10 tests)
python scripts/test_api.py

# Full question test suite (76 questions, ~20 min)
python scripts/run_test_questions.py

# V2 harder questions (50 questions, ~12 min)
python scripts/run_test_questions_v2.py
```

### Environment Variables
```bash
ANTHROPIC_API_KEY    # Required — Anthropic Claude Sonnet (primary LLM)
KIMI_API_KEY         # Optional — Kimi K2.5 (fallback LLM)
DB_PATH              # SQLite database (default: B:/el-salvador-laws/db/laws.db)
WIKI_DIR             # Wiki pages (default: B:/el-salvador-laws/wiki)
QMD_CMD              # QMD binary (default: C:\Program Files\nodejs\qmd.cmd)
QMD_COLLECTION       # QMD collection (default: el-salvador-laws)
PORT                 # Server port (default: 4200)
```

### API Endpoints
```
GET  /                    Web chat UI
GET  /api/search?q=...   Full-text search (status-aware ranking)
GET  /api/decree/{N}     Get specific decree
GET  /api/browse          Browse by category/year
POST /api/chat            Agent chat (4-layer RAG with Claude Sonnet)
GET  /api/stats           Corpus statistics
GET  /openapi.json        OpenAPI spec
GET  /healthz             Health check
```

---

## Architecture

### Four-Layer Search
```
User query → expand_query() (100+ English→Spanish legal term mappings)
    ↓
┌─────────────────────────────────────────────────┐
│  Layer 1: Wiki (instant, pre-compiled)          │
│  - 47 English wiki pages in memory              │
│  - Token overlap + decreto matching + aliases   │
│  - Returns up to 5 results                      │
├─────────────────────────────────────────────────┤
│  Layer 2: QMD (BM25 hybrid search)              │
│  - 8,211 docs indexed                           │
│  - Async subprocess, 5-second timeout           │
├─────────────────────────────────────────────────┤
│  Layer 3: FTS5 (keyword fallback)               │
│  - 82,187 chunks indexed                        │
│  - Always available, never fails                │
├─────────────────────────────────────────────────┤
│  Layer 4: Web Search (DuckDuckGo fallback)      │
│  - Only fires when DB results < 3               │
│  - Marked as external source in answers         │
└─────────────────────────────────────────────────┘
    ↓ deduplicate by decreto number
    ↓ rank: active laws first, repealed last
    ↓ build context (10 sources, 40K chars for Anthropic)
    ↓
Claude Sonnet (primary) or Kimi K2.5 (fallback)
    ↓
Answer with decreto citations + disclaimer
```

### Context Limits (adaptive)
| LLM | Max Sources | Per Source | Total Context |
|-----|-------------|-----------|---------------|
| Anthropic Claude | 10 | 8,000 chars | 40,000 chars |
| Kimi K2.5 (fallback) | 3 | 1,200 chars | 4,000 chars |

This was a critical fix — the original code had Kimi limits hardcoded even when using Claude, so the agent could only see 5% of each wiki page. Increasing to 40K for Anthropic was the single biggest improvement.

### Conversation Memory
- Session-based, in-memory
- Last 5 exchanges per session
- 30-minute inactivity expiry
- Session ID passed in request/response
- Follow-up questions use conversation context

### System Prompt Personality
- Professional law search assistant (not a chatbot)
- Always includes legal disclaimer
- Redirects off-topic questions
- Never expresses political opinions
- Never editorializes about laws
- Helps with real-life legal situations
- Always cites decreto numbers

### Query Expansion
100+ English→Spanish legal term mappings in `LEGAL_TERMS` dict. Covers: business (LLC, sociedad anónima, comerciante), criminal (assault, knife, self defense), property (landlord, eviction, squatter), daily life (drinking age, refund, gun permit), government (corruption, public records), and more.

---

## Test Results

### V1 Tests (76 questions) — Latest: **83.2%**

| Category | Score | Pass/Warn/Fail |
|----------|-------|----------------|
| Employment | 94% | 7P / 1W / 0F |
| Immigration | 92% | 5P / 1W / 0F |
| Government | 90% | 4P / 1W / 0F |
| Family | 88% | 5P / 1W / 0F |
| Business | 84% | 7P / 1W / 0F |
| Bitcoin | 83% | 5P / 1W / 0F |
| Edge Cases | 83% | 9P / 3W / 0F |
| Taxes | 79% | 4P / 3W / 0F |
| Criminal | 79% | 4P / 2W / 0F |
| Property | 75% | 4P / 2W / 0F |
| Daily Life | 67% | 2P / 4W / 0F |

**Zero failures.** Most warnings are the agent answering correctly but citing the law by title instead of decreto number.

### Improvement History
| Round | Score | Key Change |
|-------|-------|-----------|
| Round 1 | 75.0% | Baseline |
| Round 2 | 79.6% | Added 4 topic wiki pages (business, criminal, property, daily life) |
| Round 3 | 83.2% | 10x context limit fix + government wiki + 40 query expansion terms |

### V2 Tests (50 harder questions)
Created but not yet run. Covers: business (8), criminal (8), daily life (6), property (6), government (4), health/medical (5), environment (3), education (3), technology (4), multi-law scenarios (3).

---

## Wiki Pages (47)

### Decreto-Specific Pages (36)
Decreto 3, 15, 57, 66, 133, 134, 137, 142, 153, 230, 253, 274, 286, 296, 379, 405, 413, 431, 507, 562, 582, 592, 644, 652, 665, 671, 677, 697, 712, 733, 776, 849, 856, 955, 1027, 1030

### Topic Pages (6) — Cross-Cutting
| File | Topics Covered |
|------|---------------|
| topic-starting-a-business.md | LLC/SA formation, tax registration, foreign investment, permits, trademarks, free trade zones |
| topic-criminal-law-basics.md | Common crimes, penalties, weapons, self-defense, state of exception, DUI, money laundering |
| topic-property-and-real-estate.md | Buying land, titles, landlord/tenant, construction permits, adverse possession, property taxes |
| topic-daily-life-and-consumer-rights.md | Consumer protection, drinking age, drivers license, guns, telecom, scams |
| topic-employer-taxes-and-payroll.md | ISSS 7.5%, AFP 8.75%, INSAFORP 1%, aguinaldo, vacaciones, indemnización |
| topic-government-and-public-services.md | Municipal services, corruption reporting, public records, elections, how laws are made |

---

## Security

### Implemented
- [x] API keys via environment variables only (no hardcoded keys)
- [x] Request size limit middleware (MAX_REQUEST_BYTES)
- [x] CORS properly configured (not wildcard with credentials)
- [x] Payload validation on POST /api/chat (type checking, 2000 char limit)
- [x] XSS protection via escapeHtml() on all dynamic innerHTML
- [x] Health check returns 503 when DB unavailable
- [x] requirements.txt with pinned versions

### Not Yet Implemented
- [ ] TLS in download pipeline
- [ ] Web search trust boundary documentation
- [ ] Download pipeline file validation
- [ ] Pipeline refresh safety
- [ ] Chunking size validation (some exceed 4K target)

---

## What's Left To Do

### Must-have for launch
- [ ] Deploy to Railway/VPS
- [ ] Set up domain name
- [ ] HTTPS (automatic on Railway)

### Should-do
- [ ] Run V2 test suite (50 harder questions) and fix gaps
- [ ] Improve Daily Life category (67%) — weakest area
- [ ] Improve Property category (75%)
- [ ] Add immigration wiki page (92% but still missing D-286 citations)
- [ ] Streaming responses (FastAPI SSE)

### Nice-to-have
- [ ] Voice input (browser Web Speech API)
- [ ] ChatGPT Custom GPT (OpenAPI spec ready at /openapi.json)
- [ ] More wiki pages (currently 47, could do 60-80)
- [ ] QMD vector embeddings (needs model download)
- [ ] Translation of chunks to English (only 33/82K done)
- [ ] Multi-channel via Hermes or OpenClaw
- [ ] Rate limiting per IP

---

## Commands Reference

### Full Pipeline (re-run if adding new laws)
```bash
node scripts/crawl.mjs                              # 1. Crawl inventory
NODE_TLS_REJECT_UNAUTHORIZED=0 node scripts/download.mjs  # 2. Download PDFs
python scripts/extract.py                            # 3. Extract text
python scripts/translate-and-chunk.py --skip-translate  # 4. Chunk text
python scripts/build-search-db.py --rebuild          # 5. Build search DB
python scripts/prepare-qmd.py                        # 6. Prepare QMD docs
qmd update                                           # 7. Update QMD index
python scripts/serve_fastapi.py --port 4200          # 8. Start server
```

### Quick Tests
```bash
python scripts/test_api.py                           # Endpoint tests
python scripts/run_test_questions.py                 # 76 question test
curl http://localhost:4200/healthz                    # Health check
curl -X POST http://localhost:4200/api/chat \
  -H "Content-Type: application/json" \
  -d '{"question":"Is weed legal?"}'                 # Test chat
```

### Dependencies
```bash
pip install fastapi uvicorn httpx PyMuPDF requests
npm install -g @tobilu/qmd
```
