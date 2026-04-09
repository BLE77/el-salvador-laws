# Markdown Schema

## Folder layout

Use a path that makes provenance obvious before any database lookup:

```text
corpus/
  official/
    diario-oficial/
      2025/
        tomo-448/
          numero-144/
            decreto-370-reforma-constitucional.md
    asamblea/
      decreto-370/
        enactment.md
  secondary/
    natlex/
    wipolex/
    faolex/
```

## Law markdown template

```yaml
---
law_id: sv-law-decreto-370
version_id: sv-law-decreto-370-2025-08-01
title: Reforma constitucional numero dos
source_tier: official
source_name: Diario Oficial
source_url: https://www.diariooficial.gob.sv/
raw_file_url: https://www.diariooficial.gob.sv/seleccion/31570
decree_no: 370
diario_oficial_no: 144
tomo: 448
publication_date: 2025-08-01
effective_date: 2025-08-01
language: es
status: published
text_quality: born_digital
raw_sha256: ...
text_sha256: ...
amends: []
amended_by: []
repeals: []
repealed_by: []
---
```

## Body conventions

- Start with a short provenance block.
- Preserve the exact title used by the official source.
- Add one anchor per article:

```markdown
## Articulo 1 {#articulo-1}
```

- Keep page references when a paragraph crosses pages:

```markdown
[p. 4]
```

- If you create a consolidated snapshot, mark it as derived and keep the original enactment and publication files intact.

## Database minimum

Every markdown file should map cleanly to database records for:

- `laws`
- `law_versions`
- `citations`
- `source_documents`
- `extraction_jobs`
- `law_edges`
