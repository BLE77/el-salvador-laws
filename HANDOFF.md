# El Salvador Law Agent — Handoff (Updated 2026-04-09)

## Current Status: PARTIALLY DEPLOYED

**Git**: Initial commit done (`0d4842a`), 99 files, no remote repo yet.  
**Fly.io**: App deployed, machine running, but **DB upload incomplete** (113MB of 1.7GB transferred).  
**Local**: Server works perfectly on localhost. Tests pass at 83.2%.

---

## BLOCKER: Database Upload to Fly.io

The 1.7GB SQLite database needs to get onto the Fly.io volume. Every upload method tried so far has failed or been cut short on Windows:

- `fly sftp shell` with piped stdin — returns instantly, doesn't transfer
- `fly ssh console` with stdin pipe — transferred 113MB then disconnected  
- `fly proxy` — couldn't connect

### What to try next:
1. **From a Mac/Linux machine**: `cat laws.db | fly ssh console -a el-salvador-laws -C "cat > /data/db/laws.db"` — this works reliably on Unix
2. **Upload to cloud storage** (S3, R2, GCS) then `fly ssh console -C "curl -o /data/db/laws.db <url>"`
3. **Use fly proxy + WireGuard**: WireGuard peer exists (`fdaa:65:1dcb:a7b:27:0:a:102`). Start local HTTP server, curl from remote.
4. **Split file**: `split -b 100M laws.db chunk_` → upload each chunk via ssh → `cat chunk_* > laws.db`

Once the DB lands at `/data/db/laws.db`, `start.sh` auto-detects it and starts the server. No redeploy needed.

### Remote state right now:
- `/data/db/laws.db` — 113MB (INCOMPLETE, needs to be replaced with full 1.7GB file)
- Machine: `82e609b7739e08` in `dfw`, state: `started`, waiting in start.sh loop
- Volume: `vol_re13gm0mnk93g714` (3GB, encrypted)

---

## What We Built

A **law search agent** for El Salvador. Four-layer RAG pipeline over 8,213 official legal documents from the Asamblea Legislativa. Answers questions in English, cites decreto numbers, professional tone with legal disclaimers.

### Architecture
```
User query → expand_query() (100+ English→Spanish legal term mappings)
    ↓
Layer 1: Wiki (47 pre-compiled English pages, instant)
Layer 2: QMD (8,211 BM25-indexed markdown docs, 5s timeout)
Layer 3: FTS5 (82,187 chunks in SQLite, always available)
Layer 4: Web Search (DuckDuckGo, fires when DB results < 3)
    ↓ deduplicate → rank (active first, repealed last)
    ↓ build context (10 sources, 40K chars for Anthropic)
    ↓
Claude Sonnet → Answer with decreto citations + disclaimer
```

### Corpus
| Metric | Value |
|--------|-------|
| Documents | 8,213 |
| Chunks | 82,187 |
| Unique decrees | 1,025 |
| Year range | 1847–2026 |
| Wiki pages | 47 (36 decreto + 6 topic + 5 legacy) |
| Database size | ~1.7GB SQLite |

---

## File Locations

### Code — `C:\Users\10700K\Desktop\el-salvador-laws\`
```
├── scripts/
│   ├── serve_fastapi.py           # ★ Production server
│   ├── test_api.py                # 10 endpoint tests
│   ├── test_questions.json        # 76 test questions (v1)
│   ├── test_questions_v2.json     # 50 test questions (v2, harder)
│   ├── run_test_questions.py      # V1 test runner
│   ├── run_test_questions_v2.py   # V2 test runner
│   ├── crawl.mjs                  # Playwright crawler
│   ├── download.mjs               # PDF downloader
│   ├── extract.py                 # PyMuPDF text extraction
│   ├── translate-and-chunk.py     # Chunking
│   ├── build-search-db.py         # SQLite FTS5 builder
│   ├── prepare-qmd.py             # QMD markdown prep
│   ├── build-wiki.py              # Wiki generator
│   ├── crawlers/                  # Source-specific crawlers
│   ├── legacy/                    # Old server versions
│   └── lib/                       # Shared JS utilities
├── wiki/                          # 47 wiki pages (copied into Docker image)
├── data/                          # State files (gitignored)
├── public/                        # Static frontend assets
├── Dockerfile
├── fly.toml
├── start.sh
├── requirements.txt
├── package.json
├── .gitignore
└── HANDOFF.md
```

