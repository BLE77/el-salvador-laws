#!/usr/bin/env python3
"""
Minimal test suite for the El Salvador Law Search API.

Usage:
    python scripts/test_api.py
    python scripts/test_api.py --base-url http://localhost:4200
"""

import argparse
import sys
import traceback

import requests

BASE_URL = "http://localhost:4200"

passed = 0
failed = 0
skipped = 0


def report(name, ok, detail=""):
    global passed, failed
    tag = "\033[32m[PASS]\033[0m" if ok else "\033[31m[FAIL]\033[0m"
    msg = f"{tag} {name}"
    if detail:
        msg += f" - {detail}"
    print(msg)
    if ok:
        passed += 1
    else:
        failed += 1


def skip(name, detail=""):
    global skipped
    print(f"\033[33m[SKIP]\033[0m {name} - {detail}")
    skipped += 1


# ---------------------------------------------------------------------------
# 1. Health check
# ---------------------------------------------------------------------------
def test_health_check():
    name = "health_check"
    try:
        r = requests.get(f"{BASE_URL}/healthz", timeout=10)
        if r.status_code == 200:
            report(name, True, "server is running")
            return
    except Exception:
        pass
    # Fallback: /api/stats
    try:
        r = requests.get(f"{BASE_URL}/api/stats", timeout=10)
        report(name, r.status_code == 200, "fallback to /api/stats")
    except Exception as e:
        report(name, False, f"server unreachable: {e}")


# ---------------------------------------------------------------------------
# 2. Search bitcoin returns results with decreto numbers
# ---------------------------------------------------------------------------
def test_search_bitcoin():
    name = "search_bitcoin"
    try:
        r = requests.get(f"{BASE_URL}/api/search", params={"q": "bitcoin"}, timeout=15)
        data = r.json()
        results = data.get("results", [])
        if not results:
            report(name, False, "no results returned")
            return
        # Look for decreto 57 (the Bitcoin Law) anywhere in results
        texts = str(results).lower()
        has_decreto = "decreto" in texts or "57" in texts
        report(name, has_decreto, f"{len(results)} results, decreto reference found" if has_decreto else "expected decreto 57 in results")
    except Exception as e:
        report(name, False, str(e))


# ---------------------------------------------------------------------------
# 3. Search drogas returns Decreto 153
# ---------------------------------------------------------------------------
def test_search_drogas():
    name = "search_drogas"
    try:
        r = requests.get(f"{BASE_URL}/api/search", params={"q": "drogas"}, timeout=15)
        data = r.json()
        results = data.get("results", [])
        texts = str(results)
        has_153 = "153" in texts
        report(name, has_153, f"{len(results)} results, Decreto 153 found" if has_153 else "expected Decreto 153 in results")
    except Exception as e:
        report(name, False, str(e))


# ---------------------------------------------------------------------------
# 4. Decree lookup 153
# ---------------------------------------------------------------------------
def test_decree_153():
    name = "decree_lookup_153"
    try:
        r = requests.get(f"{BASE_URL}/api/decree/153", timeout=15)
        if r.status_code == 200:
            data = r.json()
            has_content = bool(data)
            report(name, has_content, "decree 153 returned content")
        else:
            report(name, False, f"status {r.status_code}")
    except Exception as e:
        report(name, False, str(e))


# ---------------------------------------------------------------------------
# 5. Chat - Is weed legal?
# ---------------------------------------------------------------------------
def test_chat_weed():
    name = "chat_weed_legal"
    try:
        r = requests.post(
            f"{BASE_URL}/api/chat",
            json={"question": "Is weed legal?"},
            timeout=60,
        )
        data = r.json()
        answer = str(data).lower()
        # Skip if LLM unavailable
        if "llm unavailable" in answer or "no llm" in answer or "api key" in answer:
            skip(name, "LLM unavailable")
            return
        has_ref = "153" in answer or "illegal" in answer or "ilegal" in answer or "prohib" in answer
        report(name, has_ref, "answer references decreto 153 or illegality" if has_ref else "expected '153' or 'illegal' in answer")
    except Exception as e:
        report(name, False, str(e))


