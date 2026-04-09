"""
Run 76 test questions against the law agent and evaluate quality.
Outputs a report showing which questions got good answers and which need work.
"""
import json, httpx, time, sys, re, os
sys.stdout.reconfigure(line_buffering=True, encoding='utf-8', errors='replace')  # Force UTF-8 unbuffered

BASE_URL = os.environ.get("BASE_URL", "http://localhost:4200")
QUESTIONS_FILE = os.path.join(os.path.dirname(__file__), "test_questions.json")
RESULTS_FILE = os.path.join(os.path.dirname(__file__), "test_results.json")
TIMEOUT = 60  # seconds per question

def check_answer(question, answer, expected_decretos):
    """Score an answer on a few dimensions."""
    issues = []
    score = 0
    max_score = 4

    # 1. Did it actually answer (not empty/error)?
    if not answer or len(answer) < 50:
        issues.append("EMPTY_OR_SHORT: Answer is too short (<50 chars)")
        return {"score": 0, "max": max_score, "issues": issues}
    score += 1

    # 2. Does it mention expected decretos?
    if expected_decretos:
        found = []
        missing = []
        for d in expected_decretos:
            # Look for "decreto 15", "Decreto 15", "D-15", "decreto-15", etc.
            pattern = rf'(?:decreto|decree|D[-.]?)\s*{d}\b'
            if re.search(pattern, answer, re.IGNORECASE):
                found.append(d)
            else:
                missing.append(d)
        if found:
            score += 1
        if not missing:
            score += 1  # bonus for finding ALL expected decretos
        if missing:
            issues.append(f"MISSING_DECRETOS: Expected {missing}, found {found}")
    else:
        score += 2  # no specific decreto expected, give full marks

    # 3. Does it have substance (not just "I don't know")?
    hedging = ["i don't have", "i couldn't find", "i don't know", "no information",
               "i'm not sure", "i cannot", "unable to find", "no results"]
    hedging_count = sum(1 for h in hedging if h.lower() in answer.lower())
    if hedging_count >= 2:
        issues.append("TOO_HEDGY: Answer is mostly hedging/uncertainty")
    else:
        score += 1

    return {"score": score, "max": max_score, "issues": issues}


def main():
    with open(QUESTIONS_FILE) as f:
        questions = json.load(f)

    print(f"Running {len(questions)} test questions against {BASE_URL}")
    print("=" * 70)

    results = []
    categories = {}
    session_id = None  # use a single session to test memory too
    total_score = 0
    total_max = 0
    errors = 0
    start_all = time.time()

    for i, q in enumerate(questions):
        cat = q["category"]
        question = q["question"]
        expected = q.get("expected_decretos", [])
        difficulty = q.get("difficulty", "unknown")

        print(f"\n[{i+1}/{len(questions)}] ({cat}/{difficulty}) {question[:60]}...")

        payload = {"question": question}
        if session_id:
            payload["session_id"] = session_id

        start = time.time()
        try:
            resp = httpx.post(
                f"{BASE_URL}/api/chat",
                json=payload,
                timeout=TIMEOUT
            )
            elapsed = time.time() - start
            data = resp.json()
            answer = data.get("answer", "")
            session_id = data.get("session_id", session_id)
            sources_count = len(data.get("sources", []))
            error = None
        except Exception as e:
            elapsed = time.time() - start
            answer = ""
            sources_count = 0
            error = str(e)
            errors += 1

        eval_result = check_answer(question, answer, expected)
        score = eval_result["score"]
        max_score = eval_result["max"]
        total_score += score
        total_max += max_score

        grade = "PASS" if score >= 3 else "WARN" if score >= 2 else "FAIL"
        print(f"  {grade} ({score}/{max_score}) {elapsed:.1f}s | {sources_count} sources", end="")
        if eval_result["issues"]:
            print(f" | {'; '.join(eval_result['issues'])}", end="")
        if error:
            print(f" | ERROR: {error}", end="")
        print()

        result = {
            "index": i,
            "category": cat,
            "difficulty": difficulty,
            "question": question,
            "expected_decretos": expected,
            "answer_length": len(answer),
            "answer_preview": answer[:200] if answer else "",
            "sources_count": sources_count,
            "elapsed_seconds": round(elapsed, 1),
            "score": score,
            "max_score": max_score,
            "grade": grade,
            "issues": eval_result["issues"],
            "error": error
        }
        results.append(result)

        if cat not in categories:
            categories[cat] = {"pass": 0, "warn": 0, "fail": 0, "total_score": 0, "total_max": 0}
        categories[cat][grade.lower()] += 1
        categories[cat]["total_score"] += score
        categories[cat]["total_max"] += max_score

    total_elapsed = time.time() - start_all

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Total: {total_score}/{total_max} ({100*total_score/total_max:.0f}%)")
    print(f"Time: {total_elapsed:.0f}s total, {total_elapsed/len(questions):.1f}s avg per question")
    print(f"Errors: {errors}")

    passes = sum(1 for r in results if r["grade"] == "PASS")
    warns = sum(1 for r in results if r["grade"] == "WARN")
    fails = sum(1 for r in results if r["grade"] == "FAIL")
    print(f"PASS: {passes} | WARN: {warns} | FAIL: {fails}")

    print(f"\nBy Category:")
    for cat, stats in sorted(categories.items()):
        pct = 100 * stats["total_score"] / stats["total_max"] if stats["total_max"] > 0 else 0
        print(f"  {cat:15s}: {pct:5.0f}% | P:{stats['pass']} W:{stats['warn']} F:{stats['fail']}")

    # Show failures
    failures = [r for r in results if r["grade"] == "FAIL"]
    if failures:
        print(f"\nFAILED QUESTIONS ({len(failures)}):")
        for r in failures:
            print(f"  [{r['category']}/{r['difficulty']}] {r['question'][:70]}")
            for issue in r["issues"]:
                print(f"    ->{issue}")
            if r["error"]:
                print(f"    ->ERROR: {r['error']}")

    # Show warnings
    warnings = [r for r in results if r["grade"] == "WARN"]
    if warnings:
        print(f"\nWARNING QUESTIONS ({len(warnings)}):")
        for r in warnings:
            print(f"  [{r['category']}/{r['difficulty']}] {r['question'][:70]}")
            for issue in r["issues"]:
                print(f"    ->{issue}")

    # Save full results
    with open(RESULTS_FILE, "w") as f:
        json.dump({
            "summary": {
                "total_score": total_score,
                "total_max": total_max,
                "percent": round(100 * total_score / total_max, 1),
                "passes": passes,
                "warns": warns,
                "fails": fails,
                "errors": errors,
                "elapsed_seconds": round(total_elapsed, 1),
                "categories": categories
            },
            "results": results
        }, f, indent=2)
    print(f"\nFull results saved to {RESULTS_FILE}")


if __name__ == "__main__":
    main()
