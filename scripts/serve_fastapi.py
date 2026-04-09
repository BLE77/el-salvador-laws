#!/usr/bin/env python3
"""
El Salvador Law Agent — FastAPI Production Server (single-file).

Four-layer search architecture:
    1. Wiki Layer    — Pre-compiled markdown wiki pages (fastest, best quality)
    2. QMD Hybrid    — BM25 + vector + LLM reranking via QMD CLI (5s timeout)
    3. FTS5 Fallback — SQLite FTS5 keyword search (always available)
    4. Web Fallback  — DuckDuckGo web search (only when DB results are sparse)

Endpoints:
    GET  /                  Web chat interface
    GET  /api/search?q=...  Full-text search
    GET  /api/decree/{N}    Lookup decree by number
    GET  /api/browse        Browse by category/year
    POST /api/chat          Agent chat (three-layer RAG)
    GET  /api/stats         Corpus statistics
    GET  /openapi.json      OpenAPI spec
    GET  /healthz           Health check

Usage:
    python scripts/serve_fastapi.py
    python scripts/serve_fastapi.py --port 8080
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import sqlite3
import sys
import time
import urllib.parse
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

# ---------------------------------------------------------------------------
# Configuration (env vars with hardcoded defaults)
# ---------------------------------------------------------------------------

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
KIMI_API_KEY = os.environ.get("KIMI_API_KEY", "").strip()
KIMI_BASE_URL = "https://api.kimi.com/coding/v1"
DB_PATH = Path(os.environ.get("DB_PATH", "B:/el-salvador-laws/db/laws.db"))
WIKI_DIR = Path(os.environ.get("WIKI_DIR", "B:/el-salvador-laws/wiki"))
QMD_CMD = os.environ.get("QMD_CMD", r"C:\Program Files\nodejs\qmd.cmd")
QMD_COLLECTION = os.environ.get("QMD_COLLECTION", "el-salvador-laws")
QMD_ENABLED = os.environ.get("QMD_ENABLED", "1").strip() not in {"0", "false", "False"}
PORT = int(os.environ.get("PORT", "4200"))
CORS_ORIGIN = os.environ.get("CORS_ORIGIN", "*")
MAX_REQUEST_BYTES = int(os.environ.get("MAX_REQUEST_BYTES", str(64 * 1024)))

# ---------------------------------------------------------------------------
# Runtime state
# ---------------------------------------------------------------------------

_wiki_available = False
_wiki_index: dict[str, dict[str, Any]] = {}
_qmd_available = False
_db_ok = False

# Thread pool for blocking SQLite calls
_db_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="db")

# Shared httpx client (created at startup)
_http_client: httpx.AsyncClient | None = None

# ---------------------------------------------------------------------------
# Lightweight conversation memory (in-memory, auto-expiring)
# ---------------------------------------------------------------------------

MAX_HISTORY = 5       # Keep last 5 exchanges per session
SESSION_TIMEOUT = 1800  # 30 minutes of inactivity


class SessionEntry:
    def __init__(self):
        self.history: list[dict[str, str]] = []  # [{"q": "...", "a_summary": "..."}, ...]
        self.last_active: float = time.time()


_sessions: dict[str, SessionEntry] = {}


def get_session(session_id: str) -> SessionEntry:
    """Retrieve or create a session, cleaning expired ones periodically."""
    now = time.time()
    expired = [k for k, v in _sessions.items() if now - v.last_active > SESSION_TIMEOUT]
    for k in expired:
        del _sessions[k]

    if session_id not in _sessions:
        _sessions[session_id] = SessionEntry()

    entry = _sessions[session_id]
    entry.last_active = now
    return entry


def add_to_history(session_id: str, question: str, answer: str):
    """Store a short summary of the exchange in session history."""
    entry = get_session(session_id)
    summary = answer[:200].rsplit(' ', 1)[0] + '...' if len(answer) > 200 else answer
    entry.history.append({"q": question, "a_summary": summary})
    if len(entry.history) > MAX_HISTORY:
        entry.history.pop(0)


def format_history_context(session_id: str) -> str:
    """Build a conversation history string to prepend to the user message."""
    entry = get_session(session_id)
    if not entry.history:
        return ""
    lines = ["Previous conversation:"]
    for h in entry.history:
        lines.append(f'- User asked: "{h["q"]}"')
        lines.append(f'  You answered: "{h["a_summary"]}"')
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Spanish/English legal term mappings for query expansion
# ---------------------------------------------------------------------------

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
    "employee": "empleado trabajador patrono cotizacion ISSS AFP",
    "employer": "patrono empleador cotizacion ISSS AFP INSAFORP",
    "payroll": "planilla cotizacion patronal ISSS AFP salario",
    "social security": "seguro social ISSS cotizacion",
    "hire": "contratacion empleado trabajador patrono",
    "salary": "salario sueldo remuneracion",
    "severance": "indemnizacion despido",
    "christmas bonus": "aguinaldo",
    "vacation": "vacaciones",
    "foreign business": "empresa extranjera inversion extranjera",
    "start a business": "empresa constitucion sociedad comercio mercantil registro",
    "open a business": "empresa constitucion sociedad comercio mercantil registro",
    "LLC": "sociedad responsabilidad limitada SRL empresa",
    "sociedad anonima": "sociedad anonima SA empresa mercantil comercio",
    "comerciante": "comerciante matricula comercio mercantil registro",
    "restaurant": "establecimiento comercio permiso municipal sanitario",
    "invest": "inversion extranjera empresa sociedad capital",
    "close a business": "disolucion liquidacion sociedad empresa",
    "assault": "lesiones agresion golpes violencia",
    "punch": "lesiones agresion golpes violencia",
    "threaten": "amenazas intimidacion",
    "machete": "armas agresion lesiones amenazas",
    "knife": "arma blanca portacion armas",
    "state of exception": "regimen excepcion suspension derechos detencion",
    "estado de excepcion": "regimen excepcion suspension derechos detencion",
    "lock up": "detencion captura prision preventiva",
    "detained": "detencion captura prision preventiva",
    "self defense": "legitima defensa penal",
    "restraining order": "medidas proteccion violencia intrafamiliar",
    "drinking age": "bebidas alcoholicas menor edad expendio",
    "lemon law": "consumidor defectuoso garantia devolucion producto",
    "refund": "consumidor devolucion garantia reclamo",
    "defective": "consumidor defectuoso garantia reclamo",
    "warranty": "garantia consumidor producto defectuoso",
    "scam": "estafa fraude consumidor denuncia",
    "price increase": "consumidor precio tarifa telecomunicacion",
    "landlord": "arrendamiento alquiler inquilino desalojo",
    "eviction": "desalojo arrendamiento inquilino lanzamiento",
    "tenant": "arrendamiento alquiler inquilino",
    "buy land": "propiedad inmueble compraventa registro escritura",
    "buy house": "propiedad inmueble compraventa registro escritura",
    "property title": "registro propiedad escritura titulo inmueble",
    "squatter": "posesion usurpacion inmueble terreno invasion",
    "building permit": "construccion permiso urbanismo municipal",
    "zoning": "urbanismo ordenamiento territorial uso suelo",
    "garbage": "municipalidad servicio basura desecho residuo",
    "municipal": "municipalidad alcaldia gobierno local",
    "complain": "reclamo denuncia queja recurso",
    "corrupt": "corrupcion funcionario publico peculado malversacion",
    "public records": "acceso informacion publica transparencia",
}

# ---------------------------------------------------------------------------
# System prompt for LLM
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a professional Salvadoran law search assistant. Your job is to help people find and understand the laws of El Salvador. You search a database of 8,200+ official legal documents and explain what the law says in plain English.

IMPORTANT BOUNDARIES:
- You are a LAW SEARCH TOOL, not a lawyer. Always include a brief disclaimer that your answers are informational only and not legal advice. Keep it short and natural — one sentence at the end, not a lecture.
- STAY ON TOPIC. You only answer questions about Salvadoran law, legal processes, government regulations, and related practical questions (taxes, business registration, immigration, etc.). If someone asks about something completely unrelated (recipes, dating advice, coding help, sports, etc.), politely redirect them: "I'm a Salvadoran law assistant — I can help you find information about laws and regulations in El Salvador. What legal topic can I help you with?"
- NEVER express political opinions. Never comment positively or negatively about any political figure, party, or administration — including the President, NUEVAS IDEAS, or any opposition party. If asked political opinion questions, say: "I'm a legal research tool — I can help you find what the law says, but I don't have political opinions. Would you like me to look up a specific law or regulation?"
- NEVER give personal opinions on whether laws are good, bad, fair, or unfair. Just explain what the law says.
- Be professional and helpful. You're like a really knowledgeable legal librarian — you find the right law, explain what it says clearly, and point people in the right direction.

RULES:
1. ONLY use the legal text excerpts provided below. Never invent law content.
2. ALWAYS cite decreto numbers explicitly. Every answer must include at least one "Decreto N (year)" citation. Example: "According to Decreto 153 (2003)...". If you know which decreto the information comes from, name it. Users need these numbers to look up the laws themselves.
3. If excerpts don't answer the question, say so clearly and suggest they consult a licensed attorney.
4. Keep Spanish legal terms with English translations in parentheses.
5. Explain for regular people, not lawyers. Be clear and direct.
6. Note the year — laws can be amended or repealed.
7. If a law is marked as REPEALED, warn the user clearly and try to cite the replacement law instead.
8. When citing a law, check if amendments exist in the search results. If you see amendments (e.g., D-338/2022 amending D-153/2003), mention them and explain what changed.
9. Always state the most recent version of the law. For example, say "Decreto 153 (2003), as amended by D-338 (2022)" rather than just "Decreto 153 (2003)".
10. If web search results are included, note that these are from external sources and recommend verifying critical information with a licensed attorney.
11. You are having a conversation. If the user asks a follow-up question (like "what about..." or "and for foreigners?" or "how much is that?"), use the conversation context to understand what they're referring to. Be helpful and natural.
12. For real-life situations ("someone punched me", "my boss won't pay me", "I got scammed"), help them find the relevant law and explain their legal options. This is exactly what you're for — helping people understand their rights. But always remind them to consult an attorney for their specific case.

CONTEXT: El Salvador is a civil law country. Laws are decretos passed by the Asamblea Legislativa. The Constitution (1983) is supreme law. Bitcoin is legal tender since 2021 (Decreto 57). State of Exception ongoing since 2022. Database covers 8,200+ documents with 1,025 unique decrees from 1933-2026."""

