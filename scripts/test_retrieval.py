#!/usr/bin/env python3
"""
Test search RETRIEVAL quality directly — does the right decreto appear in search results?

This doesn't need the LLM. It tests whether the search pipeline finds the expected
decretos for each test question. This is the real bottleneck — if the right decreto
isn't in the search results, the LLM can never cite it.

Usage:
    python scripts/test_retrieval.py
    BASE_URL=http://localhost:4201 python scripts/test_retrieval.py
"""
import json, httpx, time, sys, os
sys.stdout.reconfigure(line_buffering=True, encoding='utf-8', errors='replace')

BASE_URL = os.environ.get("BASE_URL", "http://localhost:4206")
QUESTIONS_FILE = os.path.join(os.path.dirname(__file__), "test_questions.json")
RESULTS_FILE = os.path.join(os.path.dirname(__file__), "retrieval_results.json")
TIMEOUT = 30


def check_retrieval(question: str, expected_decretos: list[str], search_results: list[dict]) -> dict:
    """Check if the expected decretos appear in search results."""
    score = 0
    max_score = 2
    issues = []

    # Extract all decreto numbers from search results
    found_decretos = set()
    for r in search_results:
        dn = r.get("decree_no", "") or r.get("decreto", "")
        if dn:
            found_decretos.add(str(dn))

    if not expected_decretos:
        # No specific decreto expected — just check we got results
        if search_results:
            score = 2
        else:
            score = 1
            issues.append("NO_RESULTS")
        return {"score": score, "max": max_score, "issues": issues, "found": list(found_decretos)}

    found = []
    missing = []
    for d in expected_decretos:
        if str(d) in found_decretos:
            found.append(d)
        else:
            missing.append(d)

    if found:
        score += 1
    if not missing:
        score += 1  # all expected decretos found

    if missing:
        issues.append(f"MISSING: {missing} (found: {list(found_decretos)[:8]})")

    return {"score": score, "max": max_score, "issues": issues, "found": found, "missing": missing}


def main():
    with open(QUESTIONS_FILE) as f:
        questions = json.load(f)

    print(f"Testing search retrieval for {len(questions)} questions against {BASE_URL}")
    print("=" * 70)

    results = []
    categories = {}
    total_score = 0
    total_max = 0
    errors = 0

    for i, q in enumerate(questions):
        cat = q["category"]
        question = q["question"]
        expected = q.get("expected_decretos", [])

        try:
            resp = httpx.get(
                f"{BASE_URL}/api/search",
                params={"q": question},
                timeout=TIMEOUT
            )
            data = resp.json()
            search_results = data.get("results", [])
            error = None
        except Exception as e:
            search_results = []
            error = str(e)
            errors += 1

        eval_result = check_retrieval(question, expected, search_results)
        score = eval_result["score"]
        max_score = eval_result["max"]
        total_score += score
        total_max += max_score

        grade = "PASS" if score >= 2 else "WARN" if score >= 1 else "FAIL"

        if grade != "PASS":
            print(f"  [{i+1:3d}] {grade} ({cat}) {question[:60]}...")
            if eval_result["issues"]:
                for issue in eval_result["issues"]:
                    print(f"        {issue}")

        result = {
            "index": i, "category": cat, "question": question,
            "expected_decretos": expected,
            "score": score, "max_score": max_score, "grade": grade,
            "issues": eval_result["issues"],
            "found_decretos": eval_result.get("found", []),
            "missing_decretos": eval_result.get("missing", []),
            "num_results": len(search_results),
            "error": error,
        }
        results.append(result)

        if cat not in categories:
            categories[cat] = {"pass": 0, "warn": 0, "fail": 0, "total_score": 0, "total_max": 0}
        categories[cat][grade.lower()] += 1
        categories[cat]["total_score"] += score
        categories[cat]["total_max"] += max_score

    pct = 100 * total_score / total_max if total_max > 0 else 0

    print(f"\n{'='*70}")
    print(f"RETRIEVAL SCORE: {total_score}/{total_max} ({pct:.1f}%)")
    print(f"Errors: {errors}")

    passes = sum(1 for r in results if r["grade"] == "PASS")
    warns = sum(1 for r in results if r["grade"] == "WARN")
    fails = sum(1 for r in results if r["grade"] == "FAIL")
    print(f"PASS: {passes} | WARN: {warns} | FAIL: {fails}")

    print(f"\nBy Category:")
    for cat, stats in sorted(categories.items(), key=lambda x: x[1]["total_score"]/max(x[1]["total_max"],1)):
        cat_pct = 100 * stats["total_score"] / stats["total_max"] if stats["total_max"] > 0 else 0
        print(f"  {cat:15s}: {cat_pct:5.0f}% | P:{stats['pass']} W:{stats['warn']} F:{stats['fail']}")

    # Save results
    with open(RESULTS_FILE, "w") as f:
        json.dump({
            "summary": {
                "total_score": total_score,
                "total_max": total_max,
                "percent": round(pct, 1),
                "passes": passes, "warns": warns, "fails": fails,
                "errors": errors,
                "categories": categories,
            },
            "results": results
        }, f, indent=2)
    print(f"\nResults saved to {RESULTS_FILE}")


if __name__ == "__main__":
    main()
