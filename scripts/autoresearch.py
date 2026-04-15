#!/usr/bin/env python3
"""
Autoresearch loop for El Salvador Law Agent.

Runs autonomous experiments to improve test scores.
Each experiment modifies serve_fastapi.py, tests, and keeps/reverts.

Usage:
    python scripts/autoresearch.py

This will:
1. Establish baseline score
2. Apply experiments from a queue of improvements
3. Keep changes that improve the score, revert those that don't
4. Log everything to experiments/log.json
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(line_buffering=True, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
SERVER_FILE = SCRIPTS / "serve_fastapi.py"
TEST_RUNNER = SCRIPTS / "test_retrieval.py"
TEST_RESULTS = SCRIPTS / "retrieval_results.json"
EXPERIMENTS_DIR = ROOT / "experiments"
LOG_FILE = EXPERIMENTS_DIR / "log.json"
BEST_FILE = EXPERIMENTS_DIR / "best.json"
BACKUPS_DIR = EXPERIMENTS_DIR / "backups"
STATUS_FILE = EXPERIMENTS_DIR / "status.json"

PORT = int(os.environ.get("AUTORESEARCH_PORT", os.environ.get("PORT", "4201")))
BASE_URL = f"http://localhost:{PORT}"


def ensure_dirs():
    EXPERIMENTS_DIR.mkdir(exist_ok=True)
    BACKUPS_DIR.mkdir(exist_ok=True)


def update_status(status: str, experiment: str = "", score: float = 0, error: str = ""):
    """Update status file so the monitor can check progress."""
    with open(STATUS_FILE, "w") as f:
        json.dump({
            "status": status,
            "experiment": experiment,
            "score": score,
            "error": error,
            "timestamp": datetime.now().isoformat(),
            "pid": os.getpid(),
        }, f, indent=2)


def get_next_num() -> int:
    existing = list(EXPERIMENTS_DIR.glob("*.json"))
    nums = []
    for f in existing:
        try:
            nums.append(int(f.stem.split("_")[0]))
        except (ValueError, IndexError):
            pass
    return max(nums, default=0) + 1


def start_server():
    env = os.environ.copy()
    env["PORT"] = str(PORT)
    # Ensure API key is passed through
    if not env.get("ANTHROPIC_API_KEY"):
        print("  WARNING: ANTHROPIC_API_KEY not set! LLM synthesis will be unavailable.")
        print("  Set it before running: export ANTHROPIC_API_KEY='sk-ant-...'")
    # Write stdout/stderr to a log file to avoid pipe buffer deadlocks
    server_log = EXPERIMENTS_DIR / "server_output.log"
    log_fh = open(server_log, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, str(SERVER_FILE), "--port", str(PORT)],
        cwd=str(ROOT),
        env=env,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
    )
    proc._log_fh = log_fh  # keep reference so we can close it later
    print(f"  Server started (PID {proc.pid}), log: {server_log}")
    time.sleep(10)

    # Wait for health
    import urllib.request
    for attempt in range(40):
        try:
            req = urllib.request.urlopen(f"{BASE_URL}/healthz", timeout=3)
            if req.status == 200:
                print("  Server healthy")
                return proc
        except Exception:
            pass
        time.sleep(1)

    print("  WARNING: Server health check timed out, proceeding anyway")
    return proc


def stop_server(proc):
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    # Close the log file handle
    if hasattr(proc, '_log_fh'):
        try:
            proc._log_fh.close()
        except Exception:
            pass


def run_tests() -> dict:
    env = os.environ.copy()
    env["BASE_URL"] = BASE_URL
    result = subprocess.run(
        [sys.executable, str(TEST_RUNNER)],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=2400,
    )
    # Print last 500 chars of output
    output = result.stdout or ""
    print(output[-500:] if len(output) > 500 else output)

    if TEST_RESULTS.exists():
        with open(TEST_RESULTS) as f:
            return json.load(f)
    return {}


def load_best() -> dict:
    if BEST_FILE.exists():
        with open(BEST_FILE) as f:
            return json.load(f)
    return {"percent": 0}


def save_best(result: dict, num: int, label: str):
    s = result.get("summary", {})
    with open(BEST_FILE, "w") as f:
        json.dump({
            "percent": s.get("percent", 0),
            "total_score": s.get("total_score", 0),
            "total_max": s.get("total_max", 304),
            "experiment": f"{num:03d}_{label}",
            "timestamp": datetime.now().isoformat(),
            "categories": s.get("categories", {}),
        }, f, indent=2)


def save_experiment(num: int, label: str, result: dict, decision: str, change: str = ""):
    s = result.get("summary", {})
    entry = {
        "number": num,
        "label": label,
        "timestamp": datetime.now().isoformat(),
        "change": change,
        "decision": decision,
        "score": {
            "percent": s.get("percent", 0),
            "total_score": s.get("total_score", 0),
            "total_max": s.get("total_max", 304),
            "passes": s.get("passes", 0),
            "warns": s.get("warns", 0),
            "fails": s.get("fails", 0),
        },
        "categories": s.get("categories", {}),
    }

    with open(EXPERIMENTS_DIR / f"{num:03d}_{label}.json", "w") as f:
        json.dump(entry, f, indent=2)

    log = []
    if LOG_FILE.exists():
        with open(LOG_FILE) as f:
            log = json.load(f)
    log.append(entry)
    with open(LOG_FILE, "w") as f:
        json.dump(log, f, indent=2)

    return entry


def read_server():
    return SERVER_FILE.read_text(encoding="utf-8")


def write_server(content: str):
    SERVER_FILE.write_text(content, encoding="utf-8")


def backup_server(num: int, label: str) -> Path:
    p = BACKUPS_DIR / f"{num:03d}_{label}.py"
    shutil.copy2(SERVER_FILE, p)
    return p


# ---------------------------------------------------------------------------
# Experiment definitions — each is a function that takes the server source
# and returns modified source + description
# ---------------------------------------------------------------------------


def exp_add_missing_legal_terms(src: str) -> tuple[str, str]:
    """Add missing term mappings that cause WARN questions to miss decretos."""
    new_terms = {
        # Taxes — foreign income, small biz, penalties
        "file taxes": "declaracion impuesto renta contribuyente plazo DGII",
        "tax filing": "declaracion impuesto renta plazo abril codigo tributario",
        "foreign income": "renta extranjera ingreso exterior fuente extranjera territorial",
        "small business tax": "renta persona natural pequeno contribuyente regimen simplificado",
        "tax penalty": "multa sancion impuesto evasion codigo tributario",
        # Property
        "property title": "registro propiedad escritura titulo inmueble",
        "property registry": "registro propiedad inmueble escritura publica CNR",
        "evict": "desalojo arrendamiento inquilino lanzamiento",
        "kick out": "desalojo arrendamiento inquilino lanzamiento",
        # Criminal
        "drinking age": "bebidas alcoholicas menor edad expendio penal corrupcion menores",
        "carry a gun": "armas fuego portacion tenencia licencia especial",
        "state of exception": "regimen excepcion suspension derechos detencion",
        "money laundering": "lavado dinero activos penal crimen organizado",
        # Daily life
        "lemon law": "consumidor defectuoso garantia devolucion producto comercio codigo comercial vicios ocultos",
        "driver license": "licencia conducir transito extranjero migracion",
        "drivers license": "licencia conducir transito extranjero migracion",
        "chivo": "bitcoin chivo billetera moneda digital decreto 57",
        "chivo wallet": "bitcoin chivo billetera moneda digital decreto 57",
        "crypto tax": "bitcoin criptomoneda impuesto renta moneda digital",
        # Government
        "garbage": "municipalidad servicio basura desecho residuo",
        "garbage collection": "municipalidad servicio basura desecho residuo ordenamiento territorial",
        # Family
        "restraining order": "medidas proteccion violencia intrafamiliar",
        "domestic violence": "violencia intrafamiliar",
        # Edge cases
        "despido": "despido injustificado laboral indemnizacion trabajo patrono",
        "injustificado": "despido injustificado laboral indemnizacion",
        "car accident": "accidente transito vehicular dano seguro responsabilidad civil",
        "drunk driving": "conducir ebriedad estado ebriedad transito penal",
    }

    # Find the end of LEGAL_TERMS dict
    marker = "}\n\n# ---------------------------------------------------------------------------\n# System prompt"
    if marker not in src:
        return src, "SKIP: couldn't find LEGAL_TERMS end marker"

    # Build new entries (only add ones not already present)
    additions = []
    for eng, esp in new_terms.items():
        # Check if already present (exact key match)
        pattern = f'    "{eng}":'
        if pattern not in src:
            additions.append(f'    "{eng}": "{esp}",')

    if not additions:
        return src, "SKIP: all terms already present"

    insert = "\n".join(additions)
    src = src.replace(marker, f"{insert}\n{marker}")
    return src, f"Added {len(additions)} missing LEGAL_TERMS entries for weak categories"


def exp_improve_decreto_citation_prompt(src: str) -> tuple[str, str]:
    """Strengthen the system prompt to always cite decreto numbers."""
    old = '2. ALWAYS cite decreto numbers explicitly. Every answer must include at least one "Decreto N (year)" citation.'
    new = '2. ALWAYS cite decreto numbers explicitly using the format "Decreto N (year)". Every answer MUST include the specific decreto number from the source material. If a source says "Decreto 1030" or "D-1030", you MUST write "Decreto 1030 (1997)" in your answer. Never describe a law only by name — always include the decreto number. This is the MOST IMPORTANT rule.'
    if old not in src:
        return src, "SKIP: couldn't find decreto citation rule"
    return src.replace(old, new), "Strengthened decreto citation instruction in system prompt"


def exp_include_resumen_in_fts_context(src: str) -> tuple[str, str]:
    """Include the resumen (summary) field in FTS context to help the LLM understand what each decreto is about."""
    old = '''        label = f"Source {len(sources) + 1} [{layer.upper()}]: "
        if decreto:
            label += f"Decreto {decreto}"
        else:
            label += title
        year = item.get("year", "?")
        label += f" ({year}){status_tag}"
        if item.get("materia"):
            label += f" -- {item['materia']}"
        articles = item.get("articles", [])
        if articles:
            label += f", Articles: {', '.join(articles)}"

        context_parts.append(f"[{label}]\\n{content}\\n")'''

    new = '''        label = f"Source {len(sources) + 1} [{layer.upper()}]: "
        if decreto:
            label += f"Decreto {decreto}"
        else:
            label += title
        year = item.get("year", "?")
        label += f" ({year}){status_tag}"
        if item.get("materia"):
            label += f" -- {item['materia']}"
        articles = item.get("articles", [])
        if articles:
            label += f", Articles: {', '.join(articles)}"

        # Include resumen for better LLM context
        resumen = item.get("resumen", "")
        resumen_line = f"\\nSummary: {resumen[:200]}" if resumen else ""
        context_parts.append(f"[{label}]{resumen_line}\\n{content}\\n")'''

    if old not in src:
        return src, "SKIP: couldn't find context_parts.append pattern"
    return src.replace(old, new), "Include resumen/summary in FTS context for better LLM grounding"


def exp_boost_fts_limit(src: str) -> tuple[str, str]:
    """Increase FTS result limit to get more diverse decreto coverage."""
    old = "def _search_fts_expanded_sync(question: str, limit: int = 12) -> list[dict]:"
    new = "def _search_fts_expanded_sync(question: str, limit: int = 18) -> list[dict]:"
    if old not in src:
        return src, "SKIP: couldn't find FTS expanded limit"
    return src.replace(old, new), "Increased FTS expanded search limit from 12 to 18 for broader decreto coverage"


def exp_explicit_decreto_in_context(src: str) -> tuple[str, str]:
    """Add an explicit instruction at the end of the user message to cite specific decretos found."""
    old = 'Answer the question using these sources. Cite decreto numbers. Be direct and concise."""'
    new = '''Answer the question using these sources. CRITICAL: For EVERY source above, cite its Decreto number in your answer using the exact format "Decreto N (year)". If a source is labeled "Decreto 1030 (1997)", you must write "Decreto 1030 (1997)" in your response. Never omit decreto numbers."""'''
    if old not in src:
        return src, "SKIP: couldn't find answer instruction"
    return src.replace(old, new), "Added explicit decreto citation instruction in user message"


