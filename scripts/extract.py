#!/usr/bin/env python3
"""
Extract text from downloaded PDFs.

Strategy:
1. Try PyMuPDF direct text extraction first.
2. If extracted text is too short (<100 chars per page avg), flag for OCR.
3. Save extracted text as .txt alongside metadata JSON.

Usage:
    python scripts/extract.py                        # extract all
    python scripts/extract.py --source diario-archive
    python scripts/extract.py --year 2024
    python scripts/extract.py --year 2020-2026
    python scripts/extract.py --limit 50
    python scripts/extract.py --dry-run

Env vars:
    RAW_DIR=B:/el-salvador-laws/raw
    DERIVED_DIR=B:/el-salvador-laws/derived
"""

import os
import sys
import json
import hashlib
import argparse
from pathlib import Path
from datetime import datetime

try:
    import fitz  # PyMuPDF
except ImportError:
    print("PyMuPDF not installed. Run: pip install pymupdf")
    sys.exit(1)

RAW_DIR = Path(os.environ.get("RAW_DIR", "B:/el-salvador-laws/raw"))
DERIVED_DIR = Path(os.environ.get("DERIVED_DIR", "B:/el-salvador-laws/derived"))
TEXT_DIR = DERIVED_DIR / "text"
STATE_FILE = Path(os.environ.get("DATA_DIR", "data")) / "extract-state.ndjson"

# Minimum average characters per page to consider text extraction successful
MIN_CHARS_PER_PAGE = 100


def parse_args():
    p = argparse.ArgumentParser(description="Extract text from downloaded PDFs")
    p.add_argument("--source", help="Filter by source directory")
    p.add_argument("--year", help="Year or year range (e.g., 2024 or 2020-2026)")
    p.add_argument("--limit", type=int, default=0, help="Max files to process")
    p.add_argument("--dry-run", action="store_true", help="Show what would be processed")
    p.add_argument("--force", action="store_true", help="Re-extract even if already done")
    return p.parse_args()


def load_done():
    """Load already-extracted file paths."""
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
                if row.get("status") == "extracted" and row.get("pdf_path"):
                    done.add(row["pdf_path"])
            except json.JSONDecodeError:
                continue
    return done


def record_result(entry):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def find_pdfs(source=None, year_min=None, year_max=None):
    """Walk RAW_DIR and yield PDF file paths."""
    if not RAW_DIR.exists():
        print(f"Raw directory not found: {RAW_DIR}")
        return

    for root, dirs, files in os.walk(RAW_DIR):
        root_path = Path(root)
        rel = root_path.relative_to(RAW_DIR)
        parts = rel.parts

        # Filter by source
        if source and len(parts) >= 1 and parts[0] != source:
            continue

        # Filter by year
        if year_min and len(parts) >= 2:
            try:
                y = int(parts[1])
                if y < year_min or y > year_max:
                    continue
            except ValueError:
                pass

        for fname in sorted(files):
            if fname.lower().endswith(".pdf"):
                yield root_path / fname


def extract_text(pdf_path):
    """
    Extract text from a PDF using PyMuPDF.
    Returns (text, page_count, quality, metadata).
    """
    doc = fitz.open(str(pdf_path))
    pages = []
    total_chars = 0
    page_count = doc.page_count

    metadata = {
        "title": doc.metadata.get("title", ""),
        "author": doc.metadata.get("author", ""),
        "subject": doc.metadata.get("subject", ""),
        "creator": doc.metadata.get("creator", ""),
        "page_count": page_count,
    }

    for page_num in range(page_count):
        page = doc[page_num]
        text = page.get_text("text")
        pages.append(text)
        total_chars += len(text.strip())

    doc.close()

    full_text = "\n\n--- PAGE BREAK ---\n\n".join(pages)

    # Assess quality
    avg_chars = total_chars / max(page_count, 1)
    if avg_chars >= MIN_CHARS_PER_PAGE:
        quality = "born_digital"
    elif avg_chars >= 20:
        quality = "partial_text"
    else:
        quality = "needs_ocr"

    return full_text, page_count, quality, metadata


def output_path(pdf_path):
    """Build output text file path mirroring the raw directory structure."""
    rel = pdf_path.relative_to(RAW_DIR)
    return TEXT_DIR / rel.with_suffix(".txt")


def output_meta_path(pdf_path):
    rel = pdf_path.relative_to(RAW_DIR)
    return TEXT_DIR / rel.with_suffix(".meta.json")


def main():
    args = parse_args()

    year_min = year_max = None
    if args.year:
        if "-" in args.year:
            year_min, year_max = map(int, args.year.split("-"))
        else:
            year_min = year_max = int(args.year)

    print("=" * 60)
    print("PDF Text Extractor")
    print("=" * 60)
    print(f"Raw dir:     {RAW_DIR}")
    print(f"Text dir:    {TEXT_DIR}")
    if args.source:
        print(f"Source:      {args.source}")
    if year_min:
        print(f"Year range:  {year_min}-{year_max}")
    if args.limit:
        print(f"Limit:       {args.limit}")
    if args.dry_run:
        print("Mode:        DRY RUN")

    # Find all PDFs
    pdfs = list(find_pdfs(args.source, year_min, year_max))
    print(f"\nFound {len(pdfs)} PDFs")

    # Filter already done
    done = set() if args.force else load_done()
    pending = [p for p in pdfs if str(p) not in done]
    print(f"Already extracted: {len(done)}")
    print(f"Pending: {len(pending)}")

    if args.limit:
        pending = pending[: args.limit]

    if args.dry_run:
        print("\nWould extract:")
        for p in pending[:20]:
            print(f"  {p} -> {output_path(p)}")
        if len(pending) > 20:
            print(f"  ... and {len(pending) - 20} more")
        return

    if not pending:
        print("\nNothing to extract.")
        return

    # Process
    extracted = 0
    needs_ocr = 0
    failed = 0
    total_pages = 0

    for i, pdf_path in enumerate(pending):
        try:
            text, page_count, quality, metadata = extract_text(pdf_path)
            total_pages += page_count

            # Save text
            out = output_path(pdf_path)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(text, encoding="utf-8")

            # Save metadata
            meta_out = output_meta_path(pdf_path)
            meta = {
                "pdf_path": str(pdf_path),
                "text_path": str(out),
                "page_count": page_count,
                "text_quality": quality,
                "char_count": len(text),
                "extracted_at": datetime.now().isoformat(),
                **metadata,
            }
            meta_out.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

            # Record state
            record_result({
                "pdf_path": str(pdf_path),
                "text_path": str(out),
                "status": "extracted",
                "quality": quality,
                "page_count": page_count,
                "char_count": len(text),
                "extracted_at": datetime.now().isoformat(),
            })

            if quality == "needs_ocr":
                needs_ocr += 1
            extracted += 1

            pct = round((i + 1) / len(pending) * 100)
            print(
                f"\r  [{pct}%] {extracted} extracted, {needs_ocr} need OCR, "
                f"{failed} failed ({i + 1}/{len(pending)})",
                end="",
                flush=True,
            )

        except Exception as e:
            failed += 1
            record_result({
                "pdf_path": str(pdf_path),
                "status": "failed",
                "error": str(e),
                "attempted_at": datetime.now().isoformat(),
            })

    print("\n")
    print("=" * 60)
    print("EXTRACTION SUMMARY")
    print("=" * 60)
    print(f"  Extracted:   {extracted}")
    print(f"  Needs OCR:   {needs_ocr}")
    print(f"  Failed:      {failed}")
    print(f"  Total pages: {total_pages}")


if __name__ == "__main__":
    main()
