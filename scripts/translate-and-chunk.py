#!/usr/bin/env python3
"""
Translate extracted law text from Spanish to English and chunk for vector search.

Pipeline:
1. Read extracted .txt files
2. Split into manageable chunks (by article/section or by character limit)
3. Translate each chunk via Claude API
4. Save bilingual chunks with metadata
5. Generate embeddings via Voyage or local model

Usage:
    python scripts/translate-and-chunk.py                  # process all
    python scripts/translate-and-chunk.py --source diario-archive
    python scripts/translate-and-chunk.py --year 2024
    python scripts/translate-and-chunk.py --limit 10
    python scripts/translate-and-chunk.py --dry-run

Env vars:
    KIMI_API_KEY             Required for translation (Kimi K2.5)
    DERIVED_DIR              Override derived storage path
    CHUNK_SIZE               Max chars per chunk (default 3000)
    BATCH_SIZE               Chunks per API call (default 5)
"""

import os
import sys
import json
import re
import time
import hashlib
import argparse
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

DERIVED_DIR = Path(os.environ.get("DERIVED_DIR", "B:/el-salvador-laws/derived"))
TEXT_DIR = DERIVED_DIR / "text"
CHUNK_DIR = DERIVED_DIR / "chunks"
STATE_FILE = Path(os.environ.get("DATA_DIR", "data")) / "translate-state.ndjson"

CHUNK_SIZE = int(os.environ.get("CHUNK_SIZE", "4000"))
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "5"))
PARALLEL_TRANSLATIONS = int(os.environ.get("PARALLEL", "2"))

KIMI_API_KEY = os.environ.get("KIMI_API_KEY", "").strip()
KIMI_BASE_URL = "https://api.kimi.com/coding/v1"

# Article/section patterns in Spanish legal text
ARTICLE_PATTERN = re.compile(
    r"(?:^|\n)"
    r"((?:Art(?:[íi]culo)?|ARTICULO|ARTÍCULO)\s*\.?\s*\d+[\w\-]*\.?\s*[-–—.]?\s*)",
    re.IGNORECASE | re.MULTILINE,
)

SECTION_PATTERNS = [
    re.compile(r"(?:^|\n)((?:CAP[ÍI]TULO|TITULO|TÍTULO|SECCIÓN|SECCION)\s+[\dIVXLCDM]+)", re.IGNORECASE | re.MULTILINE),
    re.compile(r"\n--- PAGE BREAK ---\n"),
]


def parse_args():
    p = argparse.ArgumentParser(description="Translate and chunk extracted law text")
    p.add_argument("--source", help="Filter by source directory")
    p.add_argument("--year", help="Year or year range")
    p.add_argument("--limit", type=int, default=0, help="Max files to process")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--skip-translate", action="store_true", help="Chunk only, no translation")
    p.add_argument("--force", action="store_true")
    return p.parse_args()


def load_done():
    done = set()
    if not STATE_FILE.exists():
        return done
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                if row.get("status") == "chunked" and row.get("text_path"):
                    done.add(row["text_path"])
            except json.JSONDecodeError:
                continue
    return done


def record_result(entry):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def find_text_files(source=None, year_min=None, year_max=None):
    """Walk TEXT_DIR for .txt files."""
    if not TEXT_DIR.exists():
        return
    for root, dirs, files in os.walk(TEXT_DIR):
        root_path = Path(root)
        rel = root_path.relative_to(TEXT_DIR)
        parts = rel.parts

        if source and len(parts) >= 1 and parts[0] != source:
            continue
        if year_min and len(parts) >= 2:
            try:
                y = int(parts[1])
                if y < year_min or y > year_max:
                    continue
            except ValueError:
                pass

        for fname in sorted(files):
            if fname.endswith(".txt") and not fname.endswith(".meta.json"):
                yield root_path / fname


def load_metadata(text_path):
    """Load the companion .meta.json if it exists."""
    meta_path = text_path.with_suffix(".meta.json")
    if meta_path.exists():
        return json.loads(meta_path.read_text(encoding="utf-8"))
    return {}


