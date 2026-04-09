#!/usr/bin/env python
"""
build-wiki.py  --  Generate English wiki pages for top El Salvador laws.

Reads law chunks from SQLite, sends them to Kimi K2.5 for summarization,
and writes markdown wiki pages to B:/el-salvador-laws/wiki/.

Usage:
    python scripts/build-wiki.py [--top N] [--db PATH] [--out PATH]
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import time
import urllib.request
import urllib.error

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DB_PATH = "B:/el-salvador-laws/db/laws.db"
WIKI_DIR = "B:/el-salvador-laws/wiki"
KIMI_ENDPOINT = "https://api.kimi.com/coding/v1/chat/completions"
KIMI_KEY = os.environ.get("KIMI_API_KEY", "").strip()
KIMI_MODEL = "kimi-k2.5"
MAX_CHUNK_CHARS = 3500  # keep under 4K for Kimi
RATE_LIMIT_SLEEP = 2    # seconds between API calls


def kimi_chat(messages, temperature=0.3, max_tokens=2000):
    """Call Kimi K2.5 chat completions API."""
    if not KIMI_KEY:
        raise RuntimeError("KIMI_API_KEY is required to build wiki pages")

    payload = json.dumps({
        "model": KIMI_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }).encode("utf-8")

    for attempt in range(3):
        req = urllib.request.Request(
            KIMI_ENDPOINT,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {KIMI_KEY}",
                "User-Agent": "claude-code/1.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                return body["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            print(f"    [Kimi HTTP {e.code}] {err_body[:200]}", flush=True)
            if e.code == 429:
                wait = (attempt + 1) * 15
                print(f"    Rate limited, waiting {wait}s...", flush=True)
                time.sleep(wait)
            elif attempt < 2:
                time.sleep(5)
            else:
                raise
        except Exception as e:
            print(f"    [Kimi error] {e}", flush=True)
            if attempt < 2:
                time.sleep(5)
            else:
                raise


def get_top_laws(conn, top_n=50):
    """Return the top N laws ranked by chunk count (proxy for importance)."""
    cur = conn.cursor()
    cur.execute("""
        SELECT d.id, d.decree_no, d.year, d.materia, d.rama, d.resumen,
               COUNT(ch.id) as chunk_count
        FROM documents d
        JOIN chunks ch ON ch.doc_id = d.id
        WHERE d.decree_no IS NOT NULL AND d.decree_no != ''
        GROUP BY d.id
        ORDER BY chunk_count DESC
        LIMIT ?
    """, (top_n,))
    rows = cur.fetchall()
    return [
        {
            "doc_id": r[0],
            "decree_no": r[1],
            "year": r[2] or "unknown",
            "materia": r[3] or "",
            "rama": r[4] or "",
            "resumen": r[5] or "",
            "chunk_count": r[6],
        }
        for r in rows
    ]


def get_chunks(conn, doc_id):
    """Get all chunks for a document, preferring English text."""
    cur = conn.cursor()
    cur.execute("""
        SELECT chunk_index, text_es, text_en, articles
        FROM chunks
        WHERE doc_id = ?
        ORDER BY chunk_index
    """, (doc_id,))
    return cur.fetchall()


def extract_key_points(chunk_text, decree_no, year, chunk_idx, total_chunks):
    """Ask Kimi to extract key points from a single chunk."""
    truncated = chunk_text[:MAX_CHUNK_CHARS]
    prompt = (
        f"This is chunk {chunk_idx+1}/{total_chunks} of El Salvador Decreto {decree_no} ({year}). "
        f"Extract the key legal provisions, article numbers, and important rules from this text. "
        f"Be concise -- bullet points only. If it's procedural boilerplate, just say 'procedural'.\n\n"
        f"TEXT:\n{truncated}"
    )
    messages = [{"role": "user", "content": prompt}]
    return kimi_chat(messages, temperature=0.2, max_tokens=800)


def generate_wiki_page(law, all_key_points):
    """Ask Kimi to generate the final wiki page from combined key points."""
    # Truncate key points to fit in context
    combined = "\n\n".join(all_key_points)
    if len(combined) > 3500:
        combined = combined[:3500] + "\n... [truncated]"

    prompt = (
        f"Write an English wiki page for El Salvador law Decreto {law['decree_no']} ({law['year']}).\n"
        f"Category: {law['materia']} / {law['rama']}\n"
        f"Spanish summary: {law['resumen'][:300]}\n\n"
        f"Key points extracted from the law:\n{combined}\n\n"
        f"Format EXACTLY as:\n"
        f"## Summary\n[2-4 sentences plain English]\n\n"
        f"## Key Provisions\n- **Article X**: [what it says]\n[list 5-15 most important articles]\n\n"
        f"## Who This Affects\n[1-2 sentences]\n\n"
        f"## Related Laws\n- [any referenced decrees]\n\n"
        f"Do NOT include a title or frontmatter -- I will add those."
    )
    messages = [{"role": "user", "content": prompt}]
    return kimi_chat(messages, temperature=0.3, max_tokens=2000)


def infer_title(law):
    """Generate a short English title from the law metadata."""
    resumen = law["resumen"].lower()
    rama = law["rama"]
    materia = law["materia"]

    # Common mappings
    title_map = {
        "comercio": "Commercial Code",
        "comercial": "Commercial Code",
        "tributario": "Tax Code",
        "procesal civil": "Civil and Commercial Procedure Code",
        "procesal penal": "Criminal Procedure Code",
        "laboral": "Labor Code",
        "penal": "Penal Code",
        "familia": "Family Code",
        "electoral": "Electoral Code",
        "bancario": "Banking Law",
        "financiero": "Financial System Law",
        "militar": "Military Code",
        "municipal": "Municipal Code",
        "medioambiental": "Environmental Law",
        "administrativo": "Administrative Law",
        "agrario": "Agrarian Law",
    }

    for key, title in title_map.items():
        if key in rama.lower():
            return title

    # Fallback: use materia
    return materia if materia else f"Decreto {law['decree_no']}"


def slugify(text):
    """Create a filename-safe slug."""
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    return text[:60]


def process_law(conn, law, wiki_dir):
    """Process a single law: extract key points, generate wiki page, save."""
    decree = law["decree_no"]
    year = law["year"]
    doc_id = law["doc_id"]
    title = infer_title(law)

    filename = f"decreto-{decree}-{year}-{slugify(title)}.md"
    filepath = os.path.join(wiki_dir, filename)

    # Skip if already exists and has real content (> 300 bytes)
    if os.path.exists(filepath) and os.path.getsize(filepath) > 300:
        print(f"  [skip] {filename} already exists", flush=True)
        return filename, title

    print(f"  Processing Decreto {decree} ({year}) - {title} [{law['chunk_count']} chunks]", flush=True)

    chunks = get_chunks(conn, doc_id)
    total = len(chunks)

    # Phase 1: Extract key points from chunks (sample if too many)
    key_points = []
    # Sample strategy: first 3, last 2, and every Nth in between
    if total <= 8:
        sample_indices = list(range(total))
    else:
        sample_indices = [0, 1, 2]  # first 3
        step = max(1, (total - 5) // 4)
        sample_indices += list(range(3, total - 2, step))
        sample_indices += [total - 2, total - 1]  # last 2
        sample_indices = sorted(set(sample_indices))

    # Limit to ~10 API calls per law
    sample_indices = sample_indices[:10]

    for idx in sample_indices:
        chunk = chunks[idx]
        # Prefer English text, fall back to Spanish
        en_text = (chunk[2] or "").strip()
        es_text = (chunk[1] or "").strip()
        text = en_text if len(en_text) > 50 else es_text
        if not text or len(text) < 50:
            continue

        try:
            points = extract_key_points(text, decree, year, idx, total)
            key_points.append(points)
            time.sleep(RATE_LIMIT_SLEEP)
        except Exception as e:
            print(f"    [error chunk {idx}] {e}")
            continue

    if not key_points:
        print(f"    [warn] No key points extracted, writing minimal page")
        body = f"## Summary\n{law['resumen'][:500]}\n\n## Key Provisions\nNo detailed extraction available.\n"
    else:
        # Phase 2: Generate final wiki page
        try:
            body = generate_wiki_page(law, key_points)
            time.sleep(RATE_LIMIT_SLEEP)
        except Exception as e:
            print(f"    [error generating page] {e}")
            body = f"## Summary\n{law['resumen'][:500]}\n\n## Key Provisions\nGeneration failed: {e}\n"

    # Build full page with frontmatter
    page = (
        f"---\n"
        f"decreto: {decree}\n"
        f"year: {year}\n"
        f"materia: {law['materia']}\n"
        f"rama: {law['rama']}\n"
        f"title: {title}\n"
        f"---\n"
        f"# Decreto {decree} ({year}) --- {title}\n\n"
        f"{body}\n"
    )

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(page)

    print(f"    -> Saved {filename}", flush=True)
    return filename, title


def build_index(wiki_dir, entries):
    """Create index.md linking all wiki pages."""
    lines = [
        "# El Salvador Laws Wiki\n",
        f"**{len(entries)} laws indexed**\n",
        "| Decreto | Year | Title | Category |\n",
        "|---------|------|-------|----------|\n",
    ]
    for e in sorted(entries, key=lambda x: (x.get("year", ""), x.get("decree_no", ""))):
        link = f"[{e['title']}]({e['filename']})"
        lines.append(
            f"| {e['decree_no']} | {e['year']} | {link} | {e['rama']} |\n"
        )

    index_path = os.path.join(wiki_dir, "index.md")
    with open(index_path, "w", encoding="utf-8") as f:
        f.writelines(lines)
    print(f"\nIndex written to {index_path}")


def main():
    parser = argparse.ArgumentParser(description="Build El Salvador law wiki pages")
    parser.add_argument("--top", type=int, default=50, help="Number of top laws to process")
    parser.add_argument("--db", default=DB_PATH, help="Path to SQLite database")
    parser.add_argument("--out", default=WIKI_DIR, help="Output wiki directory")
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)

    conn = sqlite3.connect(args.db)
    laws = get_top_laws(conn, args.top)
    print(f"Found {len(laws)} laws to process\n")

    entries = []
    for i, law in enumerate(laws):
        print(f"[{i+1}/{len(laws)}]", end="")
        try:
            filename, title = process_law(conn, law, args.out)
            entries.append({
                "filename": filename,
                "title": title,
                "decree_no": law["decree_no"],
                "year": law["year"],
                "rama": law["rama"],
            })
        except Exception as e:
            print(f"  [FAILED] Decreto {law['decree_no']}: {e}")

    build_index(args.out, entries)
    conn.close()

    # Summary
    total_size = sum(
        os.path.getsize(os.path.join(args.out, f))
        for f in os.listdir(args.out)
        if f.endswith(".md")
    )
    print(f"\nDone! {len(entries)} wiki pages, {total_size/1024:.1f} KB total")


if __name__ == "__main__":
    main()
