#!/usr/bin/env python3
"""
Autoresearch-style experiment runner for the El Salvador Law Agent.

Inspired by github.com/karpathy/autoresearch — autonomous experimentation loop:
  1. Save current serve_fastapi.py as baseline
  2. Start the server
  3. Run test questions, record score
  4. If score improved over best: keep. If regressed: revert.
  5. Log everything to experiments/log.json

Usage:
    # Run a single evaluation (no code changes, just measure current score)
    python scripts/experiment.py --eval

    # Run a single evaluation with a label
    python scripts/experiment.py --eval --label "baseline"

    # Compare two results
    python scripts/experiment.py --compare experiments/001_baseline.json experiments/002_expand_terms.json
"""

import json
import os
import shutil
import subprocess
import sys
import time
import signal
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(line_buffering=True, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
SERVER_FILE = SCRIPTS / "serve_fastapi.py"
TEST_RUNNER = SCRIPTS / "run_test_questions.py"
TEST_RESULTS = SCRIPTS / "test_results.json"
EXPERIMENTS_DIR = ROOT / "experiments"
LOG_FILE = EXPERIMENTS_DIR / "log.json"
BEST_FILE = EXPERIMENTS_DIR / "best.json"
BACKUPS_DIR = EXPERIMENTS_DIR / "backups"

PORT = int(os.environ.get("PORT", "4200"))
BASE_URL = f"http://localhost:{PORT}"
SERVER_STARTUP_WAIT = 8  # seconds to wait for server to start
SERVER_HEALTH_TIMEOUT = 30  # seconds to wait for /healthz


def ensure_dirs():
    EXPERIMENTS_DIR.mkdir(exist_ok=True)
    BACKUPS_DIR.mkdir(exist_ok=True)


def get_next_experiment_number() -> int:
    """Get the next experiment number from existing files."""
    existing = list(EXPERIMENTS_DIR.glob("*.json"))
    nums = []
    for f in existing:
        try:
            num = int(f.stem.split("_")[0])
            nums.append(num)
        except (ValueError, IndexError):
            pass
    return max(nums, default=0) + 1


def backup_server(experiment_num: int, label: str) -> Path:
    """Backup current serve_fastapi.py before any modification."""
    backup_path = BACKUPS_DIR / f"{experiment_num:03d}_{label}_serve_fastapi.py"
    shutil.copy2(SERVER_FILE, backup_path)
    return backup_path


def restore_server(backup_path: Path):
    """Restore serve_fastapi.py from a backup."""
    shutil.copy2(backup_path, SERVER_FILE)
    print(f"  Restored server from {backup_path.name}")


def start_server() -> subprocess.Popen:
    """Start the FastAPI server as a subprocess."""
    env = os.environ.copy()
    env["PORT"] = str(PORT)
    proc = subprocess.Popen(
        [sys.executable, str(SERVER_FILE), "--port", str(PORT)],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    print(f"  Server started (PID {proc.pid}), waiting for startup...")
    time.sleep(SERVER_STARTUP_WAIT)
    return proc


def wait_for_health(timeout: int = SERVER_HEALTH_TIMEOUT) -> bool:
    """Wait for the server to respond to /healthz."""
    import urllib.request
    start = time.time()
    while time.time() - start < timeout:
        try:
            req = urllib.request.urlopen(f"{BASE_URL}/healthz", timeout=3)
            if req.status == 200:
                print("  Server is healthy!")
                return True
        except Exception:
            pass
        time.sleep(1)
    print("  WARNING: Server health check timed out")
    return False


def stop_server(proc: subprocess.Popen):
    """Stop the server subprocess."""
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    print(f"  Server stopped (PID {proc.pid})")


def run_tests() -> dict:
    """Run the test suite and return the results."""
    print(f"  Running 76 test questions against {BASE_URL}...")
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
        timeout=2400,  # 40 minute timeout for full suite
    )
    print(result.stdout[-500:] if len(result.stdout) > 500 else result.stdout)
    if result.returncode != 0:
        print(f"  Test runner error: {result.stderr[-300:]}")
        return {}

    if TEST_RESULTS.exists():
        with open(TEST_RESULTS) as f:
            return json.load(f)
    return {}


def load_best() -> dict:
    """Load the current best score."""
    if BEST_FILE.exists():
        with open(BEST_FILE) as f:
            return json.load(f)
    return {"percent": 0, "total_score": 0, "total_max": 304, "experiment": "none"}


def save_best(result: dict, experiment_num: int, label: str):
    """Save a new best score."""
    summary = result.get("summary", {})
    best = {
        "percent": summary.get("percent", 0),
        "total_score": summary.get("total_score", 0),
        "total_max": summary.get("total_max", 304),
        "experiment": f"{experiment_num:03d}_{label}",
        "timestamp": datetime.now().isoformat(),
        "categories": summary.get("categories", {}),
    }
    with open(BEST_FILE, "w") as f:
        json.dump(best, f, indent=2)


def save_experiment(experiment_num: int, label: str, result: dict, decision: str,
                    hypothesis: str = "", change: str = ""):
    """Save experiment results to a numbered JSON file."""
    summary = result.get("summary", {})
    experiment = {
        "number": experiment_num,
        "label": label,
        "timestamp": datetime.now().isoformat(),
        "hypothesis": hypothesis,
        "change": change,
        "decision": decision,
        "score": {
            "percent": summary.get("percent", 0),
            "total_score": summary.get("total_score", 0),
            "total_max": summary.get("total_max", 304),
            "passes": summary.get("passes", 0),
            "warns": summary.get("warns", 0),
            "fails": summary.get("fails", 0),
            "errors": summary.get("errors", 0),
        },
        "categories": summary.get("categories", {}),
        "elapsed_seconds": summary.get("elapsed_seconds", 0),
    }

    filename = EXPERIMENTS_DIR / f"{experiment_num:03d}_{label}.json"
    with open(filename, "w") as f:
        json.dump(experiment, f, indent=2)
    print(f"  Saved experiment to {filename.name}")

    # Append to log
    log = []
    if LOG_FILE.exists():
        with open(LOG_FILE) as f:
            log = json.load(f)
    log.append(experiment)
    with open(LOG_FILE, "w") as f:
        json.dump(log, f, indent=2)


def compare_results(file_a: str, file_b: str):
    """Compare two experiment result files."""
    with open(file_a) as f:
        a = json.load(f)
    with open(file_b) as f:
        b = json.load(f)

    sa = a.get("score", a.get("summary", {}))
    sb = b.get("score", b.get("summary", {}))
    la = a.get("label", Path(file_a).stem)
    lb = b.get("label", Path(file_b).stem)

    pa = sa.get("percent", 0)
    pb = sb.get("percent", 0)
    diff = pb - pa

    print(f"\n{'='*60}")
    print(f"COMPARISON: {la} -> {lb}")
    print(f"{'='*60}")
    print(f"  {la}: {pa}% ({sa.get('total_score',0)}/{sa.get('total_max',0)})")
    print(f"  {lb}: {pb}% ({sb.get('total_score',0)}/{sb.get('total_max',0)})")
    print(f"  Delta: {diff:+.1f}%")
    print()

    ca = a.get("categories", {})
    cb = b.get("categories", {})
    all_cats = sorted(set(list(ca.keys()) + list(cb.keys())))
    if all_cats:
        print("  By Category:")
        for cat in all_cats:
            va = ca.get(cat, {})
            vb = cb.get(cat, {})
            pct_a = 100 * va.get("total_score", 0) / va.get("total_max", 1) if va.get("total_max", 0) > 0 else 0
            pct_b = 100 * vb.get("total_score", 0) / vb.get("total_max", 1) if vb.get("total_max", 0) > 0 else 0
            d = pct_b - pct_a
            arrow = "^" if d > 0 else "v" if d < 0 else "="
            print(f"    {cat:15s}: {pct_a:5.0f}% -> {pct_b:5.0f}% ({d:+.0f}% {arrow})")


def run_eval(label: str = "eval"):
    """Run a single evaluation without modifying code."""
    ensure_dirs()
    num = get_next_experiment_number()

    print(f"\n{'='*60}")
    print(f"EXPERIMENT {num:03d}: {label} (eval only)")
    print(f"{'='*60}")

    # Backup current state
    backup_path = backup_server(num, label)

    # Start server
    proc = start_server()
    try:
        if not wait_for_health():
            print("  ABORT: Server failed to start")
            return

        # Run tests
        result = run_tests()
        if not result:
            print("  ABORT: Test runner failed")
            return

        summary = result.get("summary", {})
        pct = summary.get("percent", 0)
        best = load_best()
        best_pct = best.get("percent", 0)

        print(f"\n  Score: {pct}% (best: {best_pct}%)")

        if pct > best_pct:
            print(f"  NEW BEST! {pct}% > {best_pct}%")
            save_best(result, num, label)
            decision = "NEW_BEST"
        elif pct == best_pct:
            decision = "TIED"
        else:
            decision = "BELOW_BEST"

        save_experiment(num, label, result, decision)

        # Print category breakdown
        cats = summary.get("categories", {})
        if cats:
            print("\n  Categories:")
            for cat, stats in sorted(cats.items()):
                cat_pct = 100 * stats["total_score"] / stats["total_max"] if stats["total_max"] > 0 else 0
                print(f"    {cat:15s}: {cat_pct:5.0f}%")

    finally:
        stop_server(proc)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Autoresearch experiment runner")
    parser.add_argument("--eval", action="store_true", help="Run evaluation only (no code changes)")
    parser.add_argument("--label", default="eval", help="Label for this experiment")
    parser.add_argument("--compare", nargs=2, metavar=("A", "B"), help="Compare two result files")
    parser.add_argument("--best", action="store_true", help="Show current best score")
    parser.add_argument("--log", action="store_true", help="Show experiment log")
    args = parser.parse_args()

    ensure_dirs()

    if args.compare:
        compare_results(args.compare[0], args.compare[1])
    elif args.best:
        best = load_best()
        if best["percent"] > 0:
            print(f"Best: {best['percent']}% (experiment {best['experiment']}, {best.get('timestamp', '?')})")
        else:
            print("No experiments run yet.")
    elif args.log:
        if LOG_FILE.exists():
            with open(LOG_FILE) as f:
                log = json.load(f)
            print(f"{'#':>3s}  {'Label':20s}  {'Score':>6s}  {'Decision':12s}  {'Time':>6s}")
            print("-" * 60)
            for entry in log:
                s = entry.get("score", {})
                print(f"{entry['number']:3d}  {entry['label']:20s}  {s.get('percent',0):5.1f}%  {entry['decision']:12s}  {s.get('elapsed_seconds',0)/60:5.1f}m")
        else:
            print("No experiments run yet.")
    elif args.eval:
        run_eval(args.label)
    else:
        parser.print_help()
        print("\n\nTypical workflow:")
        print("  1. python scripts/experiment.py --eval --label baseline")
        print("  2. [AI agent modifies scripts/serve_fastapi.py]")
        print("  3. python scripts/experiment.py --eval --label my_change")
        print("  4. python scripts/experiment.py --compare experiments/001_baseline.json experiments/002_my_change.json")
        print("  5. python scripts/experiment.py --log")


if __name__ == "__main__":
    main()