def smart_chunk(text, max_size=CHUNK_SIZE):
    """
    Split text into chunks, preferring article/section boundaries.
    Each chunk keeps context about which article(s) it contains.
    """
    # Remove page break markers for cleaner chunking
    clean = text.replace("\n--- PAGE BREAK ---\n\n", "\n\n")

    # Try to split by articles first
    articles = ARTICLE_PATTERN.split(clean)

    chunks = []
    current = ""
    current_articles = []

    for i, part in enumerate(articles):
        # Check if this part is an article header
        is_header = ARTICLE_PATTERN.match(part.strip()) if part.strip() else False

        if is_header:
            # Start new article context
            art_match = re.search(r"(\d+[\w\-]*)", part)
            art_num = art_match.group(1) if art_match else "?"

            if len(current) + len(part) > max_size and current.strip():
                # Flush current chunk
                chunks.append({
                    "text_es": current.strip(),
                    "articles": list(current_articles),
                })
                current = part
                current_articles = [art_num]
            else:
                current += part
                current_articles.append(art_num)
        else:
            if len(current) + len(part) > max_size and current.strip():
                chunks.append({
                    "text_es": current.strip(),
                    "articles": list(current_articles),
                })
                current = part
                current_articles = []
            else:
                current += part

    # Final chunk
    if current.strip():
        chunks.append({
            "text_es": current.strip(),
            "articles": list(current_articles),
        })

    # If no article splits worked, fall back to character-based chunking
    if len(chunks) <= 1 and len(clean) > max_size:
        chunks = []
        for i in range(0, len(clean), max_size):
            # Try to break at paragraph boundary
            end = min(i + max_size, len(clean))
            if end < len(clean):
                # Look for paragraph break near the end
                last_break = clean.rfind("\n\n", i, end)
                if last_break > i + max_size // 2:
                    end = last_break

            chunk_text = clean[i:end].strip()
            if chunk_text:
                chunks.append({"text_es": chunk_text, "articles": []})

    return chunks


