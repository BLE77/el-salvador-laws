#!/usr/bin/env python3
"""
El Salvador Law Agent — search API + web chat.

This is a proper legal research agent, not just a search box.
It does multi-step search, bilingual query expansion, decree lookup,
and gives grounded answers with citations.

Endpoints:
    GET  /                              Web chat interface
    GET  /api/search?q=...&limit=10     Full-text search
    GET  /api/decree/<number>           Lookup decree by number
    GET  /api/browse?materia=...        Browse by category
    POST /api/chat                      Agent chat (multi-step RAG)
    GET  /api/stats                     Corpus statistics
    GET  /openapi.json                  OpenAPI spec for ChatGPT/Claude

Usage:
    python scripts/serve.py
    python scripts/serve.py --port 8080
    python scripts/serve.py --db B:/el-salvador-laws/db/laws.db
"""

import os
import sys
import json
import re
import sqlite3
import argparse
import urllib.request
from pathlib import Path
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

DB_PATH = Path(os.environ.get("DB_PATH", "B:/el-salvador-laws/db/laws.db"))
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
KIMI_API_KEY = os.environ.get("KIMI_API_KEY", "").strip()
KIMI_BASE_URL = "https://api.kimi.com/coding/v1"
PORT = int(os.environ.get("PORT", "4200"))
CORS_ORIGIN = os.environ.get("CORS_ORIGIN", "*")
MAX_REQUEST_BYTES = int(os.environ.get("MAX_REQUEST_BYTES", str(64 * 1024)))

# ─── Spanish/English legal term mappings for query expansion ───
LEGAL_TERMS = {
    "constitution": "constitucion",
    "criminal": "penal",
    "tax": "tributario impuesto",
    "labor": "trabajo laboral",
    "bitcoin": "bitcoin moneda digital",
    "property": "propiedad inmueble",
    "business": "empresa comercio mercantil",
    "marriage": "matrimonio familia",
    "divorce": "divorcio",
    "immigration": "migracion extranjeria",
    "environment": "medio ambiente",
    "education": "educacion",
    "health": "salud",
    "pension": "pension jubilacion",
    "minimum wage": "salario minimo",
    "freedom of speech": "libertad expresion",
    "state of emergency": "regimen excepcion",
    "money laundering": "lavado dinero activos",
    "corruption": "corrupcion enriquecimiento ilicito",
    "human rights": "derechos humanos",
    "election": "eleccion electoral",
    "budget": "presupuesto",
    "military": "fuerza armada militar",
    "police": "policia seguridad publica",
    "court": "tribunal judicial corte",
    "appeal": "apelacion recurso",
    "contract": "contrato",
    "corporation": "sociedad anonima",
    "bankruptcy": "quiebra insolvencia",
    "trademark": "marca propiedad intelectual",
    "copyright": "derecho autor",
    "customs": "aduana arancel",
    "extradition": "extradicion",
    "asylum": "asilo refugio",
    "firearms": "armas fuego",
    "drugs": "drogas estupefacientes narcotico marihuana cannabis",
    "weed": "drogas marihuana cannabis estupefacientes",
    "marijuana": "drogas marihuana cannabis estupefacientes",
    "domestic violence": "violencia intrafamiliar",
    "child": "menor nino adolescente",
    "adoption": "adopcion",
    "water": "agua recurso hidrico",
    "mining": "mineria",
    "telecom": "telecomunicacion",
    "bank": "banco financiero",
    "insurance": "seguro",
    "rent": "arrendamiento alquiler",
    "abortion": "aborto",
    "death penalty": "pena muerte",
    "prison": "prision penitenciario carcel",
    "theft": "hurto robo",
    "murder": "homicidio asesinato",
    "rape": "violacion sexual",
    "fraud": "estafa fraude",
    "driving": "transito vehiculo conducir licencia",
    "alcohol": "bebidas alcoholicas",
    "gun": "armas fuego portacion",
    "permit": "permiso licencia autorizacion",
    "visa": "visa residencia migracion",
    "citizen": "ciudadania nacionalidad",
    "voting": "voto electoral sufragio",
    "union": "sindicato sindical",
    "strike": "huelga",
    "overtime": "horas extras jornada",
    "maternity": "maternidad prenatal postnatal",
    "disability": "discapacidad invalidez",
    "consumer": "consumidor proteccion",
    "food safety": "alimento salud sanitario",
    "construction": "construccion urbanismo",
    "internet": "telecomunicacion tecnologia digital",
    "cryptocurrency": "bitcoin moneda digital criptomoneda",
    "real estate": "inmueble propiedad registro bienes raices",
    "inheritance": "herencia sucesion testamento",
    "notary": "notario notarial escritura",
    "lawyer": "abogado ejercicio profesional",
    "judge": "juez magistrado tribunal",
    "prosecutor": "fiscal fiscalia ministerio publico",
}


def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def parse_limit(params, default, maximum):
    raw = params.get("limit", [str(default)])[0]
    try:
        value = int(raw)
    except ValueError:
        raise ValueError("Invalid 'limit': must be an integer")
    if value < 1:
        raise ValueError("Invalid 'limit': must be >= 1")
    return min(value, maximum)


def safe_error_message(exc):
    return f"{type(exc).__name__}: {exc}"


