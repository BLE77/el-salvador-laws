# El Salvador Law Agent — Autoresearch Program

## Objective

Improve test question scores from **83.2%** to **90%+** through autonomous experimentation.

**Metric**: Total score percentage from `run_test_questions.py` (76 questions, 4 points each).
Higher is better. Current baseline: **83.2%** (253/304).

**Weak categories** (biggest improvement potential):
- Daily Life: 67% (worst)
- Property: 75%
- Taxes: 79%
- Criminal: 79%

**Strong categories** (protect these, don't regress):
- Employment: 94%
- Immigration: 92%
- Government: 90%

---

## Key Insight: All 20 WARN Questions Have the Same Issue

Every single warning (20/76 questions) fails for exactly one reason: **MISSING_DECRETOS**.
The LLM returns a substantive answer (score 2/4) but fails to cite the expected decreto number.

This means **zero questions fail completely** — the search pipeline finds relevant content,
but either (a) the wrong decreto lands in context, or (b) the LLM doesn't cite the decreto
that IS in context. Both are fixable.

### Specific failing decretos:
| Decreto | Times Missing | Topics |
|---------|--------------|--------|
| 1030    | 5x | Criminal code (drinking age, gun laws, state of exception, money laundering, car accident) |
| 644     | 2x | Civil code (property titles, landlord eviction) |
| 671     | 2x | Commercial code (trademarks, lemon law) |
| 134/230 | 3x | Tax code (foreign income, small business filing, tax penalties) |
| 286     | 2x | Immigration (citizenship, driver's license) |
| 57      | 2x | Bitcoin law (Chivo wallet, crypto taxes) |
| 15      | 2x | Labor code (unpaid wages, unjust dismissal in Spanish) |
| 776     | 1x | Consumer protection (lemon law) |
| 274     | 1x | Municipal code (garbage complaints) |
| 677/133 | 1x | Family code (restraining orders) |

---

## How Experiments Work

1. The agent modifies `scripts/serve_fastapi.py` (the only file modified per experiment)
2. The server is restarted automatically
3. `run_test_questions.py` runs all 76 questions against the server
4. The score is compared to the previous best
5. If improved: keep the change. If regressed: revert.

Each experiment takes ~20 minutes (76 questions x ~15s each).
Target: ~3 experiments/hour, ~36 overnight.

---

## Experiment Strategies (ordered by expected impact)

### Strategy 1: Query Expansion Gaps
The `LEGAL_TERMS` dictionary maps English terms to Spanish search terms. Many test failures happen because a question uses words not in this mapping. For each failing question, check what terms the user used and whether they're missing from `LEGAL_TERMS`.

**How to test**: Look at FAIL/WARN questions in `test_results.json`. For each one, trace the query through `expand_query()` and see if the Spanish terms would match the relevant decreto in the FTS index.

### Strategy 2: FTS5 Query Construction
The `expand_query()` function builds FTS5 queries using `OR` joins. Experiment with:
- Adding phrase queries (e.g., `"salario minimo"` as a phrase, not separate words)
- Boosting specific columns in FTS5 (text_es vs text_en)
- Using NEAR operator for related terms
- Reducing noise by limiting OR expansion to top-3 most relevant terms

### Strategy 3: Wiki Coverage
Currently 47 wiki pages cover the most common topics. The weak categories (Daily Life, Property, Taxes) may lack wiki pages. Examine the wiki index and see which categories have no wiki coverage.

**Note**: Adding wiki pages requires writing new .md files to the wiki/ directory, not modifying serve_fastapi.py. Create a separate experiment for wiki additions.

### Strategy 4: Search Result Ranking
The `smart_search()` function merges results from 4 layers. Current ranking:
- Wiki first (by keyword match score)
- QMD second (by relevance score)
- FTS third (by FTS5 rank, active > unknown > repealed)

Experiment with:
- Boosting results where decreto number appears in the question
- Boosting results with higher text_quality scores
- Penalizing very old documents when newer amendments exist
- Re-ranking after merge based on combined signals

### Strategy 5: Context Window Optimization
Currently the LLM gets up to 10 sources, 40K chars. Experiment with:
- Fewer but higher-quality sources (e.g., 5 sources, each more complete)
- Including resumen (summary) fields in context for FTS results
- Including emission_date and status more prominently
- Ordering context: most relevant first vs most recent first

### Strategy 6: System Prompt Tuning
The `SYSTEM_PROMPT` tells the LLM how to answer. Small tweaks can help:
- More explicit instruction to cite ALL relevant decretos (not just primary one)
- Instruction to cross-reference when multiple laws apply
- More concise answers to stay within token limits
- Better handling of "I don't know" cases (search harder, don't hedge)

### Strategy 7: Decreto Cross-Reference
When a question matches a decreto that has amendments, the system should automatically pull in the amendment text too. Currently amendments are only included if they happen to match the search query independently.

### Strategy 8: Scoring Calibration
The evaluation checks for decreto citation patterns like `Decreto N` or `D-N`. If the LLM uses slightly different formats (e.g., "Legislative Decree No. 15"), the scoring may miss valid citations. This is an evaluation fix, not a search fix.

---

## Constraints

- **Do not modify** `run_test_questions.py` or `test_questions.json` — these are the fixed evaluation.
- **Do not modify** `prepare.py` equivalent files (build-search-db.py, etc.) — the corpus is fixed.
- **Do not change** the API contract (endpoints, request/response shapes).
- **Keep the server single-file** — all changes in `serve_fastapi.py`.
- **No new dependencies** — only use what's in requirements.txt.
- **Protect strong categories** — an experiment that improves Daily Life by 10% but drops Employment by 5% is net negative unless the total score improved.

---

## Experiment Log Format

After each experiment, record:
```
## Experiment N: [short description]
- Change: [what was modified]
- Hypothesis: [why this should help]
- Result: [score]% (was [prev]%)
- Category changes: [which went up/down]
- Decision: KEEP / REVERT
```

---

## Getting Started

To run an experiment:
```bash
# 1. Start the server
cd C:\Users\10700K\Desktop\el-salvador-laws
python scripts/serve_fastapi.py --port 4200

# 2. In another terminal, run the test
python scripts/run_test_questions.py

# 3. Check results
cat scripts/test_results.json | python -m json.tool | head -30
```

The experiment runner (`scripts/experiment.py`) automates this loop.
