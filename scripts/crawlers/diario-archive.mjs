/**
 * Crawler: Diario Oficial public archive
 *
 * Uses the API directly (no browser needed):
 *   POST /api/v1/diarios-disponibles { year, month }
 *   -> Array of { Id, FechaInicio, FechaInexacta, NombreArchivo }
 *   Download URL: /seleccion/{Id} (returns PDF directly)
 *
 * Strategy:
 * 1. For each target year, iterate months 1-12
 * 2. For each month, call the API to get issue list
 * 3. Record each issue with its date and filename
 */

import { CrawlState } from '../lib/state.mjs';
import { buildItem } from '../lib/classify.mjs';
import { createThrottle, withRetry } from '../lib/rate-limit.mjs';

const API_BASE = 'https://www.diariooficial.gob.sv/api/v1';
const SITE_BASE = 'https://www.diariooficial.gob.sv';

async function fetchIssues(year, month) {
  const res = await fetch(`${API_BASE}/diarios-disponibles`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: `year=${year}&month=${month}`,
  });

  if (!res.ok) {
    if (res.status === 400) return []; // No data for this year/month
    throw new Error(`API returned ${res.status}`);
  }

  const data = await res.json();
  return Array.isArray(data) ? data : [];
}

export async function crawl(dataDir, options = {}) {
  const state = new CrawlState('diario-archive', dataDir);
  await state.load();

  const throttle = createThrottle(500); // API calls can be faster than browser

  // Default: recent 5 years + historical samples
  const currentYear = new Date().getFullYear();
  const defaultYears = [];
  for (let y = currentYear; y >= currentYear - 4; y--) defaultYears.push(y);
  defaultYears.push(2015, 2010, 2000, 1990, 1980, 1960, 1900, 1847);

  const targetYears = options.years || defaultYears;

  // Record the site index
  if (!state.hasSeen(SITE_BASE + '/')) {
    await state.record(buildItem({
      url: SITE_BASE + '/',
      parentUrl: null,
      title: 'Diario Oficial - Archivo Publico (index)',
      needsBrowser: false,
      extra: { document_type: 'source-index' },
    }));
  }

  let totalNew = 0;

  for (const year of targetYears) {
    console.log(`\n  [year] ${year}`);
    let yearTotal = 0;

    for (let month = 1; month <= 12; month++) {
      await throttle();

      try {
        const issues = await withRetry(() => fetchIssues(year, month), 2, 1000);

        if (issues.length === 0) continue;

        console.log(`    ${year}-${String(month).padStart(2, '0')}: ${issues.length} issues`);

        for (const issue of issues) {
          const url = `${SITE_BASE}/seleccion/${issue.Id}`;
          if (state.hasSeen(url)) continue;

          await state.record(buildItem({
            url,
            parentUrl: SITE_BASE + '/',
            title: issue.NombreArchivo || `diario-${issue.FechaInicio}`,
            needsBrowser: false,
            extra: {
              document_type: 'gazette-issue-page',
              format: 'pdf',
              likely_needs_ocr: true,
              year: String(year),
              month: String(month).padStart(2, '0'),
              gazette_date: issue.FechaInicio || null,
              filename: issue.NombreArchivo || null,
              diario_id: issue.Id,
              fecha_inexacta: issue.FechaInexacta === '1',
            },
          }));
          totalNew++;
          yearTotal++;
        }
      } catch (err) {
        // Silently skip months that return errors (likely no data)
        if (!err.message.includes('400')) {
          console.log(`    [error] ${year}-${String(month).padStart(2, '0')}: ${err.message}`);
        }
      }
    }

    if (yearTotal > 0) {
      console.log(`    Year ${year} total: ${yearTotal} new issues`);
    }
  }

  console.log(`\n  Total new issues: ${totalNew}`);

  return state.stats();
}