# ---------------------------------------------------------------------------
# Initialization helpers
# ---------------------------------------------------------------------------


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from markdown text."""
    meta: dict[str, Any] = {}
    body = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            body = parts[2].strip()
            for line in parts[1].strip().splitlines():
                line = line.strip()
                if ":" in line:
                    key, _, val = line.partition(":")
                    key = key.strip()
                    val = val.strip()
                    if val.startswith("[") and val.endswith("]"):
                        val = [v.strip().strip('"').strip("'") for v in val[1:-1].split(",")]
                    elif val.startswith('"') and val.endswith('"'):
                        val = val[1:-1]
                    elif val.startswith("'") and val.endswith("'"):
                        val = val[1:-1]
                    meta[key] = val
    return meta, body


def init_wiki() -> None:
    """Scan the wiki directory and build an in-memory index."""
    global _wiki_available, _wiki_index
    _wiki_index = {}

    if not WIKI_DIR.is_dir():
        print(f"  [wiki] Directory not found: {WIKI_DIR} -- wiki layer disabled")
        _wiki_available = False
        return

    count = 0
    for md_file in WIKI_DIR.rglob("*.md"):
        try:
            raw = md_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        meta, body = _parse_frontmatter(raw)
        slug = md_file.stem.lower()

        title = meta.get("title", md_file.stem.replace("-", " ").replace("_", " "))
        tags = meta.get("tags", [])
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",")]
        aliases = meta.get("aliases", [])
        if isinstance(aliases, str):
            aliases = [a.strip() for a in aliases.split(",")]
        decree = meta.get("decreto", meta.get("decree", meta.get("decree_no", "")))

        _wiki_index[slug] = {
            "path": md_file,
            "title": title if isinstance(title, str) else str(title),
            "tags": tags,
            "aliases": aliases,
            "decreto": str(decree) if decree else "",
            "body": body,
            "meta": meta,
        }
        count += 1

    _wiki_available = count > 0
    print(f"  [wiki] Indexed {count} pages from {WIKI_DIR}")


async def init_qmd() -> None:
    """Check if the QMD CLI tool is available (async subprocess)."""
    global _qmd_available

    if not QMD_ENABLED:
        _qmd_available = False
        print("  [qmd]  Disabled by QMD_ENABLED=0")
        return

    try:
        proc = await asyncio.create_subprocess_exec(
            QMD_CMD, "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)
        _qmd_available = proc.returncode == 0
        if _qmd_available:
            version = (stdout or stderr).decode("utf-8", errors="replace").strip()
            print(f"  [qmd]  Available: {version}")
        else:
            print(f"  [qmd]  Not available (exit {proc.returncode})")
    except FileNotFoundError:
        _qmd_available = False
        print("  [qmd]  Not installed")
    except asyncio.TimeoutError:
        _qmd_available = False
        print("  [qmd]  Timeout checking version")
    except Exception as e:
        _qmd_available = False
        print(f"  [qmd]  Error checking: {e}")


def verify_db() -> bool:
    """Verify database connection works."""
    global _db_ok
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("SELECT 1 FROM documents LIMIT 1")
        conn.close()
        _db_ok = True
        print(f"  [db]   OK: {DB_PATH}")
    except Exception as e:
        _db_ok = False
        print(f"  [db]   FAILED: {e}")
    return _db_ok


# ---------------------------------------------------------------------------
# Database helpers (sync, run in executor)
# ---------------------------------------------------------------------------


def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _row_to_dict(r: sqlite3.Row) -> dict:
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
        "status": r["status"] or "unknown",
        "repealed_by": r["repealed_by"] or "",
        "relevance": r["rank"],
    }


# ---------------------------------------------------------------------------
# Layer 1: Wiki Search
# ---------------------------------------------------------------------------


def search_wiki(question: str, limit: int = 3) -> list[dict]:
    """Search pre-compiled wiki pages using keyword matching."""
    if not _wiki_available:
        return []

    q_lower = question.lower().strip()
    results = []

    q_clean = re.sub(r'[^\w\s]', ' ', q_lower)
    stop_words = {
        'the', 'is', 'in', 'of', 'and', 'or', 'for', 'to', 'a', 'an',
        'what', 'how', 'can', 'do', 'does', 'are', 'was', 'will', 'about',
        'legal', 'illegal', 'law', 'laws', 'salvador', 'salvadoran', 'tell',
        'me', 'el', 'la', 'de', 'del', 'los', 'las', 'en', 'que', 'es',
    }
    q_tokens = [w for w in q_clean.split() if len(w) > 2 and w not in stop_words]

    expanded_tokens = list(q_tokens)
    for eng, esp in LEGAL_TERMS.items():
        if eng in q_lower:
            expanded_tokens.extend(esp.split())

    decree_match = re.search(
        r'(?:decree|decreto|law|ley)\s*(?:no\.?|number|num\.?|#)?\s*(\d+)', q_lower
    )
    target_decreto = decree_match.group(1) if decree_match else None

    for slug, entry in _wiki_index.items():
        score = 0.0

        if target_decreto and entry["decreto"] == target_decreto:
            score += 100.0

        searchable = " ".join([
            entry["title"].lower(),
            slug,
            " ".join(entry["tags"]) if isinstance(entry["tags"], list) else str(entry["tags"]),
            " ".join(entry["aliases"]) if isinstance(entry["aliases"], list) else str(entry["aliases"]),
            entry["body"][:2000].lower(),
        ])

        matches = 0
        for token in expanded_tokens:
            if token.lower() in searchable:
                matches += 1

        if expanded_tokens:
            token_score = matches / len(expanded_tokens)
            score += token_score * 50.0

        title_lower = entry["title"].lower() + " " + slug
        title_matches = sum(1 for t in q_tokens if t in title_lower)
        if q_tokens:
            score += (title_matches / len(q_tokens)) * 30.0

        if score > 15.0:
            results.append({
                "title": entry["title"],
                "decreto": entry["decreto"],
                "content": entry["body"],
                "score": score,
                "source": "wiki",
                "tags": entry["tags"],
                "path": str(entry["path"]),
            })

    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:limit]


# ---------------------------------------------------------------------------
# Layer 2: QMD Hybrid Search (async subprocess with 5s timeout)
# ---------------------------------------------------------------------------


async def search_qmd(question: str, limit: int = 5) -> list[dict]:
    """Query QMD via async subprocess with strict 5-second timeout."""
    if not _qmd_available:
        return []

    try:
        proc = await asyncio.create_subprocess_exec(
            QMD_CMD, "search", question,
            "--collection", QMD_COLLECTION,
            "--json",
            "--limit", str(limit),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)

        if proc.returncode != 0:
            err_text = stderr.decode("utf-8", errors="replace")[:200]
            print(f"  [qmd]  Query error (exit {proc.returncode}): {err_text}")
            return []

        data = json.loads(stdout.decode("utf-8", errors="replace"))

        qmd_results: list[dict] = []
        items = data if isinstance(data, list) else data.get("results", [])
        for item in items:
            content = item.get("content", item.get("text", item.get("snippet", "")))
            title = item.get("title", item.get("source", "QMD result"))
            score_val = float(item.get("score", item.get("relevance", 0.0)))

            decreto = ""
            year = ""
            filepath = item.get("file", "")
            fm = re.search(r'-(\d{4})-decreto-(\d+)', filepath)
            if fm:
                year = fm.group(1)
                decreto = fm.group(2)
            else:
                tm = re.search(r'[Dd]ecreto\s+(?:N[°o]\.?\s*)?(\d+)', title)
                if tm:
                    decreto = tm.group(1)
                ym = re.search(r'\((\d{4})\)', title)
                if ym:
                    year = ym.group(1)

            qmd_results.append({
                "title": title,
                "decreto": decreto,
                "content": content,
                "score": score_val,
                "source": "qmd",
                "year": year,
                "materia": "",
            })

        return qmd_results[:limit]

    except asyncio.TimeoutError:
        print("  [qmd]  Query timed out (5s)")
        # Kill the hanging process
        try:
            proc.kill()  # type: ignore[possibly-undefined]
        except Exception:
            pass
        return []
    except json.JSONDecodeError as e:
        print(f"  [qmd]  Failed to parse JSON output: {e}")
        return []
    except Exception as e:
        print(f"  [qmd]  Query error: {e}")
        return []


# ---------------------------------------------------------------------------
# Layer 3: FTS5 Search (sync, run in executor)
# ---------------------------------------------------------------------------


def _search_fts_sync(query: str, limit: int = 10) -> list[dict]:
    """Full-text search over the corpus using SQLite FTS5."""
    conn = _get_db()
    try:
        results = conn.execute(
            """
            SELECT c.chunk_id, c.text_es, c.text_en, c.articles, c.translated,
                   d.source_file, d.source, d.year, d.pdf_path, d.text_quality,
                   d.decree_no, d.emission_date, d.publication_date,
                   d.diario_oficial_no, d.tomo, d.materia, d.rama, d.resumen,
                   d.status, d.repealed_by,
                   rank
            FROM chunks_fts fts
            JOIN chunks c ON c.id = fts.rowid
            JOIN documents d ON d.id = c.doc_id
            WHERE chunks_fts MATCH ?
            ORDER BY
                CASE COALESCE(d.status, 'unknown')
                    WHEN 'active' THEN 0
                    WHEN 'unknown' THEN 1
                    WHEN 'repealed' THEN 2
                    ELSE 1
                END,
                rank
            LIMIT ?
            """,
            (query, limit),
        ).fetchall()
    except Exception:
        results = []
    finally:
        conn.close()

    return [_row_to_dict(r) for r in results]


# ---------------------------------------------------------------------------
# Query expansion
# ---------------------------------------------------------------------------


def expand_query(question: str) -> list[str]:
    """Generate multiple search queries from a user question."""
    queries: list[str] = []
    q_lower = question.lower().strip()

    decree_match = re.search(
        r'(?:decree|decreto|law|ley)\s*(?:no\.?|number|num\.?|#)?\s*(\d+)', q_lower
    )
    if decree_match:
        queries.append(f"decreto {decree_match.group(1)}")

    spanish_terms: list[str] = []
    for eng, esp in LEGAL_TERMS.items():
        if eng in q_lower:
            spanish_terms.extend(esp.split())
    if spanish_terms:
        seen: set[str] = set()
        unique: list[str] = []
        for t in spanish_terms:
            if t not in seen:
                seen.add(t)
                unique.append(t)
        queries.append(" OR ".join(unique))

    clean = re.sub(r'[^\w\s]', ' ', question)
    stop_words = {
        'the', 'is', 'in', 'of', 'and', 'or', 'for', 'to', 'a', 'an',
        'what', 'how', 'can', 'do', 'does', 'are', 'was', 'will', 'about',
        'legal', 'illegal', 'law', 'laws', 'salvador', 'salvadoran', 'tell', 'me',
    }
    words = [w for w in clean.split() if len(w) > 2 and w.lower() not in stop_words]
    if words:
        queries.append(" OR ".join(words))

    important = [w for w in words if len(w) > 4]
    for w in important[:3]:
        queries.append(w)

    return queries if queries else [question]


def _search_fts_expanded_sync(question: str, limit: int = 12) -> list[dict]:
    """Multi-strategy FTS search: tries multiple queries, deduplicates, ranks."""
    queries = expand_query(question)
    seen_chunks: set[str] = set()
    all_results: list[dict] = []

    for q in queries:
        try:
            results = _search_fts_sync(q, limit=limit)
            for r in results:
                cid = r["chunk_id"]
                if cid not in seen_chunks:
                    seen_chunks.add(cid)
                    all_results.append(r)
        except Exception:
            continue

    all_results.sort(key=lambda r: r["relevance"])
    return all_results[:limit]


# ---------------------------------------------------------------------------
# Web Search Fallback (async) — Layer 4
# ---------------------------------------------------------------------------


async def search_web(query: str, max_results: int = 5) -> list[dict]:
    """Web search fallback using DuckDuckGo Instant Answer API.

    Only called when wiki/QMD/FTS layers produce sparse results.
    Targets official Salvadoran legal sites for relevance.
    """
    try:
        search_url = "https://api.duckduckgo.com/"
        params = {
            "q": f"El Salvador ley {query}",
            "format": "json",
            "no_html": "1",
        }
        client = _http_client or httpx.AsyncClient()
        try:
            resp = await client.get(search_url, params=params, timeout=10.0)
            data = resp.json()
        finally:
            if _http_client is None:
                await client.aclose()

        results: list[dict] = []

        # Main abstract (if available)
        if data.get("AbstractText"):
            results.append({
                "layer": "web",
                "title": data.get("Heading", ""),
                "snippet": data["AbstractText"][:500],
                "url": data.get("AbstractURL", ""),
            })

        # Related topics
        for topic in data.get("RelatedTopics", []):
            if len(results) >= max_results:
                break
            if isinstance(topic, dict) and topic.get("Text"):
                results.append({
                    "layer": "web",
                    "title": topic.get("Text", "")[:80],
                    "snippet": topic["Text"][:300],
                    "url": topic.get("FirstURL", ""),
                })

        return results
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Four-Layer Orchestrator (async)
# ---------------------------------------------------------------------------


async def smart_search(question: str, limit: int = 12) -> dict:
    """Four-layer search orchestrator. Wiki and QMD run concurrently; web is a fallback."""
    loop = asyncio.get_running_loop()

    # Run wiki (sync) in executor and QMD (async) concurrently
    wiki_future = loop.run_in_executor(_db_executor, search_wiki, question, 5)
    qmd_future = search_qmd(question, limit=5)
    fts_future = loop.run_in_executor(_db_executor, _search_fts_expanded_sync, question, limit)

    wiki_results, qmd_results, fts_results = await asyncio.gather(
        wiki_future, qmd_future, fts_future
    )

    wiki_hit = len(wiki_results) > 0 and wiki_results[0]["score"] > 50.0

    seen_decretos: set[str] = set()
    merged: list[dict] = []

    # Priority 1: Wiki results
    for wr in wiki_results:
        decreto = wr.get("decreto", "")
        if decreto:
            seen_decretos.add(decreto)
        merged.append({
            "layer": "wiki",
            "title": wr["title"],
            "decreto": decreto,
            "content": wr["content"],
            "score": wr["score"],
            "year": wr.get("meta", {}).get("year", ""),
            "materia": wr.get("meta", {}).get("materia", ""),
            "tags": wr.get("tags", []),
        })

    # Priority 2: QMD results
    for qr in qmd_results:
        decreto = qr.get("decreto", "")
        if decreto and decreto in seen_decretos:
            continue
        if decreto:
            seen_decretos.add(decreto)
        merged.append({
            "layer": "qmd",
            "title": qr["title"],
            "decreto": decreto,
            "content": qr["content"],
            "score": qr["score"],
            "year": qr.get("year", ""),
            "materia": qr.get("materia", ""),
        })

    # Priority 3: FTS results
    for fr in fts_results:
        decreto = fr.get("decree_no", "")
        if decreto and decreto in seen_decretos:
            continue
        if decreto:
            seen_decretos.add(decreto)
        text = fr.get("text_en") or fr.get("text_es", "")
        status = fr.get("status", "unknown")
        title_prefix = "[REPEALED] " if status == "repealed" else ""
        merged.append({
            "layer": "fts",
            "title": f"{title_prefix}Decreto {decreto}" if decreto else fr.get("source", "Legal text"),
            "decreto": decreto,
            "content": text,
            "score": -fr.get("relevance", 0),
            "year": fr.get("year", ""),
            "materia": fr.get("materia", ""),
            "source_file": fr.get("source_file", ""),
            "articles": fr.get("articles", []),
            "emission_date": fr.get("emission_date", ""),
            "resumen": fr.get("resumen", ""),
            "status": status,
            "repealed_by": fr.get("repealed_by", ""),
        })

    layers_used: list[str] = []
    if wiki_results:
        layers_used.append("wiki")
    if qmd_results:
        layers_used.append("qmd")
    if fts_results:
        layers_used.append("fts")

    # Priority 4: Web search fallback — only when DB results are sparse
    web_results: list[dict] = []
    if len(merged) < 3:
        web_results = await search_web(question)
        if web_results:
            layers_used.append("web")
            for wr in web_results:
                merged.append({
                    "layer": "web",
                    "title": wr.get("title", ""),
                    "decreto": "",
                    "content": wr.get("snippet", ""),
                    "score": 0,
                    "year": "",
                    "materia": "",
                    "url": wr.get("url", ""),
                })

    _status_order = {"active": 0, "unknown": 1, "repealed": 2}
    merged.sort(key=lambda r: _status_order.get(r.get("status", "unknown"), 1))

    return {
        "wiki_results": wiki_results,
        "qmd_results": qmd_results,
        "fts_results": fts_results,
        "web_results": web_results,
        "merged": merged[:limit],
        "layers_used": layers_used,
        "wiki_hit": wiki_hit,
    }


# ---------------------------------------------------------------------------
# Decree lookup (sync, run in executor)
# ---------------------------------------------------------------------------


def _get_decree_sync(decree_no: str) -> dict | None:
    """Get all chunks for a specific decree number."""
    conn = _get_db()
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


def _browse_laws_sync(
    materia: str | None = None,
    rama: str | None = None,
    year: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Browse laws by category."""
    conn = _get_db()
    conditions: list[str] = []
    params: list[Any] = []
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