def translate_chunk(text_es, api_key):
    """
    Translate a Spanish legal text chunk to English using Kimi K2.5 API.
    Returns the English translation.
    """
    if not api_key:
        return None

    import urllib.request

    prompt = f"""Translate the following Salvadoran legal text from Spanish to English.

Rules:
- Preserve all article numbers, decree numbers, and legal references exactly.
- Keep proper nouns in Spanish (names of institutions, places) but add an English gloss in parentheses on first occurrence.
- For legal terms with no clean English equivalent, keep the Spanish term in italics and add the English meaning in parentheses. Example: "amparo (constitutional protection remedy)"
- Preserve the structure: article numbers, section headers, numbered lists.
- Do NOT add commentary or interpretation. Translate only.
- If the text is garbled or clearly OCR noise, output "[OCR quality too poor for reliable translation]" for that section.

Spanish text:
---
{text_es}
---

English translation:"""

    body = json.dumps({
        "model": "kimi-k2.5",
        "max_tokens": 8192,
        "stream": False,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")

    req = urllib.request.Request(
        KIMI_BASE_URL + "/chat/completions",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "claude-code/1.0",
        },
        method="POST",
    )

    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                content = result["choices"][0]["message"]["content"]
                if content:
                    return content
                # K2.5 reasoning model may put output in reasoning_content
                reasoning = result["choices"][0]["message"].get("reasoning_content", "")
                if reasoning and not content:
                    return "[reasoning only - no translation produced]"
                return None
        except Exception as e:
            if attempt < 2:
                wait = (attempt + 1) * 5
                print(f"\n  [retry] Translation API error: {e}. Waiting {wait}s...")
                time.sleep(wait)
            else:
                print(f"\n  [error] Translation failed after 3 attempts: {e}")
                return None


def process_file(text_path, skip_translate=False, api_key=""):
    """Process one extracted text file: chunk and optionally translate."""
    text = text_path.read_text(encoding="utf-8")
    meta = load_metadata(text_path)

    if len(text.strip()) < 50:
        return {"status": "skipped", "reason": "too_short", "chunk_count": 0}

    # Chunk
    chunks = smart_chunk(text)

    # Build output
    rel = text_path.relative_to(TEXT_DIR)
    out_dir = CHUNK_DIR / rel.parent / text_path.stem
    out_dir.mkdir(parents=True, exist_ok=True)

    results = []

    for i, chunk in enumerate(chunks):
        chunk_id = f"{rel.stem}_chunk_{i:04d}"
        chunk["chunk_id"] = chunk_id
        chunk["chunk_index"] = i
        chunk["total_chunks"] = len(chunks)
        chunk["source_file"] = str(rel)
        chunk["char_count_es"] = len(chunk["text_es"])

        # Copy metadata
        if meta:
            chunk["pdf_path"] = meta.get("pdf_path", "")
            chunk["page_count"] = meta.get("page_count", 0)
            chunk["text_quality"] = meta.get("text_quality", "unknown")

        chunk["text_en"] = ""
        chunk["translated"] = False

        # Hash for dedup
        chunk["content_hash"] = hashlib.sha256(
            chunk["text_es"].encode("utf-8")
        ).hexdigest()[:16]

        results.append(chunk)

    # Translate chunks in parallel
    if not skip_translate and api_key:
        def translate_one(idx):
            c = results[idx]
            if not c["text_es"]:
                return idx, None
            t = translate_chunk(c["text_es"], api_key)
            return idx, t

        with ThreadPoolExecutor(max_workers=PARALLEL_TRANSLATIONS) as pool:
            futures = {pool.submit(translate_one, i): i for i in range(len(results))}
            for future in as_completed(futures):
                try:
                    idx, translation = future.result()
                    if translation:
                        results[idx]["text_en"] = translation
                        results[idx]["translated"] = True
                except Exception as e:
                    pass  # already logged in translate_chunk

    # Save all chunks as a single NDJSON file
    out_file = out_dir / "chunks.ndjson"
    with open(out_file, "w", encoding="utf-8") as f:
        for chunk in results:
            f.write(json.dumps(chunk, ensure_ascii=False) + "\n")

    # Also save a summary
    summary = {
        "source_file": str(rel),
        "chunk_count": len(results),
        "total_chars_es": sum(c["char_count_es"] for c in results),
        "translated_chunks": sum(1 for c in results if c.get("translated")),
        "articles_found": [a for c in results for a in c.get("articles", [])],
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return {
        "status": "chunked",
        "chunk_count": len(results),
        "translated": sum(1 for c in results if c.get("translated")),
    }


def main():
    args = parse_args()

    year_min = year_max = None
    if args.year:
        if "-" in args.year:
            year_min, year_max = map(int, args.year.split("-"))
        else:
            year_min = year_max = int(args.year)

    api_key = KIMI_API_KEY if not args.skip_translate else ""

    print("=" * 60)
    print("Translate & Chunk Pipeline")
    print("=" * 60)
    print(f"Text dir:    {TEXT_DIR}")
    print(f"Chunk dir:   {CHUNK_DIR}")
    print(f"Translate:   {'YES' if api_key else 'NO (--skip-translate or no API key)'}")
    if args.source:
        print(f"Source:      {args.source}")
    if year_min:
        print(f"Year range:  {year_min}-{year_max}")

    # Find text files
    files = list(find_text_files(args.source, year_min, year_max))
    print(f"\nFound {len(files)} text files")

    done = set() if args.force else load_done()
    pending = [f for f in files if str(f) not in done]
    print(f"Already processed: {len(done)}")
    print(f"Pending: {len(pending)}")

    if args.limit:
        pending = pending[: args.limit]

    if args.dry_run:
        print("\nWould process:")
        for f in pending[:20]:
            print(f"  {f}")
        if len(pending) > 20:
            print(f"  ... and {len(pending) - 20} more")
        return

    if not pending:
        print("\nNothing to process.")
        return

    total_chunks = 0
    total_translated = 0
    failed = 0

    for i, text_path in enumerate(pending):
        try:
            result = process_file(text_path, args.skip_translate, api_key)
            total_chunks += result.get("chunk_count", 0)
            total_translated += result.get("translated", 0)

            record_result({
                "text_path": str(text_path),
                "status": result["status"],
                "chunk_count": result.get("chunk_count", 0),
                "processed_at": datetime.now().isoformat(),
            })

            pct = round((i + 1) / len(pending) * 100)
            print(
                f"\r  [{pct}%] {i + 1}/{len(pending)} files, "
                f"{total_chunks} chunks, {total_translated} translated",
                end="",
                flush=True,
            )

        except Exception as e:
            failed += 1
            print(f"\n  [error] {text_path}: {e}")
            record_result({
                "text_path": str(text_path),
                "status": "failed",
                "error": str(e),
                "attempted_at": datetime.now().isoformat(),
            })

    print("\n")
    print("=" * 60)
    print("CHUNK & TRANSLATE SUMMARY")
    print("=" * 60)
    print(f"  Files processed: {len(pending) - failed}")
    print(f"  Total chunks:    {total_chunks}")
    print(f"  Translated:      {total_translated}")
    print(f"  Failed:          {failed}")


if __name__ == "__main__":
    main()
