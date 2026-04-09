/**
 * Crawler: Asamblea Legislativa - Decretos por Ano (EXHAUSTIVE)
 *
 * Key discovery: {BASE}/decretos-por-anios/{YEAR}/0 returns ALL decrees
 * for a given year on a single page (no pagination needed).
 *
 * Strategy:
 * 1. Load the year index to get all available years (1860-2026)
 * 2. For EVERY year, fetch the /0 page to get all decrees
 * 3. Extract decree IDs and metadata from each card
 * 4. For each decree, fetch the detail page for full metadata + PDF link
 *
 * URL patterns:
 *   Year page: /leyes-y-decretos/decretos-por-anios/{YEAR}/0
 *   Detail:    /leyes-y-decretos/view/{ID}
 *   PDF:       /sites/default/files/documents/decretos/{GUID}.pdf
 */

import { newPage } from '../lib/browser.mjs';
import { CrawlState } from '../lib/state.mjs';
import { buildItem } from '../lib/classify.mjs';
import { createThrottle, withRetry, sleep } from '../lib/rate-limit.mjs';

const BASE_URL = 'https://www.asamblea.gob.sv/leyes-y-decretos/decretos-por-anios';

export async function crawl(dataDir, options = {}) {
  const state = new CrawlState('asamblea-year-archive', dataDir);
  await state.load();

  const throttle = createThrottle(3000); // 3s between requests for listing pages
  const detailThrottle = createThrottle(1500); // 1.5s for detail pages (lighter)
  const page = await newPage();

  // Allow limiting for testing
  const maxYears = options.maxYears || Infinity;
  const startYear = options.startYear || null; // e.g. 2020 to start from recent
  const fetchDetails = options.fetchDetails !== false; // fetch individual decree pages

  try {
    // Step 1: Get all available years
    console.log('[asamblea-years] Loading year index...');
    await throttle();
    await withRetry(() => page.goto(BASE_URL, { waitUntil: 'networkidle' }));
    await sleep(3000);

    // Record the index page
    if (!state.hasSeen(BASE_URL)) {
      await state.record(buildItem({
        url: BASE_URL,
        parentUrl: null,
        title: 'Asamblea Legislativa - Decretos por Ano (index)',
        needsBrowser: true,
        extra: { document_type: 'source-index' },
      }));
    }

    // Extract all year values
    const years = await page.evaluate(() => {
      const items = [];
      document.querySelectorAll('a[href]').forEach(a => {
        const text = a.textContent.trim();
        if (/^(18|19|20)\d{2}$/.test(text) && a.href.includes('asamblea.gob.sv')) {
          items.push(parseInt(text));
        }
      });
      // Deduplicate and sort descending
      return [...new Set(items)].sort((a, b) => b - a);
    });

    console.log(`[asamblea-years] Found ${years.length} years (${years[years.length - 1]}-${years[0]})`);

    // Filter by startYear if specified
    let targetYears = years;
    if (startYear) {
      targetYears = years.filter(y => y >= startYear);
      console.log(`[asamblea-years] Filtered to ${targetYears.length} years (>= ${startYear})`);
    }
    targetYears = targetYears.slice(0, maxYears);

    let totalDecrees = 0;
    let totalNew = 0;

    // Step 2: For each year, fetch ALL decrees
    for (const year of targetYears) {
      const yearUrl = `${BASE_URL}/${year}/0`;
      console.log(`\n  [year ${year}] ${yearUrl}`);

      // Record the year page
      if (!state.hasSeen(yearUrl)) {
        await state.record(buildItem({
          url: yearUrl,
          parentUrl: BASE_URL,
          title: `Decretos ${year} (all)`,
          needsBrowser: true,
          extra: { document_type: 'source-index', year: String(year) },
        }));
      }

      await throttle();

      try {
        await withRetry(() => page.goto(yearUrl, { waitUntil: 'networkidle' }));
        await sleep(2000);

        // Extract all decree cards
        const decrees = await page.evaluate(() => {
          const items = [];
          // Cards have data-al-modal-url attributes pointing to /leyes-y-decretos/view/{ID}
          document.querySelectorAll('[data-al-modal-url]').forEach(el => {
            const url = el.getAttribute('data-al-modal-url');
            if (!url) return;

            // Extract decree number from card header
            const header = el.querySelector('.card-header, .card-title, h5, h4');
            const headerText = header ? header.textContent.trim() : '';

            // Extract title from card body
            const body = el.querySelector('.card-body, .card-text, p');
            const bodyText = body ? body.textContent.trim() : '';

            // Extract date from card footer
            const footer = el.querySelector('.card-footer, small');
            const footerText = footer ? footer.textContent.trim() : '';

            items.push({
              url: url.startsWith('http') ? url : 'https://www.asamblea.gob.sv' + url,
              headerText,
              bodyText: bodyText.substring(0, 500),
              footerText,
            });
          });

          // Fallback: look for links to /leyes-y-decretos/view/
          if (items.length === 0) {
            document.querySelectorAll('a[href*="/leyes-y-decretos/view/"]').forEach(a => {
              items.push({
                url: a.href,
                headerText: '',
                bodyText: a.textContent.trim().substring(0, 500),
                footerText: '',
              });
            });
          }

          return items;
        });

        console.log(`    Found ${decrees.length} decrees`);
        totalDecrees += decrees.length;

        // Record each decree
        for (const d of decrees) {
          if (state.hasSeen(d.url)) continue;

          // Parse decree number from header
          const decreeNoMatch = d.headerText.match(/(?:No\.?\s*)?(?:Decreto\s*)?(\d+)/i);
          const decreeNo = decreeNoMatch ? decreeNoMatch[1] : null;

          // Parse emission date from footer
          const dateMatch = d.footerText.match(/(\d{2}\/\d{2}\/\d{4})/);
          const emissionDate = dateMatch ? dateMatch[1] : null;

          // Extract internal ID from URL
          const idMatch = d.url.match(/\/view\/(\d+)/);
          const internalId = idMatch ? idMatch[1] : null;

          await state.record(buildItem({
            url: d.url,
            parentUrl: yearUrl,
            title: d.bodyText || d.headerText,
            needsBrowser: true,
            extra: {
              document_type: 'decree-page',
              format: 'html',
              year: String(year),
              decree_no: decreeNo,
              emission_date: emissionDate,
              internal_id: internalId,
              header_text: d.headerText,
            },
          }));
          totalNew++;
        }

        // Step 3: Optionally fetch detail pages for full metadata + PDF links
        if (fetchDetails) {
          const unseenDecrees = decrees.filter(d => {
            // Check if we already have the detail version
            return !state.hasSeen(d.url + '#detail');
          });

          // Limit detail fetches per year to avoid overloading
          const detailLimit = options.detailsPerYear || 50;
          const toFetch = unseenDecrees.slice(0, detailLimit);

          if (toFetch.length > 0) {
            console.log(`    Fetching ${toFetch.length} decree details...`);
          }

          for (let i = 0; i < toFetch.length; i++) {
            const d = toFetch[i];
            await detailThrottle();

            try {
              await withRetry(() => page.goto(d.url, { waitUntil: 'domcontentloaded' }));
              await sleep(800);

              // Extract full metadata using exact CSS selectors for the Drupal table layout
              const detail = await page.evaluate(() => {
                const q = (sel) => {
                  const el = document.querySelector(sel);
                  return el ? el.textContent.trim() : null;
                };

                return {
                  title: q('#asamblea-contenido h1.page-title span.field--name-title') ||
                         q('#asamblea-contenido h1') ||
                         q('h1.page-title'),
                  // Datos del Decreto table (table.mb-0)
                  emissionDate: q('#block-asamblea-content table.mb-0 tbody tr:nth-child(1) td:nth-child(2)'),
                  materia: q('#block-asamblea-content table.mb-0 tbody tr:nth-child(1) td:nth-child(4)'),
                  numeroDecreto: q('#block-asamblea-content table.mb-0 tbody tr:nth-child(2) td:nth-child(2)'),
                  subMateria: q('#block-asamblea-content table.mb-0 tbody tr:nth-child(2) td:nth-child(4)'),
                  rama: q('#block-asamblea-content table.mb-0 tbody tr:nth-child(3) td:last-child'),
                  // Resumen
                  resumen: q('#block-asamblea-content .card-body p.text-justify small') ||
                           q('#block-asamblea-content .card-body p.text-justify') ||
                           q('#block-asamblea-content .card-body p'),
                  // Datos de Publicacion table (second table, without .mb-0)
                  diarioNo: q('#block-asamblea-content table:not(.mb-0) tbody td:nth-child(1)'),
                  tomo: q('#block-asamblea-content table:not(.mb-0) tbody td:nth-child(2)'),
                  publicationDate: q('#block-asamblea-content table:not(.mb-0) tbody td:nth-child(3)'),
                  // PDF link
                  pdfUrl: (() => {
                    const a = document.querySelector('#asamblea-contenido a.btn-blue-al[href*=".pdf"]') ||
                              document.querySelector('a[href*=".pdf"]');
                    return a ? a.href : null;
                  })(),
                };
              });

              // Use decree number from detail page, fall back to card header
              const decreeNo = detail.numeroDecreto ||
                d.headerText.match(/(\d+)/)?.[1] || null;

              // Record PDF if found
              if (detail.pdfUrl && !state.hasSeen(detail.pdfUrl)) {
                await state.record(buildItem({
                  url: detail.pdfUrl,
                  parentUrl: d.url,
                  title: detail.title || d.bodyText,
                  needsBrowser: false,
                  extra: {
                    document_type: 'direct-pdf',
                    format: 'pdf',
                    year: String(year),
                    decree_no: decreeNo,
                    diario_oficial_no: detail.diarioNo,
                    tomo: detail.tomo,
                    publication_date: detail.publicationDate,
                    emission_date: detail.emissionDate,
                    materia: detail.materia,
                    rama: detail.rama,
                    resumen: detail.resumen,
                  },
                }));
                totalNew++;
              }

              // Mark detail as fetched
              await state.record(buildItem({
                url: d.url + '#detail',
                parentUrl: d.url,
                title: `[detail] ${detail.title || d.bodyText}`,
                needsBrowser: false,
                extra: {
                  document_type: 'decree-detail',
                  format: 'html',
                  year: String(year),
                  decree_no: decreeNo,
                  pdf_url: detail.pdfUrl,
                  diario_oficial_no: detail.diarioNo,
                  tomo: detail.tomo,
                  publication_date: detail.publicationDate,
                  emission_date: detail.emissionDate,
                  materia: detail.materia,
                  sub_materia: detail.subMateria,
                  rama: detail.rama,
                  resumen: detail.resumen,
                },
              }));

              if ((i + 1) % 10 === 0) {
                console.log(`      ${i + 1}/${toFetch.length} details fetched`);
              }

            } catch (err) {
              console.log(`      [error] Detail ${d.url}: ${err.message}`);
            }
          }
        }

      } catch (err) {
        console.log(`    [error] Year ${year}: ${err.message}`);
      }
    }

    console.log(`\n  TOTAL: ${totalDecrees} decrees found across ${targetYears.length} years, ${totalNew} new items recorded`);

  } finally {
    await page.context().close();
  }

  return state.stats();
}