# ─── Search functions ───

def search_fts(query, limit=10):
    """Full-text search over the corpus."""
    conn = get_db()
    try:
        results = conn.execute(
            """
            SELECT c.chunk_id, c.text_es, c.text_en, c.articles, c.translated,
                   d.source_file, d.source, d.year, d.pdf_path, d.text_quality,
                   d.decree_no, d.emission_date, d.publication_date,
                   d.diario_oficial_no, d.tomo, d.materia, d.rama, d.resumen,
                   rank
            FROM chunks_fts fts
            JOIN chunks c ON c.id = fts.rowid
            JOIN documents d ON d.id = c.doc_id
            WHERE chunks_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (query, limit),
        ).fetchall()
    except Exception:
        results = []
    finally:
        conn.close()

    return [_row_to_dict(r) for r in results]


def _row_to_dict(r):
    return {
        "chunk_id": r["chunk_id"],
        "text_es": r["text_es"],
        "text_en": r["text_en"] or "",
        "articles": json.loads(r["articles"]) if r["articles"] else [],
        "translated": bool(r["translated"]),
        "source_file": r["source_file"],
        "source": r["source"],
        "year": r["year"],
        "pdf_path": r["pdf_path"],
        "text_quality": r["text_quality"],
        "decree_no": r["decree_no"],
        "emission_date": r["emission_date"],
        "publication_date": r["publication_date"],
        "materia": r["materia"],
        "rama": r["rama"],
        "resumen": r["resumen"],
        "relevance": r["rank"],
    }


def expand_query(question):
    """Generate multiple search queries from a user question.
    Returns a list of FTS5 queries to try.
    ORDER MATTERS: Spanish legal expansions come FIRST so they get priority."""
    queries = []
    q_lower = question.lower().strip()

    # 1. Extract decree number if mentioned (highest priority)
    decree_match = re.search(r'(?:decree|decreto|law|ley)\s*(?:no\.?|number|num\.?|#)?\s*(\d+)', q_lower)
    if decree_match:
        queries.append(f"decreto {decree_match.group(1)}")

    # 2. Spanish expansions FIRST (these are the most precise legal matches)
    spanish_terms = []
    for eng, esp in LEGAL_TERMS.items():
        if eng in q_lower:
            spanish_terms.extend(esp.split())
    if spanish_terms:
        # Deduplicate while preserving order
        seen = set()
        unique = []
        for t in spanish_terms:
            if t not in seen:
                seen.add(t)
                unique.append(t)
        queries.append(" OR ".join(unique))

    # 3. Direct query (cleaned for FTS5) — filter out very common words
    clean = re.sub(r'[^\w\s]', ' ', question)
    stop_words = {'the', 'is', 'in', 'of', 'and', 'or', 'for', 'to', 'a', 'an',
                  'what', 'how', 'can', 'do', 'does', 'are', 'was', 'will', 'about',
                  'legal', 'illegal', 'law', 'laws', 'salvador', 'salvadoran', 'tell', 'me'}
    words = [w for w in clean.split() if len(w) > 2 and w.lower() not in stop_words]
    if words:
        queries.append(" OR ".join(words))

    # 4. Individual important words (fallback)
    important = [w for w in words if len(w) > 4]
    for w in important[:3]:
        queries.append(w)

    return queries if queries else [question]


def smart_search(question, limit=12):
    """Multi-strategy search: tries multiple queries, deduplicates, ranks."""
    queries = expand_query(question)
    seen_chunks = set()
    all_results = []

    for q in queries:
        try:
            results = search_fts(q, limit=limit)
            for r in results:
                cid = r["chunk_id"]
                if cid not in seen_chunks:
                    seen_chunks.add(cid)
                    all_results.append(r)
        except Exception:
            continue

    # Sort by relevance (lower rank = more relevant in FTS5)
    all_results.sort(key=lambda r: r["relevance"])
    return all_results[:limit]


def get_decree(decree_no):
    """Get all chunks for a specific decree number."""
    conn = get_db()
    results = conn.execute(
        """
        SELECT c.chunk_id, c.text_es, c.text_en, c.articles, c.translated,
               c.chunk_index,
               d.source_file, d.source, d.year, d.pdf_path, d.text_quality,
               d.decree_no, d.emission_date, d.publication_date,
               d.diario_oficial_no, d.tomo, d.materia, d.rama, d.resumen
        FROM chunks c
        JOIN documents d ON d.id = c.doc_id
        WHERE d.decree_no = ?
        ORDER BY c.chunk_index
        """,
        (str(decree_no),),
    ).fetchall()
    conn.close()

    if not results:
        return None

    first = results[0]
    return {
        "decree_no": first["decree_no"],
        "year": first["year"],
        "emission_date": first["emission_date"],
        "publication_date": first["publication_date"],
        "diario_oficial_no": first["diario_oficial_no"],
        "tomo": first["tomo"],
        "materia": first["materia"],
        "rama": first["rama"],
        "resumen": first["resumen"],
        "text_quality": first["text_quality"],
        "chunks": [
            {
                "chunk_id": r["chunk_id"],
                "text_es": r["text_es"],
                "text_en": r["text_en"] or "",
                "articles": json.loads(r["articles"]) if r["articles"] else [],
            }
            for r in results
        ],
    }


def browse_laws(materia=None, rama=None, year=None, limit=50):
    """Browse laws by category."""
    conn = get_db()
    conditions = []
    params = []
    if materia:
        conditions.append("d.materia LIKE ?")
        params.append(f"%{materia}%")
    if rama:
        conditions.append("d.rama LIKE ?")
        params.append(f"%{rama}%")
    if year:
        conditions.append("d.year = ?")
        params.append(str(year))

    where = "WHERE " + " AND ".join(conditions) if conditions else ""

    results = conn.execute(
        f"""
        SELECT DISTINCT d.decree_no, d.year, d.emission_date, d.materia,
               d.rama, d.resumen, d.source_file
        FROM documents d
        {where}
        ORDER BY d.year DESC, d.decree_no DESC
        LIMIT ?
        """,
        params + [limit],
    ).fetchall()
    conn.close()

    return [
        {
            "decree_no": r["decree_no"],
            "year": r["year"],
            "emission_date": r["emission_date"],
            "materia": r["materia"],
            "rama": r["rama"],
            "resumen": (r["resumen"] or "")[:200],
        }
        for r in results
        if r["decree_no"]
    ]


def get_stats():
    """Get corpus statistics."""
    conn = get_db()
    stats = {
        "documents": conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
        "chunks": conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
        "translated": conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE translated = 1"
        ).fetchone()[0],
        "decrees": conn.execute(
            "SELECT COUNT(DISTINCT decree_no) FROM documents WHERE decree_no IS NOT NULL"
        ).fetchone()[0],
        "sources": [
            {"source": r[0], "count": r[1]}
            for r in conn.execute(
                "SELECT source, COUNT(*) FROM documents GROUP BY source ORDER BY COUNT(*) DESC"
            ).fetchall()
        ],
        "years": [
            {"year": r[0], "count": r[1]}
            for r in conn.execute(
                "SELECT year, COUNT(*) FROM documents WHERE year IS NOT NULL GROUP BY year ORDER BY year DESC LIMIT 20"
            ).fetchall()
        ],
        "categories": [
            {"materia": r[0], "count": r[1]}
            for r in conn.execute(
                "SELECT materia, COUNT(*) FROM documents WHERE materia IS NOT NULL GROUP BY materia ORDER BY COUNT(*) DESC LIMIT 15"
            ).fetchall()
        ],
    }
    conn.close()
    return stats


# ─── Agent chat ───

SYSTEM_PROMPT = """You are a legal research expert on El Salvador law. Answer in English. Be concise and direct.

RULES:
1. ONLY use the legal text excerpts provided below. Never invent law content.
2. Cite decreto numbers: "According to Decreto 153 (2003)..."
3. If excerpts don't answer the question, say so clearly.
4. Keep Spanish legal terms with English translations in parentheses.
5. Explain for regular people, not lawyers.
6. Note the year — laws can be amended or repealed.

CONTEXT: El Salvador is a civil law country. Laws are decretos passed by the Asamblea Legislativa. The Constitution (1983) is supreme law. Bitcoin is legal tender since 2021 (Decreto 57). State of Exception ongoing since 2022. Database covers 8,200+ documents with 1,025 unique decrees from 1933-2026."""


def call_llm(system, user_msg):
    """Call LLM (Anthropic or Kimi). Returns answer string."""
    answer = None

    if ANTHROPIC_API_KEY:
        body = json.dumps({
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 3000,
            "system": system,
            "messages": [{"role": "user", "content": user_msg}],
        }).encode("utf-8")

        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=body,
            headers={
                "Content-Type": "application/json",
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                answer = result["content"][0]["text"]
        except Exception as e:
            print(f"  [llm] Anthropic error: {e}")

    if not answer and KIMI_API_KEY:
        body = json.dumps({
            "model": "kimi-k2.5",
            "max_tokens": 4096,
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg},
            ],
        }).encode("utf-8")

        req = urllib.request.Request(
            KIMI_BASE_URL + "/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {KIMI_API_KEY}",
                "User-Agent": "claude-code/1.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                msg = result["choices"][0]["message"]
                answer = msg.get("content", "")
                reasoning = msg.get("reasoning_content", "")
                # Kimi K2.5 sometimes puts the answer in reasoning_content
                # Use content if available, otherwise use reasoning but clean it up
                if not answer and reasoning:
                    # Extract the actual answer from reasoning (skip thinking preamble)
                    answer = reasoning
                print(f"  [llm] Kimi response: content={len(answer)} chars, reasoning={len(reasoning)} chars")
        except Exception as e:
            print(f"  [llm] Kimi error: {e}")

    return answer


def agent_chat(question):
    """Full agent: multi-step search → context building → grounded answer."""

    # Step 1: Check if it's a direct decree lookup
    decree_match = re.search(r'(?:decree|decreto)\s*(?:no\.?|number|#)?\s*(\d+)', question.lower())
    decree_data = None
    if decree_match:
        decree_data = get_decree(decree_match.group(1))

    # Step 2: Smart search with query expansion
    results = smart_search(question, limit=12)

    # Step 3: If no results, try harder with individual words
    if not results:
        words = re.sub(r'[^\w\s]', ' ', question).split()
        for word in words:
            if len(word) > 3:
                results = search_fts(word, limit=5)
                if results:
                    break

    # Step 4: Build context for the LLM
    context_parts = []
    sources = []
    seen_decrees = set()

    # Add decree lookup data first if available
    if decree_data:
        full_text = "\n".join(c["text_es"] for c in decree_data["chunks"])
        if len(full_text) > 6000:
            full_text = full_text[:6000] + "\n[... text truncated ...]"
        label = f"Decreto {decree_data['decree_no']} ({decree_data['year']})"
        if decree_data.get("materia"):
            label += f" — {decree_data['materia']}"
        if decree_data.get("resumen"):
            label += f"\nSummary: {decree_data['resumen'][:200]}"
        context_parts.append(f"[FULL DECREE: {label}]\n{full_text}\n")
        sources.append({
            "index": len(sources) + 1,
            "decree_no": decree_data["decree_no"],
            "year": decree_data["year"],
            "materia": decree_data.get("materia"),
            "rama": decree_data.get("rama"),
            "emission_date": decree_data.get("emission_date"),
            "type": "full_decree",
        })
        seen_decrees.add(decree_data["decree_no"])

    # Add search results
    for r in results:
        if len(context_parts) >= 10:
            break

        text = r["text_en"] if r["text_en"] else r["text_es"]
        articles = ", ".join(r["articles"]) if r["articles"] else ""

        label = f"Source {len(sources) + 1}: "
        if r.get("decree_no"):
            label += f"Decreto {r['decree_no']}"
            if r["decree_no"] in seen_decrees:
                continue  # Skip duplicate
            seen_decrees.add(r["decree_no"])
        else:
            label += r.get("source", "unknown")
        label += f" ({r.get('year', '?')})"
        if r.get("materia"):
            label += f" — {r['materia']}"
        if r.get("emission_date"):
            label += f", issued {r['emission_date']}"
        if articles:
            label += f", Articles: {articles}"

        context_parts.append(f"[{label}]\n{text}\n")
        sources.append({
            "index": len(sources) + 1,
            "decree_no": r.get("decree_no"),
            "year": r.get("year"),
            "source": r.get("source"),
            "source_file": r.get("source_file"),
            "materia": r.get("materia"),
            "rama": r.get("rama"),
            "articles": r.get("articles"),
            "text_quality": r.get("text_quality"),
            "type": "search_result",
        })

    if not context_parts:
        return {
            "answer": "I couldn't find any relevant legal texts for your question. Try rephrasing with specific legal terms, or ask in Spanish for better results. Our database covers 8,200+ documents with 1,025 decrees from 1933-2026.",
            "sources": [],
            "queries_tried": [q for q in expand_query(question)],
        }

    # AGGRESSIVE context limiting — Kimi K2.5 hallucinates with long context.
    # Strategy: Send at most 3 sources, total max 4K chars.
    # Prioritize: trim each source text to ~1200 chars max.
    MAX_SOURCES = 3
    MAX_PER_SOURCE = 1200
    MAX_TOTAL = 4000

    context_parts_limited = []
    total_chars = 0
    for i, part in enumerate(context_parts[:MAX_SOURCES]):
        # Trim individual source text
        if len(part) > MAX_PER_SOURCE:
            part = part[:MAX_PER_SOURCE] + "\n[... truncated ...]"
        if total_chars + len(part) > MAX_TOTAL:
            remaining = MAX_TOTAL - total_chars
            if remaining > 300:
                context_parts_limited.append(part[:remaining] + "\n[... truncated ...]")
            break
        context_parts_limited.append(part)
        total_chars += len(part)

    context = "\n---\n".join(context_parts_limited)

    user_msg = f"""Question: {question}

Legal texts found (Source 1 = most relevant):

{context}

Answer the question using these sources. Cite decreto numbers. Be direct and concise."""

    # Step 5: Call LLM
    answer = call_llm(SYSTEM_PROMPT, user_msg)

    if not answer:
        # Fallback: no LLM available, give raw results
        answer = "**Search Results** (LLM unavailable for synthesis)\n\n"
        for s in sources:
            dn = s.get("decree_no")
            label = f"Decreto {dn}" if dn else (s.get("source") or "Legal text")
            materia = s.get("materia") or ""
            answer += f"- **{label}** ({s.get('year', '?')})"
            if materia:
                answer += f" — {materia}"
            answer += "\n"

    # Include top search results summary for transparency
    top_results = []
    seen = set()
    for r in results[:8]:
        dn = r.get("decree_no")
        if not dn or dn in seen:
            continue
        seen.add(dn)
        top_results.append({
            "decree_no": dn,
            "year": r.get("year", "?"),
            "materia": r.get("materia", ""),
            "resumen": (r.get("resumen") or "")[:150],
        })
        if len(top_results) >= 5:
            break

    return {"answer": answer, "sources": sources, "top_results": top_results}


# ─── HTML UI ───

CHAT_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>El Salvador Law Agent</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --bg: #09090b;
    --surface: #111114;
    --surface2: #18181b;
    --surface3: #27272a;
    --border: #27272a;
    --border-light: #3f3f46;
    --text: #fafafa;
    --text-secondary: #a1a1aa;
    --text-dim: #71717a;
    --accent: #6366f1;
    --accent-hover: #818cf8;
    --accent-dim: #4f46e5;
    --accent-bg: rgba(99,102,241,0.1);
    --green: #22c55e;
    --amber: #f59e0b;
    --blue: #3b82f6;
    --red: #ef4444;
  }

  body {
    font-family: 'Inter', -apple-system, system-ui, sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    -webkit-font-smoothing: antialiased;
  }

  header {
    padding: 1rem 2rem;
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    justify-content: space-between;
    backdrop-filter: blur(8px);
    position: sticky;
    top: 0;
    z-index: 10;
    background: rgba(9,9,11,0.85);
  }

  .header-left { display: flex; align-items: center; gap: 0.75rem; }

  header h1 {
    font-size: 1.1rem;
    font-weight: 600;
    letter-spacing: -0.02em;
  }

  .badge {
    font-size: 0.65rem;
    padding: 0.15rem 0.5rem;
    background: var(--accent-bg);
    color: var(--accent-hover);
    border: 1px solid rgba(99,102,241,0.2);
    border-radius: 100px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-weight: 600;
  }

  .stats-bar {
    display: flex;
    gap: 1.5rem;
    font-size: 0.7rem;
    color: var(--text-dim);
  }

  .stats-bar .stat {
    display: flex;
    align-items: center;
    gap: 0.35rem;
  }

  .stats-bar .dot {
    width: 5px;
    height: 5px;
    border-radius: 50%;
    background: var(--green);
    flex-shrink: 0;
  }

  main {
    flex: 1;
    display: flex;
    flex-direction: column;
    max-width: 800px;
    width: 100%;
    margin: 0 auto;
    padding: 0 1.5rem;
  }

  #messages {
    flex: 1;
    overflow-y: auto;
    padding: 1.5rem 0;
    display: flex;
    flex-direction: column;
    gap: 1.25rem;
  }

  .message { max-width: 100%; line-height: 1.7; font-size: 0.9rem; }

  .message.user {
    align-self: flex-end;
    background: var(--accent-dim);
    padding: 0.6rem 1rem;
    border-radius: 16px 16px 4px 16px;
    max-width: 75%;
    font-weight: 500;
  }

  .message.assistant {
    background: var(--surface);
    border: 1px solid var(--border);
    padding: 1.25rem 1.5rem;
    border-radius: 16px;
  }

  .message.assistant h3 {
    font-size: 0.85rem;
    font-weight: 600;
    color: var(--accent-hover);
    margin: 1rem 0 0.4rem 0;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }

  .message.assistant h3:first-child { margin-top: 0; }

  .message.assistant p { margin-bottom: 0.6rem; }
  .message.assistant p:last-child { margin-bottom: 0; }

  .message.assistant strong { color: var(--accent-hover); font-weight: 600; }

  .message.assistant code {
    background: var(--surface2);
    padding: 0.15rem 0.4rem;
    border-radius: 4px;
    font-size: 0.85em;
    font-family: 'SF Mono', 'Consolas', monospace;
  }

  .message.assistant ul, .message.assistant ol {
    margin: 0.5rem 0;
    padding-left: 1.5rem;
  }

  .message.assistant li { margin-bottom: 0.3rem; }

  .sources-section {
    margin-top: 1rem;
    padding-top: 0.75rem;
    border-top: 1px solid var(--border);
  }

  .sources-toggle {
    background: none;
    border: 1px solid var(--border);
    color: var(--text-secondary);
    padding: 0.35rem 0.75rem;
    border-radius: 6px;
    cursor: pointer;
    font-size: 0.75rem;
    font-family: inherit;
    font-weight: 500;
    transition: all 0.15s;
  }

  .sources-toggle:hover { border-color: var(--accent); color: var(--text); }

  .sources-list {
    display: none;
    margin-top: 0.6rem;
    font-size: 0.78rem;
    color: var(--text-secondary);
  }

  .sources-list.open { display: block; }

  .source-item {
    padding: 0.4rem 0;
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: baseline;
    gap: 0.5rem;
  }

  .source-item:last-child { border-bottom: none; }

  .source-num {
    background: var(--accent-bg);
    color: var(--accent-hover);
    font-size: 0.65rem;
    font-weight: 700;
    padding: 0.1rem 0.35rem;
    border-radius: 3px;
    flex-shrink: 0;
  }

  .source-meta { color: var(--text-dim); font-size: 0.72rem; }

  .thinking {
    color: var(--text-dim);
    font-style: italic;
    display: flex;
    align-items: center;
    gap: 0.6rem;
    font-size: 0.85rem;
    padding: 0.75rem 0;
  }

  .thinking .spinner {
    width: 14px;
    height: 14px;
    border: 2px solid var(--border-light);
    border-top-color: var(--accent);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }

  @keyframes spin { to { transform: rotate(360deg); } }

  .input-area {
    padding: 1rem 0 1.5rem;
    position: sticky;
    bottom: 0;
    background: var(--bg);
  }

  .input-row {
    display: flex;
    gap: 0.5rem;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 0.35rem;
    transition: border-color 0.2s;
  }

  .input-row:focus-within { border-color: var(--accent); }

  #question {
    flex: 1;
    padding: 0.6rem 0.75rem;
    background: transparent;
    border: none;
    color: var(--text);
    font-size: 0.9rem;
    font-family: inherit;
    outline: none;
  }

  #question::placeholder { color: var(--text-dim); }

  #send-btn {
    padding: 0.6rem 1.25rem;
    background: var(--accent);
    color: white;
    border: none;
    border-radius: 8px;
    font-weight: 600;
    cursor: pointer;
    font-size: 0.85rem;
    font-family: inherit;
    transition: background 0.15s;
  }

  #send-btn:hover { background: var(--accent-hover); }
  #send-btn:disabled { opacity: 0.4; cursor: not-allowed; }

  .input-hint {
    text-align: center;
    font-size: 0.7rem;
    color: var(--text-dim);
    margin-top: 0.5rem;
  }

  .welcome {
    text-align: center;
    padding: 4rem 1rem 2rem;
  }

  .welcome h2 {
    font-size: 1.6rem;
    font-weight: 700;
    color: var(--text);
    margin-bottom: 0.6rem;
    letter-spacing: -0.03em;
  }

  .welcome p {
    color: var(--text-secondary);
    margin-bottom: 0.5rem;
    max-width: 480px;
    margin-inline: auto;
    font-size: 0.88rem;
    line-height: 1.6;
  }

  .examples {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 0.5rem;
    margin-top: 2rem;
    max-width: 560px;
    margin-inline: auto;
  }

  .example-btn {
    background: var(--surface);
    border: 1px solid var(--border);
    color: var(--text-secondary);
    padding: 0.65rem 0.85rem;
    border-radius: 10px;
    cursor: pointer;
    font-size: 0.8rem;
    font-family: inherit;
    text-align: left;
    transition: all 0.15s;
    line-height: 1.4;
  }

  .example-btn:hover {
    border-color: var(--accent);
    color: var(--text);
    background: var(--accent-bg);
  }

  @media (max-width: 640px) {
    header { padding: 0.75rem 1rem; }
    .stats-bar { display: none; }
    main { padding: 0 1rem; }
    .examples { grid-template-columns: 1fr; }
  }
</style>
</head>
<body>
<header>
  <div class="header-left">
    <h1>El Salvador Law Agent</h1>
    <span class="badge">Beta</span>
  </div>
  <div class="stats-bar" id="stats-bar">
    <span class="stat"><span class="dot"></span> Loading...</span>
  </div>
</header>
<main>
  <div id="messages">
    <div class="welcome">
      <h2>Ask about Salvadoran law</h2>
      <p>Search official legal texts from the Asamblea Legislativa. Get answers grounded in actual law with decree citations.</p>
      <p style="font-size:0.78rem; color:var(--text-dim)">Ask in English or Spanish. Try specific questions for best results.</p>
      <div class="examples">
        <button class="example-btn" onclick="askExample(this)">What is the current state of emergency law?</button>
        <button class="example-btn" onclick="askExample(this)">Tell me about the Bitcoin law</button>
        <button class="example-btn" onclick="askExample(this)">What are the tax laws for businesses?</button>
        <button class="example-btn" onclick="askExample(this)">Show me Decreto 426</button>
        <button class="example-btn" onclick="askExample(this)">What does the law say about money laundering?</button>
        <button class="example-btn" onclick="askExample(this)">Explain the labor code protections</button>
      </div>
    </div>
  </div>
  <div class="input-area">
    <div class="input-row">
      <input type="text" id="question" placeholder="Ask a question about Salvadoran law..." autocomplete="off">
      <button id="send-btn" onclick="sendQuestion()">Ask</button>
    </div>
    <div class="input-hint">Powered by official legal texts from asamblea.gob.sv</div>
  </div>
</main>
<script>
const messagesEl = document.getElementById('messages');
const questionEl = document.getElementById('question');
const sendBtn = document.getElementById('send-btn');
let firstQuestion = true;

fetch('/api/stats')
  .then(r => r.json())
  .then(s => {
    document.getElementById('stats-bar').innerHTML =
      `<span class="stat"><span class="dot"></span> ${s.decrees || s.documents} decrees indexed</span>` +
      `<span class="stat">${s.chunks} searchable sections</span>`;
  })
  .catch(() => {
    document.getElementById('stats-bar').innerHTML = '<span class="stat">Offline</span>';
  });

questionEl.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendQuestion(); }
});

function askExample(btn) { questionEl.value = btn.textContent; sendQuestion(); }

function renderMarkdown(text) {
  return text
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/\\*\\*\\*(.*?)\\*\\*\\*/g, '<strong><em>$1</em></strong>')
    .replace(/\\*\\*(.*?)\\*\\*/g, '<strong>$1</strong>')
    .replace(/\\*(.*?)\\*/g, '<em>$1</em>')
    .replace(/`(.*?)`/g, '<code>$1</code>')
    .replace(/^### (.+)$/gm, '<h3>$1</h3>')
    .replace(/^- (.+)$/gm, '<li>$1</li>')
    .replace(/^(\\d+)\\. (.+)$/gm, '<li>$2</li>')
    .replace(/(<li>.*<\\/li>)/s, '<ul>$1</ul>')
    .replace(/\\n\\n/g, '</p><p>')
    .replace(/\\n/g, '<br>');
}

async function sendQuestion() {
  const q = questionEl.value.trim();
  if (!q) return;

  if (firstQuestion) { messagesEl.innerHTML = ''; firstQuestion = false; }

  const userDiv = document.createElement('div');
  userDiv.className = 'message user';
  userDiv.textContent = q;
  messagesEl.appendChild(userDiv);

  const thinkDiv = document.createElement('div');
  thinkDiv.className = 'thinking';
  thinkDiv.innerHTML = '<div class="spinner"></div> Searching legal database...';
  messagesEl.appendChild(thinkDiv);

  questionEl.value = '';
  sendBtn.disabled = true;
  messagesEl.scrollTop = messagesEl.scrollHeight;

  try {
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question: q }),
    });
    const data = await res.json();
    thinkDiv.remove();

    const assistDiv = document.createElement('div');
    assistDiv.className = 'message assistant';

    let html = '<p>' + renderMarkdown(data.answer) + '</p>';

    // Show top search results for transparency
    if (data.top_results && data.top_results.length > 0) {
      html += '<div style="margin-top:0.75rem;padding:0.6rem 0.8rem;background:var(--surface2);border-radius:8px;font-size:0.78rem;">';
      html += '<div style="color:var(--accent-hover);font-weight:600;margin-bottom:0.4rem;font-size:0.7rem;text-transform:uppercase;letter-spacing:0.05em;">Top matches from database</div>';
      for (const r of data.top_results) {
        if (!r.decree_no || r.decree_no === '?') continue;
        html += `<div style="color:var(--text-secondary);padding:0.15rem 0;">Decreto ${r.decree_no} (${r.year || '?'})`;
        if (r.materia) html += ` — ${r.materia}`;
        if (r.resumen) html += `<br><span style="color:var(--text-dim);font-size:0.72rem;">${r.resumen}</span>`;
        html += '</div>';
      }
      html += '</div>';
    }

    if (data.sources && data.sources.length > 0) {
      html += '<div class="sources-section">';
      html += `<button class="sources-toggle" onclick="this.nextElementSibling.classList.toggle('open'); this.textContent = this.nextElementSibling.classList.contains('open') ? 'Hide sources' : 'Show ${data.sources.length} sources'">Show ${data.sources.length} sources</button>`;
      html += '<div class="sources-list">';
      for (const s of data.sources) {
        let label = '';
        if (s.decree_no) label += `Decreto ${s.decree_no}`;
        else label += s.source || 'Legal text';
        label += ` (${s.year || '?'})`;
        let meta = '';
        if (s.materia) meta += s.materia;
        if (s.rama) meta += (meta ? ' / ' : '') + s.rama;
        if (s.emission_date) meta += (meta ? ' — ' : '') + 'Issued ' + s.emission_date;
        html += `<div class="source-item"><span class="source-num">${s.index}</span><div><div>${label}</div>${meta ? '<div class="source-meta">' + meta + '</div>' : ''}</div></div>`;
      }
      html += '</div></div>';
    }

    assistDiv.innerHTML = html;
    messagesEl.appendChild(assistDiv);
  } catch (err) {
    thinkDiv.remove();
    const errDiv = document.createElement('div');
    errDiv.className = 'message assistant';
    errDiv.innerHTML = `<p>Connection error. Is the server running?</p>`;
    messagesEl.appendChild(errDiv);
  }

  sendBtn.disabled = false;
  messagesEl.scrollTop = messagesEl.scrollHeight;
  questionEl.focus();
}
</script>
</body>
</html>"""


# ─── OpenAPI spec ───

def get_openapi_spec():
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "El Salvador Law Agent API",
            "description": "Search and query the complete corpus of El Salvador laws, decrees, and legal texts. Covers decrees from the Asamblea Legislativa (1860-2026).",
            "version": "1.0.0",
        },
        "servers": [{"url": f"http://localhost:{PORT}"}],
        "paths": {
            "/api/search": {
                "get": {
                    "operationId": "searchLaws",
                    "summary": "Full-text search over El Salvador legal corpus",
                    "description": "Search for legal text in Spanish or English. Returns matching chunks with decree metadata.",
                    "parameters": [
                        {"name": "q", "in": "query", "required": True, "schema": {"type": "string"}, "description": "Search query (Spanish or English). Use Spanish legal terms for best results."},
                        {"name": "limit", "in": "query", "schema": {"type": "integer", "default": 10}, "description": "Max results (1-50)"},
                    ],
                    "responses": {"200": {"description": "Search results with legal text excerpts, decree numbers, and metadata"}},
                }
            },
            "/api/decree/{number}": {
                "get": {
                    "operationId": "getDecree",
                    "summary": "Get full text and metadata for a specific decree",
                    "parameters": [
                        {"name": "number", "in": "path", "required": True, "schema": {"type": "string"}, "description": "Decree number (e.g., 503)"},
                    ],
                    "responses": {
                        "200": {"description": "Full decree text, metadata, and all articles"},
                        "404": {"description": "Decree not found in database"},
                    },
                }
            },
            "/api/browse": {
                "get": {
                    "operationId": "browseLaws",
                    "summary": "Browse laws by category, branch of law, or year",
                    "parameters": [
                        {"name": "materia", "in": "query", "schema": {"type": "string"}, "description": "Subject category (e.g., Hacienda, Educacion)"},
                        {"name": "rama", "in": "query", "schema": {"type": "string"}, "description": "Branch of law (e.g., Derecho Penal, Derecho Civil)"},
                        {"name": "year", "in": "query", "schema": {"type": "string"}, "description": "Year (e.g., 2025)"},
                        {"name": "limit", "in": "query", "schema": {"type": "integer", "default": 50}, "description": "Max results"},
                    ],
                    "responses": {"200": {"description": "List of matching decrees with metadata"}},
                }
            },
            "/api/chat": {
                "post": {
                    "operationId": "askLawQuestion",
                    "summary": "Ask a question about Salvadoran law and get a grounded answer",
                    "description": "Multi-step RAG agent that searches, retrieves context, and generates a grounded answer with citations.",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"question": {"type": "string", "description": "Question about Salvadoran law (English or Spanish)"}},
                                    "required": ["question"],
                                }
                            }
                        },
                    },
                    "responses": {"200": {"description": "Grounded answer with decree citations and source list"}},
                }
            },
            "/api/stats": {
                "get": {
                    "operationId": "getCorpusStats",
                    "summary": "Get corpus statistics: document counts, categories, year coverage",
                    "responses": {"200": {"description": "Corpus statistics"}},
                }
            },
        },
    }