def _get_stats_sync() -> dict:
    """Get corpus statistics."""
    conn = _get_db()
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
                "SELECT year, COUNT(*) FROM documents WHERE year IS NOT NULL "
                "GROUP BY year ORDER BY year DESC LIMIT 20"
            ).fetchall()
        ],
        "categories": [
            {"materia": r[0], "count": r[1]}
            for r in conn.execute(
                "SELECT materia, COUNT(*) FROM documents WHERE materia IS NOT NULL "
                "GROUP BY materia ORDER BY COUNT(*) DESC LIMIT 15"
            ).fetchall()
        ],
        "search_layers": {
            "wiki": _wiki_available,
            "wiki_pages": len(_wiki_index),
            "qmd": _qmd_available,
            "fts": True,
            "web": True,
        },
    }
    conn.close()
    return stats


# ---------------------------------------------------------------------------
# LLM call (async, via httpx)
# ---------------------------------------------------------------------------


async def call_llm(system: str, user_msg: str) -> str | None:
    """Call LLM (Anthropic primary, Kimi fallback). Returns answer string or None."""
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0))

    answer: str | None = None

    # Try Anthropic first
    if ANTHROPIC_API_KEY:
        try:
            resp = await _http_client.post(
                "https://api.anthropic.com/v1/messages",
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": 3000,
                    "system": system,
                    "messages": [{"role": "user", "content": user_msg}],
                },
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                },
            )
            resp.raise_for_status()
            result = resp.json()
            answer = result["content"][0]["text"]
        except Exception as e:
            print(f"  [llm] Anthropic error: {e}")

    # Fallback to Kimi
    if not answer and KIMI_API_KEY:
        try:
            resp = await _http_client.post(
                KIMI_BASE_URL + "/chat/completions",
                json={
                    "model": "kimi-k2.5",
                    "max_tokens": 4096,
                    "stream": False,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user_msg},
                    ],
                },
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {KIMI_API_KEY}",
                    "User-Agent": "claude-code/1.0",
                },
            )
            resp.raise_for_status()
            result = resp.json()
            msg = result["choices"][0]["message"]
            answer = msg.get("content", "")
            reasoning = msg.get("reasoning_content", "")
            if not answer and reasoning:
                answer = reasoning
            print(
                f"  [llm] Kimi response: content={len(answer or '')} chars, "
                f"reasoning={len(reasoning)} chars"
            )
        except Exception as e:
            print(f"  [llm] Kimi error: {e}")

    return answer