# ---------------------------------------------------------------------------
# 6. Chat - What is the Bitcoin law?
# ---------------------------------------------------------------------------
def test_chat_bitcoin():
    name = "chat_bitcoin_law"
    try:
        r = requests.post(
            f"{BASE_URL}/api/chat",
            json={"question": "What is the Bitcoin law?"},
            timeout=60,
        )
        data = r.json()
        answer = str(data).lower()
        if "llm unavailable" in answer or "no llm" in answer or "api key" in answer:
            skip(name, "LLM unavailable")
            return
        has_57 = "57" in answer
        report(name, has_57, "answer references decreto 57" if has_57 else "expected '57' in answer")
    except Exception as e:
        report(name, False, str(e))


# ---------------------------------------------------------------------------
# 7. Stats endpoint - documents > 8000, chunks > 80000
# ---------------------------------------------------------------------------
def test_stats():
    name = "stats_counts"
    try:
        r = requests.get(f"{BASE_URL}/api/stats", timeout=10)
        data = r.json()
        docs = data.get("documents", 0)
        chunks = data.get("chunks", 0)
        ok = docs > 8000 and chunks > 80000
        report(name, ok, f"documents={docs}, chunks={chunks}")
    except Exception as e:
        report(name, False, str(e))


# ---------------------------------------------------------------------------
# 8. Browse endpoint returns categories
# ---------------------------------------------------------------------------
def test_browse():
    name = "browse_categories"
    try:
        r = requests.get(f"{BASE_URL}/api/browse", timeout=10)
        data = r.json()
        results = data.get("results", [])
        report(name, len(results) > 0, f"{len(results)} browse results")
    except Exception as e:
        report(name, False, str(e))


# ---------------------------------------------------------------------------
# 9. Search results include status field
# ---------------------------------------------------------------------------
def test_status_field():
    name = "status_field"
    try:
        r = requests.get(f"{BASE_URL}/api/search", params={"q": "drogas"}, timeout=15)
        data = r.json()
        results = data.get("results", [])
        if not results:
            report(name, False, "no results to check")
            return
        has_status = any("status" in (r if isinstance(r, dict) else {}) for r in results)
        report(name, has_status, "results include status field" if has_status else "no status field found in results")
    except Exception as e:
        report(name, False, str(e))


# ---------------------------------------------------------------------------
# 10. Wiki integration - penal code search returns wiki results
# ---------------------------------------------------------------------------
def test_wiki_integration():
    name = "wiki_integration"
    try:
        r = requests.get(f"{BASE_URL}/api/search", params={"q": "penal code"}, timeout=15)
        data = r.json()
        wiki_hit = data.get("wiki_hit")
        layers = data.get("layers_used", [])
        has_wiki = bool(wiki_hit) or "wiki" in str(layers).lower()
        report(name, has_wiki, f"wiki_hit={wiki_hit}, layers={layers}" if has_wiki else "no wiki results detected")
    except Exception as e:
        report(name, False, str(e))


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
ALL_TESTS = [
    test_health_check,
    test_search_bitcoin,
    test_search_drogas,
    test_decree_153,
    test_chat_weed,
    test_chat_bitcoin,
    test_stats,
    test_browse,
    test_status_field,
    test_wiki_integration,
]


def main():
    global BASE_URL
    parser = argparse.ArgumentParser(description="Test the El Salvador Law Search API")
    parser.add_argument("--base-url", default=BASE_URL, help="Base URL of the running server")
    args = parser.parse_args()
    BASE_URL = args.base_url.rstrip("/")

    print(f"\nTesting API at {BASE_URL}\n" + "=" * 50)

    for test_fn in ALL_TESTS:
        try:
            test_fn()
        except Exception:
            report(test_fn.__name__, False, "unexpected exception")
            traceback.print_exc()

    total = passed + failed
    print("=" * 50)
    print(f"\n{passed}/{total} tests passed", end="")
    if skipped:
        print(f" ({skipped} skipped)", end="")
    print()

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
