/**
 * URL and page classifier for El Salvador legal sources.
 *
 * Classifies discovered URLs into document_type categories and
 * extracts metadata where possible from URL patterns and page content.
 */

/**
 * Classify a URL based on its pattern.
 * @param {string} url
 * @returns {{ document_type: string, format: string, likely_needs_ocr: boolean }}
 */
export function classifyUrl(url) {
  const u = url.toLowerCase();

  // Direct PDF
  if (u.endsWith('.pdf') || u.includes('.pdf?') || u.includes('/pdf/')) {
    return { document_type: 'direct-pdf', format: 'pdf', likely_needs_ocr: true };
  }

  // Diario Oficial patterns
  if (u.includes('diariooficial.gob.sv')) {
    if (u.includes('/seleccion/')) return { document_type: 'gazette-issue-page', format: 'html', likely_needs_ocr: false };
    if (u.includes('/diarios/')) return { document_type: 'gazette-issue-page', format: 'html', likely_needs_ocr: false };
    if (/\d{4}/.test(u) && !u.includes('buscar')) return { document_type: 'gazette-browse', format: 'html', likely_needs_ocr: false };
    return { document_type: 'source-index', format: 'html', likely_needs_ocr: false };
  }

  // Asamblea patterns
  if (u.includes('asamblea.gob.sv')) {
    if (u.includes('busqueda-decretos')) return { document_type: 'search-page', format: 'html', likely_needs_ocr: false };
    if (u.includes('decretos-por-anios')) return { document_type: 'source-index', format: 'html', likely_needs_ocr: false };
    if (u.includes('anuarios-legislativos')) return { document_type: 'source-index', format: 'html', likely_needs_ocr: false };
    if (u.includes('/decreto') || u.includes('/ley')) return { document_type: 'decree-page', format: 'html', likely_needs_ocr: false };
    if (u.includes('/node/') || u.includes('/content/')) return { document_type: 'law-record-page', format: 'html', likely_needs_ocr: false };
    return { document_type: 'source-index', format: 'html', likely_needs_ocr: false };
  }

  // Jurisprudencia patterns
  if (u.includes('jurisprudencia.gob.sv')) {
    if (u.includes('/documento/') || u.includes('/doc/')) return { document_type: 'law-record-page', format: 'html', likely_needs_ocr: false };
    if (u.includes('/busqueda') || u.includes('/search')) return { document_type: 'search-page', format: 'html', likely_needs_ocr: false };
    return { document_type: 'source-index', format: 'html', likely_needs_ocr: false };
  }

  // Asamblea library
  if (u.includes('biblioteca.asamblea.gob.sv')) {
    if (u.includes('/record/') || u.includes('/items/')) return { document_type: 'law-record-page', format: 'html', likely_needs_ocr: false };
    if (u.includes('/search') || u.includes('/catalog')) return { document_type: 'search-page', format: 'html', likely_needs_ocr: false };
    return { document_type: 'source-index', format: 'html', likely_needs_ocr: false };
  }

  // Generic
  if (u.endsWith('.doc') || u.endsWith('.docx')) return { document_type: 'document', format: 'doc', likely_needs_ocr: false };
  if (u.endsWith('.html') || u.endsWith('.htm')) return { document_type: 'html-document', format: 'html', likely_needs_ocr: false };

  return { document_type: 'unknown', format: 'unknown', likely_needs_ocr: false };
}

/**
 * Try to extract decree number from text or URL.
 * @param {string} text
 * @returns {string|null}
 */
export function extractDecreeNo(text) {
  if (!text) return null;
  // Patterns: "Decreto No. 123", "Decreto N° 123", "Decreto 123", "D.L. No. 123"
  const m = text.match(/(?:decreto|d\.?\s*l\.?)\s*(?:no?\.?°?\s*)(\d+)/i);
  return m ? m[1] : null;
}

/**
 * Try to extract Diario Oficial number from text.
 * @param {string} text
 * @returns {string|null}
 */
export function extractDiarioNo(text) {
  if (!text) return null;
  const m = text.match(/(?:diario\s+oficial|d\.?\s*o\.?)\s*(?:no?\.?°?\s*)(\d+)/i);
  return m ? m[1] : null;
}

/**
 * Try to extract tomo from text.
 * @param {string} text
 * @returns {string|null}
 */
export function extractTomo(text) {
  if (!text) return null;
  const m = text.match(/tomo\s*(?:no?\.?°?\s*)(\d+)/i);
  return m ? m[1] : null;
}

/**
 * Try to extract a year from text or URL.
 * @param {string} text
 * @returns {string|null}
 */
export function extractYear(text) {
  if (!text) return null;
  const m = text.match(/((?:18|19|20)\d{2})/);
  return m ? m[1] : null;
}

/**
 * Build an inventory item from discovered info.
 */
export function buildItem({ url, parentUrl, title, text, needsBrowser = false, extra = {} }) {
  const classification = classifyUrl(url);
  const combined = [title, text, url].filter(Boolean).join(' ');

  return {
    discovered_url: url,
    parent_url: parentUrl || null,
    title: title || null,
    document_type: classification.document_type,
    format: classification.format,
    decree_no: extractDecreeNo(combined),
    diario_oficial_no: extractDiarioNo(combined),
    tomo: extractTomo(combined),
    year: extractYear(combined),
    needs_browser: needsBrowser,
    likely_needs_ocr: classification.likely_needs_ocr,
    status: 'discovered',
    ...extra,
  };
}