### Data — `B:\el-salvador-laws\`
```
├── raw\                    # 8,371 downloaded PDFs
├── derived\text\           # Extracted text
├── derived\chunks\         # JSON chunk files
├── db\laws.db              # ★ The 1.7GB SQLite database
├── qmd-docs\               # 8,211 QMD markdown files
└── wiki\                   # Wiki pages (source of truth)
```

### Local DB copy for upload: `C:\Users\10700K\Desktop\el-salvador-laws\laws.db` (1.7GB, gitignored)

---

## How to Run Locally

```bash
export ANTHROPIC_API_KEY="sk-ant-api03-..."
cd C:\Users\10700K\Desktop\el-salvador-laws
python scripts/serve_fastapi.py --port 4200
# Open http://localhost:4200
```

### Environment Variables
```
ANTHROPIC_API_KEY    # Required (Claude Sonnet)
KIMI_API_KEY         # Optional (fallback LLM)
DB_PATH              # Default: B:/el-salvador-laws/db/laws.db
WIKI_DIR             # Default: B:/el-salvador-laws/wiki
QMD_CMD              # Default: C:\Program Files\nodejs\qmd.cmd
PORT                 # Default: 4200
```

---

## Fly.io Deployment Details

```
App:      el-salvador-laws
URL:      https://el-salvador-laws.fly.dev/
Region:   dfw (Dallas)
Machine:  82e609b7739e08 (shared-cpu-1x, 1GB RAM)
Volume:   vol_re13gm0mnk93g714 (3GB, encrypted)
Secret:   ANTHROPIC_API_KEY (set)
flyctl:   C:\Users\10700K\flyctl\flyctl.exe
```

### Redeploy after code changes:
```bash
cd C:\Users\10700K\Desktop\el-salvador-laws
C:\Users\10700K\flyctl\flyctl.exe deploy
```

---

## Test Results

### V1 (76 questions): **83.2%** — Zero failures

| Category | Score |
|----------|-------|
| Employment | 94% |
| Immigration | 92% |
| Government | 90% |
| Family | 88% |
| Business | 84% |
| Bitcoin | 83% |
| Edge Cases | 83% |
| Taxes | 79% |
| Criminal | 79% |
| Property | 75% |
| Daily Life | 67% ← weakest |

### V2 (50 harder questions): NOT YET RUN

---

## Git Status

- **Repo**: `C:\Users\10700K\Desktop\el-salvador-laws\.git`
- **Branch**: `master`
- **Latest commit**: `0d4842a` — initial commit, 99 files
- **Remote**: NONE — needs `gh repo create` or manual GitHub setup
- **No sensitive data committed** — verified clean (no keys, no .env, no .db files)

### To push to GitHub:
```bash
gh repo create el-salvador-laws --private --source=. --remote=origin --push
```

---

## What's Left

### Must-do
- [ ] **Upload 1.7GB database to Fly.io** (see blocker section above)
- [ ] Create GitHub repo and push
- [ ] Set up custom domain + HTTPS

### Should-do
- [ ] Run V2 test suite (50 harder questions)
- [ ] Improve Daily Life (67%) and Property (75%) categories
- [ ] Add immigration wiki page
- [ ] Streaming responses (SSE)

### Nice-to-have
- [ ] Voice input
- [ ] ChatGPT Custom GPT
- [ ] More wiki pages (47 → 60-80)
- [ ] Rate limiting per IP
- [ ] Translation of chunks to English

---

## Security Checklist
- [x] API keys via env vars only (no hardcoded keys anywhere)
- [x] .gitignore blocks *.db, .env, *api_key*, *secret*
- [x] Request size limit middleware
- [x] CORS configured (not wildcard)
- [x] XSS protection via escapeHtml()
- [x] Payload validation (2000 char limit)
- [x] No sensitive files in git (verified)

---

## Quick Reference

```bash
# Start local server
python scripts/serve_fastapi.py --port 4200

# Run tests
python scripts/test_api.py                    # Endpoint tests
python scripts/run_test_questions.py          # 76 questions (~20 min)
python scripts/run_test_questions_v2.py       # 50 questions (~12 min)

# Check Fly.io
C:\Users\10700K\flyctl\flyctl.exe status -a el-salvador-laws
C:\Users\10700K\flyctl\flyctl.exe ssh console -a el-salvador-laws

# Deploy
C:\Users\10700K\flyctl\flyctl.exe deploy
```