# ---------------------------------------------------------------------------
# Agent chat (async)
# ---------------------------------------------------------------------------


async def agent_chat(question: str, session_id: str = "") -> dict:
    """Full agent: three-layer search -> context building -> grounded answer."""
    loop = asyncio.get_running_loop()

    # Step 1: Check if it's a direct decree lookup
    decree_match = re.search(
        r'(?:decree|decreto)\s*(?:no\.?|number|#)?\s*(\d+)', question.lower()
    )
    decree_data = None
    if decree_match:
        decree_data = await loop.run_in_executor(
            _db_executor, _get_decree_sync, decree_match.group(1)
        )

    # Step 2: Three-layer search
    search_result = await smart_search(question, limit=12)
    merged = search_result["merged"]
    layers_used = search_result["layers_used"]
    wiki_hit = search_result["wiki_hit"]

    # Step 3: If no results from any layer, try harder with individual words
    if not merged and not decree_data:
        words = re.sub(r'[^\w\s]', ' ', question).split()
        for word in words:
            if len(word) > 3:
                fts_fallback = await loop.run_in_executor(
                    _db_executor, _search_fts_sync, word, 5
                )
                if fts_fallback:
                    for r in fts_fallback:
                        text = r.get("text_en") or r.get("text_es", "")
                        merged.append({
                            "layer": "fts",
                            "title": f"Decreto {r.get('decree_no', '?')}",
                            "decreto": r.get("decree_no", ""),
                            "content": text,
                            "score": 0,
                            "year": r.get("year", ""),
                            "materia": r.get("materia", ""),
                            "articles": r.get("articles", []),
                            "emission_date": r.get("emission_date", ""),
                            "resumen": r.get("resumen", ""),
                        })
                    layers_used.append("fts")
                    break

    # Step 4: Build context for LLM
    context_parts: list[str] = []
    sources: list[dict] = []
    seen_decrees: set[str] = set()

    if decree_data:
        full_text = "\n".join(c["text_es"] for c in decree_data["chunks"])
        if len(full_text) > 6000:
            full_text = full_text[:6000] + "\n[... text truncated ...]"
        label = f"Decreto {decree_data['decree_no']} ({decree_data['year']})"
        if decree_data.get("materia"):
            label += f" -- {decree_data['materia']}"
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
            "layer": "db",
        })
        seen_decrees.add(decree_data["decree_no"])

    for item in merged:
        if len(context_parts) >= 10:
            break

        decreto = item.get("decreto", "")
        if decreto and decreto in seen_decrees:
            continue
        if decreto:
            seen_decrees.add(decreto)

        content = item.get("content", "")
        layer = item.get("layer", "fts")
        title = item.get("title", "Legal text")

        item_status = item.get("status", "unknown")
        status_tag = ""
        if item_status == "repealed":
            repealed_by = item.get("repealed_by", "")
            status_tag = " (REPEALED" + (f" by Decreto {repealed_by}" if repealed_by else "") + ")"
        elif item_status == "active":
            status_tag = " (ACTIVE)"

        # Web results get a special disclaimer label
        if layer == "web":
            web_url = item.get("url", "")
            label = f"Source {len(sources) + 1} [WEB]: {title}"
            if web_url:
                label += f" — {web_url}"
            context_parts.append(
                f"[{label}]\n"
                f"Web search result (not from our database — verify independently):\n"
                f"{content}\n"
            )
            sources.append({
                "index": len(sources) + 1,
                "decree_no": "",
                "year": "",
                "source": title,
                "materia": "",
                "articles": None,
                "emission_date": "",
                "status": "external",
                "repealed_by": "",
                "type": "web_result",
                "layer": "web",
                "url": web_url,
            })
            continue

        label = f"Source {len(sources) + 1} [{layer.upper()}]: "
        if decreto:
            label += f"Decreto {decreto}"
        else:
            label += title
        year = item.get("year", "?")
        label += f" ({year}){status_tag}"
        if item.get("materia"):
            label += f" -- {item['materia']}"
        articles = item.get("articles", [])
        if articles:
            label += f", Articles: {', '.join(articles)}"

        context_parts.append(f"[{label}]\n{content}\n")
        sources.append({
            "index": len(sources) + 1,
            "decree_no": decreto,
            "year": year,
            "source": title,
            "materia": item.get("materia", ""),
            "articles": articles if articles else None,
            "emission_date": item.get("emission_date", ""),
            "status": item_status,
            "repealed_by": item.get("repealed_by", ""),
            "type": "search_result",
            "layer": layer,
        })

    if not context_parts:
        return {
            "answer": (
                "I couldn't find any relevant legal texts for your question. "
                "Try rephrasing with specific legal terms, or ask in Spanish for better results. "
                "Our database covers 8,200+ documents with 1,025 decrees from 1933-2026."
            ),
            "sources": [],
            "queries_tried": expand_query(question),
            "layers_used": layers_used,
        }

    # Step 5: Context limiting — generous for Anthropic, strict for Kimi
    if ANTHROPIC_API_KEY:
        MAX_SOURCES = 10
        MAX_PER_SOURCE = 8000
        MAX_TOTAL = 40000
    else:
        MAX_SOURCES = 3
        MAX_PER_SOURCE = 1200
        MAX_TOTAL = 4000

    context_parts_limited: list[str] = []
    total_chars = 0
    for part in context_parts[:MAX_SOURCES]:
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

    source_note = ""
    if wiki_hit:
        source_note = " Source 1 is from the pre-compiled legal wiki (highest quality)."

    # Prepend conversation history for follow-up context
    history_context = format_history_context(session_id) if session_id else ""
    history_block = f"{history_context}\n\n" if history_context else ""

    user_msg = f"""{history_block}Current question: {question}

Legal texts found (Source 1 = most relevant).{source_note}

{context}

Answer the question using these sources. Cite decreto numbers. Be direct and concise."""

    # Step 6: Call LLM
    answer = await call_llm(SYSTEM_PROMPT, user_msg)

    if not answer:
        answer = "**Search Results** (LLM unavailable for synthesis)\n\n"
        for s in sources:
            dn = s.get("decree_no")
            label = f"Decreto {dn}" if dn else (s.get("source") or "Legal text")
            materia = s.get("materia") or ""
            layer_tag = f"[{s.get('layer', '?')}]"
            answer += f"- {layer_tag} **{label}** ({s.get('year', '?')})"
            if materia:
                answer += f" -- {materia}"
            answer += "\n"

    # Build top_results for transparency
    top_results: list[dict] = []
    seen: set[str] = set()
    for item in merged[:8]:
        dn = item.get("decreto", "")
        if not dn or dn in seen:
            continue
        seen.add(dn)
        top_results.append({
            "decree_no": dn,
            "year": item.get("year", "?"),
            "materia": item.get("materia", ""),
            "resumen": (item.get("resumen") or "")[:150],
            "layer": item.get("layer", "fts"),
        })
        if len(top_results) >= 5:
            break

    return {
        "answer": answer,
        "sources": sources,
        "top_results": top_results,
        "layers_used": layers_used,
        "wiki_hit": wiki_hit,
    }