def exp_add_decreto_search_layer(src: str) -> tuple[str, str]:
    """When a topic maps to a known decreto, directly search for that decreto number in FTS.
    This is the biggest gap: the search finds related text but misses the canonical decreto."""

    # We need to add a TOPIC_DECRETOS map and inject decreto-specific searches
    topic_decreto_map = '''
# Topic-to-decreto mapping for direct decreto injection
TOPIC_DECRETOS: dict[str, list[str]] = {
    "criminal": ["1030"], "penal": ["1030"], "theft": ["1030"], "murder": ["1030"],
    "assault": ["1030"], "drugs": ["1030"], "weed": ["1030"], "gun": ["1030"],
    "knife": ["1030"], "fraud": ["1030"], "money laundering": ["1030"],
    "drinking age": ["1030"], "drunk": ["1030"], "prison": ["1030"],
    "detained": ["1030"], "state of exception": ["1030"], "self defense": ["1030"],
    "threaten": ["1030"], "domestic violence": ["1030", "677"],
    "property": ["644"], "land": ["644"], "rent": ["644"], "landlord": ["644"],
    "tenant": ["644"], "evict": ["644"], "squatter": ["644"], "building permit": ["644"],
    "inheritance": ["644", "677"], "adverse possession": ["644"], "beachfront": ["644"],
    "real estate": ["644"], "property title": ["644"], "buy house": ["644"],
    "tax": ["134", "230"], "income tax": ["134"], "IVA": ["296"],
    "tax penalty": ["230"], "codigo tributario": ["230"], "file taxes": ["134", "230"],
    "business": ["671"], "LLC": ["671"], "trademark": ["671"], "corporation": ["671"],
    "comerciante": ["671"], "close a business": ["671"], "sociedad anonima": ["671"],
    "commercial code": ["671"],
    "labor": ["15"], "employment": ["15"], "minimum wage": ["15"], "vacation": ["15"],
    "severance": ["15"], "overtime": ["15"], "maternity": ["15"], "aguinaldo": ["15"],
    "despido": ["15"], "hire": ["15"], "payroll": ["15"],
    "family": ["677"], "divorce": ["677"], "child support": ["677"],
    "adoption": ["677"], "marriage": ["677"], "custody": ["677"],
    "immigration": ["286"], "visa": ["286"], "residency": ["286"],
    "citizenship": ["286"], "passport": ["286"], "driver license": ["286"],
    "bitcoin": ["57"], "crypto": ["57"], "chivo": ["57"],
    "consumer": ["776"], "refund": ["776"], "defective": ["776"], "lemon law": ["776"],
    "municipal": ["274"], "garbage": ["274"], "zoning": ["274"],
}
'''

    # Insert after LEGAL_TERMS dict
    marker = "# ---------------------------------------------------------------------------\n# System prompt for LLM"
    if marker not in src:
        return src, "SKIP: couldn't find system prompt marker"

    src = src.replace(marker, topic_decreto_map + "\n" + marker)

    # Now modify expand_query to inject decreto-specific queries
    old_expand = """    return queries if queries else [question]"""
    new_expand = """    # Inject direct decreto queries based on topic
    q_lower_check = question.lower()
    injected_decretos: set[str] = set()
    for topic_key, decreto_list in TOPIC_DECRETOS.items():
        if topic_key in q_lower_check:
            for d in decreto_list:
                if d not in injected_decretos:
                    injected_decretos.add(d)
                    queries.append(f"decreto {d}")

    return queries if queries else [question]"""

    if old_expand not in src:
        return src, "SKIP: couldn't find expand_query return"

    src = src.replace(old_expand, new_expand)
    return src, "Added TOPIC_DECRETOS map: directly searches for canonical decreto numbers when topic is detected"


