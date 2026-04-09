# Ingestion Architecture

## Core principle

Keep the raw file, the normalized text, and the retrieval index as separate layers.

## Recommended storage model

- Raw layer: immutable PDFs and downloaded HTML snapshots in object storage
- Metadata layer: PostgreSQL tables for laws, versions, sources, citations, and extraction jobs
- Retrieval layer: PostgreSQL full-text search plus vector embeddings for semantic fallback

## Minimal pipeline

### 1. Acquire

- Crawl official sources first:
- [Asamblea search](https://www.asamblea.gob.sv/leyes-y-decretos/busqueda-decretos)
- [Asamblea annuals](https://www.asamblea.gob.sv/leyes-y-decretos/anuarios-legislativos)
- [Diario Oficial archive](https://www.diariooficial.gob.sv/)
- [Jurisprudencia.gob.sv](https://www.jurisprudencia.gob.sv/)

Rules:

- Save the raw file exactly as downloaded.
- Hash every file and deduplicate by hash before doing expensive extraction.
- Store a source snapshot URL for reproducibility.

### 2. Extract text

- Try direct text extraction first.
- Only run OCR when the text layer is missing or clearly broken.
- Recommended OCR stack:
- [OCRmyPDF](https://ocrmypdf.readthedocs.io/en/stable/introduction.html)
- [Tesseract](https://tesseract-ocr.github.io/tessdoc/)

Useful defaults:

- language: `spa`
- fallback language: `eng`
- use `--skip-text` for mixed PDFs
- use `--redo-ocr` when the embedded text layer is unusable

### 3. Normalize to markdown

- Preferred converter: [PyMuPDF4LLM](https://pymupdf.readthedocs.io/en/latest/pymupdf4llm/index.html)
- Fallback for ugly layouts and tables: Docling

Normalization goals:

- Remove repeating headers and footers
- Repair hyphenation across line wraps
- Preserve article numbers and section titles
- Preserve page references for citation
- Add stable anchors per article, chapter, and title

### 4. Build a version graph

Every law should distinguish:

- original enactment
- official publication
- amended versions
- repeals
- derived consolidated snapshots

Store graph edges such as:

- `amends`
- `amended_by`
- `repeals`
- `repealed_by`
- `consolidates`

### 5. Index for AI retrieval

- Exact search: PostgreSQL `tsvector`
- Semantic search: [pgvector](https://github.com/pgvector/pgvector)
- Optional fast browse layer: [Meilisearch](https://www.meilisearch.com/docs/learn/relevancy/typo_tolerance_settings)

Retrieval should return:

- exact article text
- law title
- version status
- decree number
- Diario Oficial citation
- page number
- source URL

## Site pattern

- One page per law version
- One anchor per article or section
- Raw PDF, markdown text, and machine JSON exposed side by side
- Version timeline visible to both humans and retrieval systems
