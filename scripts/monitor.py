#!/usr/bin/env python3
"""
Monitor script for autoresearch loop.
Checks if the autoresearch process is still running and making progress.
If stuck, diagnoses the issue and restarts.

Called by cron every 20 minutes.
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.stdout.reconfigure(line_buffering=True, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
EXPERIMENTS_DIR = ROOT / "experiments"
STATUS_FILE = EXPERIMENTS_DIR / "status.json"
LOG_FILE = EXPERIMENTS_DIR / "log.json"
AUTORESEARCH_SCRIPT = ROOT / "scripts" / "autoresearch.py"
MONITOR_LOG = EXPERIMENTS_DIR / "monitor.log"


def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(MONITOR_LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def get_status() -> dict:
    if STATUS_FILE.exists():
        with open(STATUS_FILE) as f:
            return json.load(f)
    return {}


def is_process_running(pid: int) -> bool:
    """Check if a process with given PID is running."""
    try:
        # On Windows, use tasklist
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True, timeout=10
        )
        return str(pid) in result.stdout
    except Exception:
        return False


def get_experiment_count() -> int:
    if LOG_FILE.exists():
        with open(LOG_FILE) as f:
            return len(json.load(f))
    return 0


def check_and_restart():
    """Check autoresearch status and restart if needed."""
    EXPERIMENTS_DIR.mkdir(exist_ok=True)

    status = get_status()
    log(f"Monitor check - Status: {json.dumps(status, default=str)}")

    # Case 1: No status file — autoresearch never started
    if not status:
        log("No status file found. Starting autoresearch...")
        start_autoresearch()
        return

    # Case 2: Process completed all experiments
    if status.get("status") == "complete":
        log(f"Autoresearch completed. Final score: {status.get('score', '?')}%")
        log("All experiments done. Nothing to restart.")
        return

    # Case 3: Check if the process is still alive
    pid = status.get("pid", 0)
    if pid and is_process_running(pid):
        # Process is running — check if it's making progress
        ts = status.get("timestamp", "")
        if ts:
            try:
                last_update = datetime.fromisoformat(ts)
                age = datetime.now() - last_update
                if age > timedelta(minutes=45):
                    log(f"Process {pid} alive but status stale ({age}). May be stuck.")
                    log("Killing stale process and restarting...")
                    kill_process(pid)
                    time.sleep(3)
                    start_autoresearch()
                else:
                    log(f"Process {pid} running, last update {age.seconds//60}m ago. Status: {status.get('status')} / {status.get('experiment')}")
            except Exception:
                log(f"Process {pid} alive, status: {status.get('status')}")
        return

    # Case 4: Process died (PID not running)
    if status.get("status") not in ("complete", "error"):
        log(f"Process {pid} not running. Last status: {status.get('status')} / {status.get('experiment')}")
        log("Restarting autoresearch...")
        start_autoresearch()
        return

    # Case 5: Error state
    if status.get("status") == "error":
        error = status.get("error", "unknown")
        log(f"Autoresearch in error state: {error}")
        log("Attempting restart...")
        start_autoresearch()
        return


def kill_process(pid: int):
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], timeout=10, capture_output=True)
    except Exception as e:
        log(f"Failed to kill PID {pid}: {e}")


def start_autoresearch():
    """Start the autoresearch loop in background."""
    log_path = EXPERIMENTS_DIR / "autoresearch_output.log"
    try:
        with open(log_path, "a") as log_f:
            proc = subprocess.Popen(
                [sys.executable, str(AUTORESEARCH_SCRIPT)],
                cwd=str(ROOT),
                stdout=log_f,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
        log(f"Started autoresearch (PID {proc.pid})")
        log(f"Output logging to {log_path}")
    except Exception as e:
        log(f"Failed to start autoresearch: {e}")


if __name__ == "__main__":
    check_and_restart()