def exp_lower_wiki_threshold(src: str) -> tuple[str, str]:
    """Lower the wiki match threshold to let more wiki pages through."""
    old = "        if score > 15.0:"
    new = "        if score > 10.0:"
    if old not in src:
        return src, "SKIP: couldn't find wiki score threshold"
    return src.replace(old, new), "Lowered wiki match threshold from 15.0 to 10.0 for broader wiki coverage"


def exp_add_decreto_to_fts_query(src: str) -> tuple[str, str]:
    """When FTS finds results, also search for 'decreto N' directly to ensure
    the canonical law shows up in results."""
    old = """    all_results.sort(key=lambda r: r["relevance"])
    return all_results[:limit]"""

    new = """    # Also directly search for any decreto numbers found so far to ensure canonical text is included
    found_decretos = set()
    for r in all_results:
        dn = r.get("decree_no", "")
        if dn and dn not in found_decretos:
            found_decretos.add(dn)
    # Search for each decreto directly to get its primary text
    for dn in list(found_decretos)[:5]:
        try:
            decreto_results = _search_fts_sync(f"decreto {dn}", limit=2)
            for r in decreto_results:
                cid = r["chunk_id"]
                if cid not in seen_chunks:
                    seen_chunks.add(cid)
                    all_results.append(r)
        except Exception:
            pass

    all_results.sort(key=lambda r: r["relevance"])
    return all_results[:limit]"""

    if old not in src:
        return src, "SKIP: couldn't find FTS sort/return"
    return src.replace(old, new), "After FTS search, also directly search found decreto numbers to include canonical text"


