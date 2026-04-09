# Official Sources

## Canonical sources

### Asamblea Legislativa

- Primary entry point: [Busqueda de Leyes y Decretos](https://www.asamblea.gob.sv/leyes-y-decretos/busqueda-decretos)
- Supporting routes:
- [Decretos por Ano](https://www.asamblea.gob.sv/leyes-y-decretos/decretos-por-anios)
- [Ultimos Decretos Aprobados](https://www.asamblea.gob.sv/leyes-y-decretos/ultimos-aprobados)
- [Anuarios Legislativos](https://www.asamblea.gob.sv/leyes-y-decretos/anuarios-legislativos)
- [Constitucion](https://www.asamblea.gob.sv/leyes-y-decretos/constitucion)

Why it matters:

- Best official discovery layer for decree numbers, law names, publication metadata, and legislative chronology.
- Recent items may still be pending final publication in the Diario Oficial.
- Assembly pages are not a substitute for a reform-aware consolidated statute database.

What to store:

- Law title
- Decree number
- Emission date
- Publication date
- Diario Oficial number
- Tomo
- Source URL

### Diario Oficial / Imprenta Nacional

- Public archive: [Diario Oficial](https://www.diariooficial.gob.sv/)
- Intake and publication platform: [SIPUDO](https://sipudo.imprentanacional.gob.sv/)
- Service page indicating archive search across digitized diaries: [Busqueda de Diarios Oficiales](https://imprentanacional.gob.sv/servicios/busqueda-de-diarios-oficiales/)

Why it matters:

- This is the publication record that usually controls entry into force.
- The public archive is PDF-first and browse-heavy, so it should be mirrored into a structured acquisition queue.
- Archive coverage currently reaches from 1847 through 2026 in the year selector seen on 2026-04-06.

What to watch:

- Keep the original issue PDF forever.
- Track issue number, tomo, publication date, and page spans.
- Check for existing text layers before running OCR.

### Centro de Documentacion Judicial / Jurisprudencia

- Institutional page: [Centro de Documentacion Judicial](https://www.csj.gob.sv/centro-de-documentacion-judicial/)
- Search portal: [Jurisprudencia.gob.sv](https://www.jurisprudencia.gob.sv/)

Why it matters:

- The court documentation system explicitly states that it disseminates jurisprudence as well as laws, decrees, regulations, ordinances, and international instruments.
- It exposes legal texts in a search-driven interface that is more retrieval-friendly than the public Diario archive.

Use it for:

- Cross-checking titles and citations
- Finding searchable code text
- Filling gaps when the Assembly or Diario flow is harder to query directly

## Official but sectoral sources

### Asamblea library catalog

- Portal: [Biblioteca Asamblea](https://biblioteca.asamblea.gob.sv/)

Use it for:

- Historical code editions
- Constitutional compilations
- Older legal volumes that are easier to find in the catalog than in the live legislative portal

Limit:

- Catalog items are not guaranteed to reflect the latest reform state.

### Transparencia.gob.sv

- Portal: [Transparencia](https://www.transparencia.gob.sv/)

Use it for:

- Institutional regulations
- Manuals and normative documents
- Decentralized legal materials that do not surface cleanly in the main legislative interfaces

Limit:

- Coverage is fragmented by institution and category.

### Superintendencia del Sistema Financiero

- Portal: [Marco legal y normativo](https://ssf.gob.sv/estadisticas/marco-legal-y-normativo/leyes-2/)

Use it for:

- Finance, securities, banking, AML, pensions, and supervisory regulations

### Ministerio de Hacienda

- Institutional frame: [Marco institucional](https://www.mh.gob.sv/marco-institucional/)
- Transparency frame: [Marco Normativo](https://transparencia.mh.gob.sv/laip/es/Temas/Ley_de_Acceso_a_la_Informacion_Publica/Marco_Normativo/)

Use it for:

- Tax, budget, finance, customs, and ministry-specific normative materials

## Acquisition notes

- Treat the Assembly as the primary enactment metadata source.
- Treat the Diario Oficial as the publication-of-record source.
- Treat sectoral portals as backfill for regulations and specialized norms.
- Keep approval dates and publication dates distinct in every record.
