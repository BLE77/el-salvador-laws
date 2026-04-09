#!/usr/bin/env python3
"""
Build a SQLite search database from chunked law text.

Creates:
- Full-text search (FTS5) over both Spanish and English text
- Metadata table for filtering by source, year, decree, etc.
- Ready for the API to query

Usage:
    python scripts/build-search-db.py
    python scripts/build-search-db.py --db B:/el-salvador-laws/db/laws.db

Env vars:
    DERIVED_DIR=B:/el-salvador-laws/derived
    DB_PATH=B:/el-salvador-laws/db/laws.db
"""

import os
import sys
import json
import sqlite3
import argparse
from pathlib import Path
from datetime import datetime

DERIVED_DIR = Path(os.environ.get("DERIVED_DIR", "B:/el-salvador-laws/derived"))
CHUNK_DIR = DERIVED_DIR / "chunks"
DB_PATH = Path(os.environ.get("DB_PATH", "B:/el-salvador-laws/db/laws.db"))
DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))


def parse_args():
    p = argparse.ArgumentParser(description="Build search database from chunks")
    p.add_argument("--db", default=str(DB_PATH), help="Database file path")
    p.add_argument("--rebuild", action="store_true", help="Drop and recreate tables")
    return p.parse_args()


def create_tables(conn, rebuild=False):
    if rebuild:
        conn.execute("DROP TABLE IF EXISTS chunks_fts")
        conn.execute("DROP TABLE IF EXISTS chunks")
        conn.execute("DROP TABLE IF EXISTS documents")

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_file TEXT UNIQUE NOT NULL,
            pdf_path TEXT,
            page_count INTEGER,
            text_quality TEXT,
            chunk_count INTEGER,
            source TEXT,
            year TEXT,
            decree_no TEXT,
            emission_date TEXT,
            publication_date TEXT,
            diario_oficial_no TEXT,
            tomo TEXT,
            materia TEXT,
            sub_materia TEXT,
            rama TEXT,
            resumen TEXT,
            imported_at TEXT
        );

        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_id INTEGER NOT NULL REFERENCES documents(id),
            chunk_id TEXT UNIQUE NOT NULL,
            chunk_index INTEGER,
            text_es TEXT NOT NULL,
            text_en TEXT,
            articles TEXT,  -- JSON array of article numbers
            content_hash TEXT,
            char_count_es INTEGER,
            translated BOOLEAN DEFAULT 0,
            FOREIGN KEY (doc_id) REFERENCES documents(id)
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
            text_es,
            text_en,
            chunk_id UNINDEXED,
            content=chunks,
            content_rowid=id,
            tokenize='unicode61 remove_diacritics 2'
        );

        CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
            INSERT INTO chunks_fts(rowid, text_es, text_en, chunk_id)
            VALUES (new.id, new.text_es, COALESCE(new.text_en, ''), new.chunk_id);
        END;

        CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
            INSERT INTO chunks_fts(chunks_fts, rowid, text_es, text_en, chunk_id)
            VALUES ('delete', old.id, old.text_es, COALESCE(old.text_en, ''), old.chunk_id);
        END;

        CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
            INSERT INTO chunks_fts(chunks_fts, rowid, text_es, text_en, chunk_id)
            VALUES ('delete', old.id, old.text_es, COALESCE(old.text_en, ''), old.chunk_id);
            INSERT INTO chunks_fts(rowid, text_es, text_en, chunk_id)
            VALUES (new.id, new.text_es, COALESCE(new.text_en, ''), new.chunk_id);
        END;
    """)


def load_inventory_metadata():
    """Load decree metadata from crawl inventory files. Returns dict keyed by PDF filename."""
    meta_by_pdf = {}
    runs_dir = DATA_DIR / "runs"
    if not runs_dir.exists():
        return meta_by_pdf

    for inv_file in runs_dir.rglob("inventory.ndjson"):
        with open(inv_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # Index by PDF URL and filename for matching
                dt = row.get("document_type", "")
                if dt in ("direct-pdf", "decree-detail"):
                    url = row.get("discovered_url", "")
                    pdf_url = row.get("pdf_url", url)
                    if pdf_url:
                        # Extract filename from PDF URL
                        pdf_name = pdf_url.rstrip("/").split("/")[-1]
                        if pdf_name.endswith(".pdf"):
                            meta_by_pdf[pdf_name] = {
                                "decree_no": row.get("decree_no"),
                                "emission_date": row.get("emission_date"),
                                "publication_date": row.get("publication_date"),
                                "diario_oficial_no": row.get("diario_oficial_no"),
                                "tomo": row.get("tomo"),
                                "materia": row.get("materia"),
                                "sub_materia": row.get("sub_materia"),
                                "rama": row.get("rama"),
                                "resumen": row.get("resumen"),
                            }

    print(f"  Loaded metadata for {len(meta_by_pdf)} PDFs from inventory")
    return meta_by_pdf


def find_chunk_files():
    """Find all chunks.ndjson files."""
    if not CHUNK_DIR.exists():
        print(f"Chunk directory not found: {CHUNK_DIR}")
        return
    for path in CHUNK_DIR.rglob("chunks.ndjson"):
        yield path


def import_chunks(conn, chunk_file, inv_meta=None):
    """Import chunks from one NDJSON file."""
    chunks = []
    with open(chunk_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    chunks.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    if not chunks:
        return 0

    first = chunks[0]
    source_file = first.get("source_file", str(chunk_file.relative_to(CHUNK_DIR)))

    # Check if already imported
    existing = conn.execute(
        "SELECT id FROM documents WHERE source_file = ?", (source_file,)
    ).fetchone()

    if existing:
        return 0  # Already imported

    # Extract source and year from path
    parts = Path(source_file).parts
    source = parts[0] if len(parts) >= 1 else "unknown"
    year = parts[1] if len(parts) >= 2 else None

    # Try to match inventory metadata via PDF filename
    pdf_path = first.get("pdf_path", "")
    inv = {}
    if inv_meta and pdf_path:
        pdf_name = Path(pdf_path).name
        inv = inv_meta.get(pdf_name, {})
        # If not found, try matching by GUID (strip decreto-NNN_ prefix)
        if not inv:
            import re
            guid_match = re.search(r'([0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}\.pdf)', pdf_name, re.IGNORECASE)
            if guid_match:
                inv = inv_meta.get(guid_match.group(1), {})

    # Insert document
    conn.execute(
        """INSERT INTO documents (source_file, pdf_path, page_count, text_quality,
           chunk_count, source, year, decree_no, emission_date, publication_date,
           diario_oficial_no, tomo, materia, sub_materia, rama, resumen, imported_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            source_file,
            pdf_path,
            first.get("page_count", 0),
            first.get("text_quality", "unknown"),
            len(chunks),
            source,
            year,
            inv.get("decree_no"),
            inv.get("emission_date"),
            inv.get("publication_date"),
            inv.get("diario_oficial_no"),
            inv.get("tomo"),
            inv.get("materia"),
            inv.get("sub_materia"),
            inv.get("rama"),
            inv.get("resumen"),
            datetime.now().isoformat(),
        ),
    )
    doc_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    # Insert chunks
    for chunk in chunks:
        conn.execute(
            """INSERT OR IGNORE INTO chunks
               (doc_id, chunk_id, chunk_index, text_es, text_en, articles,
                content_hash, char_count_es, translated)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                doc_id,
                chunk.get("chunk_id", ""),
                chunk.get("chunk_index", 0),
                chunk.get("text_es", ""),
                chunk.get("text_en", ""),
                json.dumps(chunk.get("articles", []), ensure_ascii=False),
                chunk.get("content_hash", ""),
                chunk.get("char_count_es", 0),
                1 if chunk.get("translated") else 0,
            ),
        )

    return len(chunks)


def main():
    args = parse_args()
    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("Build Search Database")
    print("=" * 60)
    print(f"Chunk dir: {CHUNK_DIR}")
    print(f"Database:  {db_path}")

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    create_tables(conn, rebuild=args.rebuild)

    inv_meta = load_inventory_metadata()

    chunk_files = list(find_chunk_files())
    print(f"\nFound {len(chunk_files)} chunk files")

    total_chunks = 0
    total_docs = 0

    for i, cf in enumerate(chunk_files):
        n = import_chunks(conn, cf, inv_meta)
        if n > 0:
            total_chunks += n
            total_docs += 1
            conn.commit()

        pct = round((i + 1) / len(chunk_files) * 100)
        print(f"\r  [{pct}%] {total_docs} docs, {total_chunks} chunks", end="", flush=True)

    conn.commit()

    # Stats
    doc_count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    chunk_count = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    translated = conn.execute("SELECT COUNT(*) FROM chunks WHERE translated = 1").fetchone()[0]

    conn.close()

    print("\n")
    print("=" * 60)
    print("DATABASE SUMMARY")
    print("=" * 60)
    print(f"  Documents:         {doc_count}")
    print(f"  Chunks:            {chunk_count}")
    print(f"  Translated chunks: {translated}")
    print(f"  Database size:     {db_path.stat().st_size / 1024 / 1024:.1f} MB")


if __name__ == "__main__":
    main()
