"""
prepare-qmd.py  --  Convert El Salvador law chunks into markdown files for QMD indexing.

Reads document metadata from SQLite (laws.db) and chunk text from NDJSON files,
then writes one .md file per document into B:/el-salvador-laws/qmd-docs/.

Each markdown file has YAML frontmatter with metadata and the full Spanish text
organized by articles.

Usage:
    python scripts/prepare-qmd.py
"""

import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

DB_PATH = Path("B:/el-salvador-laws/db/laws.db")
CHUNKS_ROOT = Path("B:/el-salvador-laws/derived/chunks")
OUTPUT_ROOT = Path("B:/el-salvador-laws/qmd-docs")

def slugify(text: str) -> str:
    """Create a filesystem-safe slug from text."""
    text = text.strip().lower()
    text = re.sub(r'[^a-z0-9]+', '-', text)
    return text.strip('-')[:120]

def escape_yaml(val: str) -> str:
    """Escape a string for YAML frontmatter."""
    if not val:
        return '""'
    # If it contains special chars, quote it
    if any(c in val for c in ':{}[]#&*!|>\'"%@`'):
        return '"' + val.replace('\\', '\\\\').replace('"', '\\"') + '"'
    return val

def find_chunks_dir(source_file: str) -> Path | None:
    """Given a source_file like 'asamblea-year-archive\\2024\\decreto-1000_UUID.txt',
    find the corresponding chunks directory."""
    # source_file uses backslash, normalize
    parts = source_file.replace('\\', '/').replace('.txt', '').split('/')
    # e.g. ['asamblea-year-archive', '2024', 'decreto-1000_UUID']
    candidate = CHUNKS_ROOT / '/'.join(parts)
    if candidate.is_dir():
        return candidate
    return None

def read_chunks(chunks_dir: Path) -> list[dict]:
    """Read all chunks from an NDJSON file."""
    ndjson = chunks_dir / "chunks.ndjson"
    if not ndjson.exists():
        return []
    chunks = []
    with open(ndjson, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    chunks.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    # Sort by chunk_index
    chunks.sort(key=lambda c: c.get('chunk_index', 0))
    return chunks

def build_markdown(doc: dict, chunks: list[dict]) -> str:
    """Build a markdown document from metadata and chunks."""
    # Frontmatter
    lines = ['---']
    lines.append(f'decree_no: {escape_yaml(str(doc["decree_no"] or ""))}')
    lines.append(f'year: {escape_yaml(str(doc["year"] or ""))}')
    lines.append(f'source: {escape_yaml(str(doc["source"] or ""))}')
    lines.append(f'materia: {escape_yaml(str(doc["materia"] or ""))}')
    lines.append(f'sub_materia: {escape_yaml(str(doc["sub_materia"] or ""))}')
    lines.append(f'rama: {escape_yaml(str(doc["rama"] or ""))}')
    lines.append(f'emission_date: {escape_yaml(str(doc["emission_date"] or ""))}')
    lines.append(f'publication_date: {escape_yaml(str(doc["publication_date"] or ""))}')
    lines.append(f'diario_oficial_no: {escape_yaml(str(doc["diario_oficial_no"] or ""))}')
    lines.append(f'tomo: {escape_yaml(str(doc["tomo"] or ""))}')
    lines.append(f'page_count: {doc["page_count"] or 0}')
    lines.append(f'text_quality: {escape_yaml(str(doc["text_quality"] or ""))}')
    lines.append('---')
    lines.append('')

    # Title
    decree = doc["decree_no"] or "Sin número"
    year = doc["year"] or ""
    resumen = doc.get("resumen") or ""
    lines.append(f'# Decreto N° {decree} ({year})')
    lines.append('')

    if resumen:
        lines.append(f'> {resumen}')
        lines.append('')

    # Body: concatenate all chunk text
    for chunk in chunks:
        text = chunk.get('text_es', '').strip()
        if not text:
            continue
        lines.append(text)
        lines.append('')

    return '\n'.join(lines)

def main():
    if not DB_PATH.exists():
        print(f"ERROR: Database not found at {DB_PATH}")
        sys.exit(1)

    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, source_file, pdf_path, page_count, text_quality,
               chunk_count, source, year, decree_no, emission_date,
               publication_date, diario_oficial_no, tomo, materia,
               sub_materia, rama, resumen
        FROM documents
        ORDER BY id
    """)

    docs = cursor.fetchall()
    total = len(docs)
    print(f"Processing {total} documents...")

    written = 0
    skipped = 0
    errors = 0
    t0 = time.time()

    for i, doc in enumerate(docs):
        doc = dict(doc)
        source_file = doc["source_file"]

        # Find chunks directory
        chunks_dir = find_chunks_dir(source_file)
        if not chunks_dir:
            skipped += 1
            continue

        chunks = read_chunks(chunks_dir)
        if not chunks:
            skipped += 1
            continue

        # Build output filename
        # Use source/year/decree structure
        source = doc["source"] or "unknown"
        year = doc["year"] or "unknown"
        decree = doc["decree_no"] or f"doc-{doc['id']}"
        safe_decree = re.sub(r'[^a-zA-Z0-9_-]', '_', str(decree))

        out_dir = OUTPUT_ROOT / source / str(year)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / f"decreto-{safe_decree}.md"

        try:
            md = build_markdown(doc, chunks)
            with open(out_file, 'w', encoding='utf-8') as f:
                f.write(md)
            written += 1
        except Exception as e:
            errors += 1
            if errors <= 5:
                print(f"  ERROR on doc {doc['id']}: {e}")

        if (i + 1) % 1000 == 0:
            elapsed = time.time() - t0
            rate = (i + 1) / elapsed
            print(f"  [{i+1}/{total}] {rate:.0f} docs/sec | written={written} skipped={skipped} errors={errors}")

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.1f}s")
    print(f"  Written: {written}")
    print(f"  Skipped: {skipped}")
    print(f"  Errors:  {errors}")
    print(f"  Output:  {OUTPUT_ROOT}")

    conn.close()

if __name__ == "__main__":
    main()