# Queue of experiments to run (in order) — Round 2: retrieval-focused
EXPERIMENT_QUEUE = [
    ("topic_decreto_inject", exp_add_decreto_search_layer),
    ("lower_wiki_threshold", exp_lower_wiki_threshold),
    ("decreto_fts_enrich", exp_add_decreto_to_fts_query),
    ("include_resumen", exp_include_resumen_in_fts_context),
    ("boost_fts_limit", exp_boost_fts_limit),
]


def run_experiment(num: int, label: str, modify_fn, best_pct: float) -> tuple[float, bool]:
    """Run a single experiment: modify, test, keep/revert."""
    print(f"\n{'='*60}")
    print(f"EXPERIMENT {num:03d}: {label}")
    print(f"{'='*60}")

    update_status("running", label)

    # Backup current server
    backup = backup_server(num, label)

    # Apply modification
    src = read_server()
    new_src, description = modify_fn(src)

    if description.startswith("SKIP"):
        print(f"  {description}")
        save_experiment(num, label, {"summary": {"percent": best_pct}}, "SKIPPED", description)
        update_status("skipped", label, best_pct)
        return best_pct, False

    print(f"  Change: {description}")
    write_server(new_src)

    # Start server and test
    proc = start_server()
    try:
        result = run_tests()
        if not result:
            print("  ABORT: Test runner failed")
            write_server(src)  # Revert
            save_experiment(num, label, {"summary": {"percent": 0}}, "ERROR", description)
            update_status("error", label, error="test runner failed")
            return best_pct, False

        pct = result.get("summary", {}).get("percent", 0)
        print(f"\n  Score: {pct}% (best: {best_pct}%)")

        if pct > best_pct:
            print(f"  KEEP! {pct}% > {best_pct}%")
            save_experiment(num, label, result, "KEEP", description)
            save_best(result, num, label)
            update_status("completed", label, pct)
            return pct, True
        elif pct == best_pct:
            print(f"  TIED at {pct}% — keeping change (no regression)")
            save_experiment(num, label, result, "KEEP_TIED", description)
            update_status("completed", label, pct)
            return pct, True
        else:
            print(f"  REVERT: {pct}% < {best_pct}%")
            write_server(src)  # Revert to pre-experiment state
            save_experiment(num, label, result, "REVERT", description)
            update_status("reverted", label, pct)
            return best_pct, False
    finally:
        stop_server(proc)