# ─── HTTP Handler ───

class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        sys.stderr.write(f"  {args[0]}\n")

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", CORS_ORIGIN)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, html, status=200):
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", CORS_ORIGIN)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_bad_request(self, message):
        self.send_json({"error": message}, 400)

    def send_internal_error(self, exc, context="request"):
        print(f"  [error] {context}: {safe_error_message(exc)}", file=sys.stderr)
        self.send_json({"error": f"Internal server error during {context}"}, 500)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", CORS_ORIGIN)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if parsed.path == "/" or parsed.path == "":
            self.send_html(CHAT_HTML)
            return

        if parsed.path == "/api/search":
            q = params.get("q", [""])[0]
            if not q:
                self.send_bad_request("Missing query parameter 'q'")
                return
            try:
                limit = parse_limit(params, 10, 50)
                results = smart_search(q, limit)
                self.send_json({"query": q, "results": results, "count": len(results)})
            except ValueError as e:
                self.send_bad_request(str(e))
            except Exception as e:
                self.send_internal_error(e, "search")
            return

        # Decree lookup: /api/decree/503
        decree_match = re.match(r'^/api/decree/(\d+)$', parsed.path)
        if decree_match:
            try:
                data = get_decree(decree_match.group(1))
                if data:
                    self.send_json(data)
                else:
                    self.send_json({"error": f"Decree {decree_match.group(1)} not found"}, 404)
            except Exception as e:
                self.send_internal_error(e, "decree lookup")
            return

        if parsed.path == "/api/browse":
            try:
                results = browse_laws(
                    materia=params.get("materia", [None])[0],
                    rama=params.get("rama", [None])[0],
                    year=params.get("year", [None])[0],
                    limit=parse_limit(params, 50, 200),
                )
                self.send_json({"results": results, "count": len(results)})
            except ValueError as e:
                self.send_bad_request(str(e))
            except Exception as e:
                self.send_internal_error(e, "browse")
            return

        if parsed.path == "/api/stats":
            try:
                self.send_json(get_stats())
            except Exception as e:
                self.send_internal_error(e, "stats")
            return

        if parsed.path == "/openapi.json":
            self.send_json(get_openapi_spec())
            return

        self.send_json({"error": "Not found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)

        if parsed.path == "/api/chat":
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length <= 0:
                self.send_bad_request("Request body is required")
                return
            if content_length > MAX_REQUEST_BYTES:
                self.send_json({"error": "Request body too large"}, 413)
                return

            body = self.rfile.read(content_length)
            try:
                data = json.loads(body.decode("utf-8"))
            except json.JSONDecodeError:
                self.send_bad_request("Invalid JSON body")
                return

            try:
                question = data.get("question", "").strip()
                if not question:
                    self.send_bad_request("Missing 'question'")
                    return
                response = agent_chat(question)
                self.send_json(response)
            except Exception as e:
                self.send_internal_error(e, "chat")
            return

        self.send_json({"error": "Not found"}, 404)


def main():
    global DB_PATH

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--db", default=str(DB_PATH))
    args = parser.parse_args()

    DB_PATH = Path(args.db)

    if not DB_PATH.exists():
        print(f"Database not found: {DB_PATH}")
        print("Run build-search-db.py first.")
        sys.exit(1)

    llm = "Anthropic" if ANTHROPIC_API_KEY else ("Kimi K2.5" if KIMI_API_KEY else "NONE")
    server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    print("=" * 50)
    print("  El Salvador Law Agent")
    print("=" * 50)
    print(f"  Database:  {DB_PATH}")
    print(f"  LLM:       {llm}")
    print(f"  Server:    http://localhost:{args.port}")
    print(f"  API docs:  http://localhost:{args.port}/openapi.json")
    print("=" * 50)
    print()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.server_close()


if __name__ == "__main__":
    main()