# ---------------------------------------------------------------------------
# HTML Chat UI (copied from serve_v2.py)
# ---------------------------------------------------------------------------

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

  .stats-bar .dot.off {
    background: var(--text-dim);
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

  .layer-tag {
    font-size: 0.6rem;
    font-weight: 600;
    padding: 0.05rem 0.3rem;
    border-radius: 3px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    flex-shrink: 0;
  }
  .layer-tag.wiki { background: rgba(34,197,94,0.15); color: #4ade80; }
  .layer-tag.qmd { background: rgba(59,130,246,0.15); color: #60a5fa; }
  .layer-tag.fts { background: rgba(161,161,170,0.15); color: #a1a1aa; }
  .layer-tag.db { background: rgba(245,158,11,0.15); color: #fbbf24; }

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

  .layers-info {
    display: flex;
    gap: 0.4rem;
    margin-top: 0.5rem;
    flex-wrap: wrap;
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
    <span class="badge">v2</span>
  </div>
  <div class="stats-bar" id="stats-bar">
    <span class="stat"><span class="dot"></span> Loading...</span>
  </div>
</header>
<main>
  <div id="messages">
    <div class="welcome">
      <h2>Search Salvadoran Law</h2>
      <p>Find and understand laws from El Salvador's official legal database. Get clear answers with decreto citations.</p>
      <p style="font-size:0.78rem; color:var(--text-dim)">Ask in English or Spanish &bull; 8,200+ official documents &bull; Not legal advice &mdash; consult an attorney for your specific case.</p>
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
let sessionId = null;

fetch('/api/stats')
  .then(r => r.json())
  .then(s => {
    const layers = s.search_layers || {};
    let layerHtml = '';
    if (layers.wiki) layerHtml += '<span class="stat"><span class="dot"></span> Wiki (' + layers.wiki_pages + ')</span>';
    else layerHtml += '<span class="stat"><span class="dot off"></span> Wiki off</span>';
    if (layers.qmd) layerHtml += '<span class="stat"><span class="dot"></span> QMD</span>';
    else layerHtml += '<span class="stat"><span class="dot off"></span> QMD off</span>';
    layerHtml += '<span class="stat"><span class="dot"></span> FTS5</span>';
    if (layers.web) layerHtml += '<span class="stat"><span class="dot"></span> Web</span>';
    layerHtml += '<span class="stat">' + (s.decrees || s.documents) + ' decrees</span>';
    document.getElementById('stats-bar').innerHTML = layerHtml;
  })
  .catch(() => {
    document.getElementById('stats-bar').innerHTML = '<span class="stat">Offline</span>';
  });

questionEl.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendQuestion(); }
});

function askExample(btn) { questionEl.value = btn.textContent; sendQuestion(); }

function toggleSources(btn, count) {
  var list = btn.nextElementSibling;
  list.classList.toggle("open");
  btn.textContent = list.classList.contains("open") ? "Hide sources" : "Show " + count + " sources";
}

function escapeHtml(text) {
  if (typeof text !== 'string') return '';
  var div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

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
  thinkDiv.innerHTML = '<div class="spinner"></div> Searching legal database (wiki + semantic + keyword)...';
  messagesEl.appendChild(thinkDiv);

  questionEl.value = '';
  sendBtn.disabled = true;
  messagesEl.scrollTop = messagesEl.scrollHeight;

  try {
    const payload = { question: q };
    if (sessionId) payload.session_id = sessionId;
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (data.session_id) sessionId = data.session_id;
    thinkDiv.remove();

    const assistDiv = document.createElement('div');
    assistDiv.className = 'message assistant';

    let html = '<p>' + renderMarkdown(data.answer) + '</p>';

    // Show layers used
    if (data.layers_used && data.layers_used.length > 0) {
      html += '<div class="layers-info">';
      for (const layer of data.layers_used) {
        html += '<span class="layer-tag ' + escapeHtml(layer) + '">' + escapeHtml(layer) + '</span>';
      }
      if (data.wiki_hit) html += '<span class="layer-tag wiki" style="border:1px solid #4ade80;">wiki hit</span>';
      html += '</div>';
    }

    // Show top search results for transparency
    if (data.top_results && data.top_results.length > 0) {
      html += '<div style="margin-top:0.75rem;padding:0.6rem 0.8rem;background:var(--surface2);border-radius:8px;font-size:0.78rem;">';
      html += '<div style="color:var(--accent-hover);font-weight:600;margin-bottom:0.4rem;font-size:0.7rem;text-transform:uppercase;letter-spacing:0.05em;">Top matches from database</div>';
      for (const r of data.top_results) {
        if (!r.decree_no || r.decree_no === '?') continue;
        const layerTag = r.layer ? ' <span class="layer-tag ' + escapeHtml(r.layer) + '" style="font-size:0.55rem;">' + escapeHtml(r.layer) + '</span>' : '';
        html += '<div style="color:var(--text-secondary);padding:0.15rem 0;">Decreto ' + escapeHtml(r.decree_no) + ' (' + escapeHtml(r.year || '?') + ')' + layerTag;
        if (r.materia) html += ' &mdash; ' + escapeHtml(r.materia);
        if (r.resumen) html += '<br><span style="color:var(--text-dim);font-size:0.72rem;">' + escapeHtml(r.resumen) + '</span>';
        html += '</div>';
      }
      html += '</div>';
    }

    if (data.sources && data.sources.length > 0) {
      html += '<div class="sources-section">';
      html += '<button class="sources-toggle" onclick="toggleSources(this,' + data.sources.length + ')">Show ' + data.sources.length + ' sources</button>';
      html += '<div class="sources-list">';
      for (const s of data.sources) {
        let label = '';
        if (s.decree_no) label += 'Decreto ' + escapeHtml(s.decree_no);
        else label += escapeHtml(s.source || 'Legal text');
        label += ' (' + escapeHtml(s.year || '?') + ')';
        const layerClass = escapeHtml(s.layer || 'fts');
        let meta = '';
        if (s.materia) meta += escapeHtml(s.materia);
        if (s.rama) meta += (meta ? ' / ' : '') + escapeHtml(s.rama);
        if (s.emission_date) meta += (meta ? ' &mdash; ' : '') + 'Issued ' + escapeHtml(s.emission_date);
        html += '<div class="source-item"><span class="source-num">' + s.index + '</span><span class="layer-tag ' + layerClass + '">' + layerClass + '</span><div><div>' + label + '</div>' + (meta ? '<div class="source-meta">' + meta + '</div>' : '') + '</div></div>';
      }
      html += '</div></div>';
    }

    assistDiv.innerHTML = html;
    messagesEl.appendChild(assistDiv);
  } catch (err) {
    thinkDiv.remove();
    const errDiv = document.createElement('div');
    errDiv.className = 'message assistant';
    errDiv.innerHTML = '<p>Connection error. Is the server running?</p>';
    messagesEl.appendChild(errDiv);
  }

  sendBtn.disabled = false;
  messagesEl.scrollTop = messagesEl.scrollHeight;
  questionEl.focus();
}
</script>
</body>
</html>"""

# ---------------------------------------------------------------------------
# OpenAPI spec
# ---------------------------------------------------------------------------


def _build_openapi_spec() -> dict:
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "El Salvador Law Agent API v2",
            "description": (
                "Three-layer search (Wiki + QMD + FTS5) over the complete corpus "
                "of El Salvador laws, decrees, and legal texts. Covers decrees from "
                "the Asamblea Legislativa (1860-2026)."
            ),
            "version": "2.0.0",
        },
        "servers": [{"url": f"http://localhost:{PORT}"}],
        "paths": {
            "/api/search": {
                "get": {
                    "operationId": "searchLaws",
                    "summary": "Full-text search over El Salvador legal corpus",
                    "description": (
                        "Search for legal text in Spanish or English. "
                        "Returns matching chunks with decree metadata."
                    ),
                    "parameters": [
                        {
                            "name": "q",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "string"},
                            "description": "Search query (Spanish or English).",
                        },
                        {
                            "name": "limit",
                            "in": "query",
                            "schema": {"type": "integer", "default": 10},
                            "description": "Max results (1-50)",
                        },
                    ],
                    "responses": {
                        "200": {
                            "description": "Search results with legal text excerpts, decree numbers, and metadata"
                        }
                    },
                }
            },
            "/api/decree/{number}": {
                "get": {
                    "operationId": "getDecree",
                    "summary": "Get full text and metadata for a specific decree",
                    "parameters": [
                        {
                            "name": "number",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                            "description": "Decree number (e.g., 503)",
                        },
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
                        {
                            "name": "materia",
                            "in": "query",
                            "schema": {"type": "string"},
                            "description": "Subject category (e.g., Hacienda, Educacion)",
                        },
                        {
                            "name": "rama",
                            "in": "query",
                            "schema": {"type": "string"},
                            "description": "Branch of law (e.g., Derecho Penal)",
                        },
                        {
                            "name": "year",
                            "in": "query",
                            "schema": {"type": "string"},
                            "description": "Year (e.g., 2025)",
                        },
                        {
                            "name": "limit",
                            "in": "query",
                            "schema": {"type": "integer", "default": 50},
                            "description": "Max results",
                        },
                    ],
                    "responses": {
                        "200": {"description": "List of matching decrees with metadata"}
                    },
                }
            },
            "/api/chat": {
                "post": {
                    "operationId": "askLawQuestion",
                    "summary": "Ask a question about Salvadoran law and get a grounded answer",
                    "description": (
                        "Three-layer RAG agent: Wiki -> QMD -> FTS5. "
                        "Deduplicates, merges, synthesizes a grounded answer with citations."
                    ),
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "question": {
                                            "type": "string",
                                            "description": "Question about Salvadoran law",
                                        },
                                        "session_id": {
                                            "type": "string",
                                            "description": "Optional session ID for conversation continuity. Generated automatically on first request if omitted.",
                                        },
                                    },
                                    "required": ["question"],
                                }
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "Grounded answer with decree citations and layer metadata"
                        }
                    },
                }
            },
            "/api/stats": {
                "get": {
                    "operationId": "getCorpusStats",
                    "summary": "Get corpus statistics",
                    "responses": {
                        "200": {
                            "description": "Corpus statistics including search layer availability"
                        }
                    },
                }
            },
            "/healthz": {
                "get": {
                    "operationId": "healthCheck",
                    "summary": "Health check endpoint",
                    "responses": {
                        "200": {"description": "Server health status"}
                    },
                }
            },
        },
    }


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="El Salvador Law Agent API",
    version="2.0.0",
    docs_url=None,
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGIN.split(",") if CORS_ORIGIN != "*" else ["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if request.method == "POST":
            content_length = request.headers.get("content-length")
            if content_length and int(content_length) > MAX_REQUEST_BYTES:
                return JSONResponse(status_code=413, content={"error": "Request too large"})
        return await call_next(request)


app.add_middleware(RequestSizeLimitMiddleware)


# -- Lifecycle events -------------------------------------------------------

@app.on_event("startup")
async def startup_event() -> None:
    global _http_client
    print()
    print("Initializing search layers...")

    # Wiki init (sync, fast)
    init_wiki()

    # QMD init (async)
    await init_qmd()

    # DB verification
    verify_db()

    # Create shared httpx client
    _http_client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0))

    llm = "Anthropic" if ANTHROPIC_API_KEY else ("Kimi K2.5" if KIMI_API_KEY else "NONE")
    print()
    print("=" * 56)
    print("  El Salvador Law Agent v2 (FastAPI)")
    print("=" * 56)
    print(f"  Database:  {DB_PATH}")
    print(f"  Wiki:      {WIKI_DIR} ({'ON -- ' + str(len(_wiki_index)) + ' pages' if _wiki_available else 'OFF'})")
    print(f"  QMD:       {QMD_COLLECTION} ({'ON' if _qmd_available else 'OFF'})")
    print(f"  FTS5:      ON (always available)")
    print(f"  LLM:       {llm}")
    print(f"  Server:    http://localhost:{PORT}")
    print(f"  Health:    http://localhost:{PORT}/healthz")
    print(f"  API docs:  http://localhost:{PORT}/openapi.json")
    print("=" * 56)
    print()


@app.on_event("shutdown")
async def shutdown_event() -> None:
    global _http_client
    if _http_client:
        await _http_client.aclose()
        _http_client = None
    _db_executor.shutdown(wait=False)
    print("Server shut down cleanly.")


# -- Helper to clamp limit --------------------------------------------------

def _parse_limit(raw: str | None, default: int, maximum: int) -> int:
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid 'limit': must be an integer")
    if value < 1:
        raise HTTPException(status_code=400, detail="Invalid 'limit': must be >= 1")
    return min(value, maximum)


# -- Routes ------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse(content=CHAT_HTML)


@app.get("/api/search")
async def api_search(
    q: str = Query(default="", description="Search query"),
    limit: int = Query(default=10, ge=1, le=50, description="Max results"),
) -> JSONResponse:
    if not q:
        raise HTTPException(status_code=400, detail="Missing query parameter 'q'")
    result = await smart_search(q, limit)
    return JSONResponse(content={
        "query": q,
        "results": result["merged"],
        "count": len(result["merged"]),
        "layers_used": result["layers_used"],
    })


@app.get("/api/decree/{number}")
async def api_decree(number: str) -> JSONResponse:
    loop = asyncio.get_running_loop()
    data = await loop.run_in_executor(_db_executor, _get_decree_sync, number)
    if data:
        return JSONResponse(content=data)
    raise HTTPException(status_code=404, detail=f"Decree {number} not found")


@app.get("/api/browse")
async def api_browse(
    materia: str | None = Query(default=None),
    rama: str | None = Query(default=None),
    year: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> JSONResponse:
    loop = asyncio.get_running_loop()
    results = await loop.run_in_executor(
        _db_executor, _browse_laws_sync, materia, rama, year, limit
    )
    return JSONResponse(content={"results": results, "count": len(results)})


@app.post("/api/chat")
async def api_chat(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid JSON"})

    if not isinstance(body, dict):
        return JSONResponse(status_code=400, content={"error": "Expected JSON object"})

    question = body.get("question")
    if not isinstance(question, str) or not question.strip():
        return JSONResponse(status_code=400, content={"error": "Missing or invalid 'question' field"})

    question = question.strip()[:2000]

    # Session management: reuse existing or generate new
    session_id = (body.get("session_id") or "").strip()
    if not session_id:
        session_id = uuid.uuid4().hex[:16]

    response = await agent_chat(question, session_id=session_id)

    # Save exchange to session history
    add_to_history(session_id, question, response.get("answer", ""))

    response["session_id"] = session_id
    return JSONResponse(content=response)


@app.get("/api/stats")
async def api_stats() -> JSONResponse:
    loop = asyncio.get_running_loop()
    stats = await loop.run_in_executor(_db_executor, _get_stats_sync)
    return JSONResponse(content=stats)


@app.get("/openapi.json")
async def openapi_spec() -> JSONResponse:
    return JSONResponse(content=_build_openapi_spec())


@app.get("/healthz")
async def healthz() -> JSONResponse:
    loop = asyncio.get_running_loop()

    # Quick DB check
    db_status = False
    db_stats: dict[str, Any] = {}
    try:

        def _quick_db_check() -> tuple[bool, dict]:
            conn = _get_db()
            docs = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            conn.close()
            return True, {"documents": docs, "chunks": chunks}

        db_status, db_stats = await loop.run_in_executor(_db_executor, _quick_db_check)
    except Exception:
        db_status = False

    health_content = {
        "status": "ok" if db_status else "degraded",
        "llm": {
            "anthropic": bool(ANTHROPIC_API_KEY),
            "kimi": bool(KIMI_API_KEY),
        },
        "search_layers": {
            "wiki": _wiki_available,
            "wiki_pages": len(_wiki_index),
            "qmd": _qmd_available,
            "fts": db_status,
            "web": True,
        },
        "db": {
            "connected": db_status,
            **db_stats,
        },
    }

    if not db_status:
        return JSONResponse(status_code=503, content=health_content)

    return JSONResponse(content=health_content)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    global DB_PATH, WIKI_DIR, PORT

    import argparse

    parser = argparse.ArgumentParser(description="El Salvador Law Agent -- FastAPI server")
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--wiki", default=str(WIKI_DIR))
    args = parser.parse_args()

    DB_PATH = Path(args.db)
    WIKI_DIR = Path(args.wiki)
    PORT = args.port

    if not DB_PATH.exists():
        print(f"Database not found: {DB_PATH}")
        print("Run build-search-db.py first.")
        sys.exit(1)

    # Handle SIGINT gracefully
    def _handle_sigint(sig: int, frame: Any) -> None:
        print("\nShutting down...")
        sys.exit(0)

    signal.signal(signal.SIGINT, _handle_sigint)

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level="info",
        access_log=True,
    )


if __name__ == "__main__":
    main()