def main():
    ensure_dirs()
    print(f"Autoresearch loop starting at {datetime.now().isoformat()}")
    print(f"Server file: {SERVER_FILE}")
    print(f"Test runner: {TEST_RUNNER}")
    print(f"Experiments dir: {EXPERIMENTS_DIR}")
    print()

    # Step 1: Baseline evaluation
    num = get_next_num()
    best = load_best()
    best_pct = best.get("percent", 0)

    if best_pct == 0:
        print("No baseline found. Running baseline evaluation...")
        update_status("baseline", "baseline")
        backup_server(num, "baseline")
        proc = start_server()
        try:
            result = run_tests()
            if result:
                best_pct = result.get("summary", {}).get("percent", 0)
                save_experiment(num, "baseline", result, "BASELINE")
                save_best(result, num, "baseline")
                print(f"\nBaseline: {best_pct}%")
            else:
                print("FATAL: Baseline test failed")
                update_status("error", "baseline", error="baseline test failed")
                return
        finally:
            stop_server(proc)
        num += 1
    else:
        print(f"Existing baseline: {best_pct}% (from {best.get('experiment', '?')})")

    # Step 2: Run experiment queue
    for label, modify_fn in EXPERIMENT_QUEUE:
        num = get_next_num()
        try:
            best_pct, kept = run_experiment(num, label, modify_fn, best_pct)
        except Exception as e:
            print(f"  EXCEPTION in experiment {label}: {e}")
            traceback.print_exc()
            update_status("error", label, error=str(e))
            # Restore from most recent backup if something went wrong
            backups = sorted(BACKUPS_DIR.glob("*.py"), reverse=True)
            if backups:
                shutil.copy2(backups[0], SERVER_FILE)
                print(f"  Restored from {backups[0].name}")

    # Summary
    print(f"\n{'='*60}")
    print("AUTORESEARCH COMPLETE")
    print(f"{'='*60}")
    final_best = load_best()
    print(f"Final best: {final_best.get('percent', 0)}% (experiment: {final_best.get('experiment', '?')})")
    update_status("complete", "all", final_best.get("percent", 0))

    if LOG_FILE.exists():
        with open(LOG_FILE) as f:
            log = json.load(f)
        print(f"\nExperiment log ({len(log)} experiments):")
        for e in log:
            s = e.get("score", {})
            print(f"  {e['number']:3d} {e['label']:30s} {s.get('percent',0):5.1f}%  {e['decision']}")


if __name__ == "__main__":
    main()
