# El Salvador Law Atlas

This project collects the highest-value online sources for Salvadoran law and turns the research into a markdown-first corpus plan.

## What is here now

- A verified source inventory with official and secondary discovery points.
- A static site generated from markdown and JSON with no external build dependencies.
- A proposed corpus layout for law-by-law markdown pages, source provenance, and version history.

## What the research says

- The canonical backbone is the [Asamblea Legislativa legislative portal](https://www.asamblea.gob.sv/leyes-y-decretos/busqueda-decretos) plus the [Diario Oficial archive](https://www.diariooficial.gob.sv/).
- The [Diario Oficial public archive](https://www.diariooficial.gob.sv/) currently exposes issues from 1847 through 2026 by year and month, but discovery is browse-heavy and PDF-first.
- The [Centro de Documentacion Judicial](https://www.csj.gob.sv/centro-de-documentacion-judicial/) and [Jurisprudencia.gob.sv](https://www.jurisprudencia.gob.sv/) are important for laws, decrees, regulations, ordinances, and instruments that are already searchable in a legal-research interface.
- The [Asamblea annuals](https://www.asamblea.gob.sv/leyes-y-decretos/anuarios-legislativos) are useful enactment snapshots, but they should not be treated as consolidated current law after later reforms.
- Secondary databases such as [WIPO Lex](https://www.wipo.int/wipolex/en/legislation/members/profile/SV), [NATLEX](https://natlex.ilo.org/), and [FAOLEX](https://www.fao.org/faolex/country-profiles/general-profile/en/?iso3=SLV) are valuable for metadata, sector backfill, and amendment tracking.

## Working assumptions

- Every law record should preserve both enactment metadata and publication metadata.
- OCR should be a fallback, not the default, because a non-trivial share of PDFs already expose searchable text.
- One markdown file should represent one law version, with stable anchors for each article or section.
- AI search should combine exact lexical retrieval with semantic retrieval, but exact article text and provenance should remain primary.

## Immediate next moves

- Seed the corpus from official sources first.
- Persist raw PDFs and extracted text separately.
- Normalize extracted text into markdown with article anchors and structured frontmatter.
- Build a version graph so amendments, repeals, and consolidated snapshots can be tracked without losing original publications.
