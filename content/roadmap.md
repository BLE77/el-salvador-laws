# Roadmap

## Phase 0

- Freeze the source inventory
- Confirm robots, access, and download patterns for each official source
- Decide which sources require browser automation

## Phase 1

- Crawl official source indexes only
- Queue raw PDFs and HTML snapshots
- Normalize canonical metadata fields

## Phase 2

- Run text extraction
- OCR only the files that need it
- Score extraction quality and re-run failed cases

## Phase 3

- Convert clean text to markdown
- Add article anchors
- Build version links between enactments, reforms, and repeals

## Phase 4

- Load metadata into PostgreSQL
- Build exact search and vector search
- Publish law pages, source pages, and machine JSON

## Phase 5

- Add consolidation workflows
- Add bilingual retrieval for constitution and high-value laws
- Add corpus QA dashboards for missing metadata, duplicate texts, and broken citations
