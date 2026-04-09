/**
 * Crawler: Asamblea Legislativa - Busqueda de Decretos
 *
 * Strategy:
 * 1. Load the search page
 * 2. Discover the search form structure
 * 3. Paginate through results to discover decree/law record URLs
 * 4. Classify and record each discovered item
 *
 * This is the strongest official discovery surface.
 */

import { newPage } from '../lib/browser.mjs';
import { CrawlState } from '../lib/state.mjs';
import { buildItem } from '../lib/classify.mjs';
import { createThrottle, withRetry, sleep } from '../lib/rate-limit.mjs';

const BASE_URL = 'https://www.asamblea.gob.sv/leyes-y-decretos/busqueda-decretos';

export async function crawl(dataDir, options = {}) {
  const state = new CrawlState('asamblea-search', dataDir);
  await state.load();

  const throttle = createThrottle(2500);
  const page = await newPage();
  const maxPages = options.maxPages || 20;

  try {
    console.log('[asamblea-search] Loading search page...');
    await throttle();
    await withRetry(() => page.goto(BASE_URL, { waitUntil: 'networkidle' }));
    await page.waitForTimeout(3000);

    // Record the search page itself
    if (!state.hasSeen(BASE_URL)) {
      await state.record(buildItem({
        url: BASE_URL,
        parentUrl: null,
        title: 'Asamblea Legislativa - Busqueda de Decretos',
        needsBrowser: true,
        extra: { document_type: 'search-page' },
      }));
    }

    // Analyze the search page structure
    const formInfo = await page.evaluate(() => {
      const forms = Array.from(document.querySelectorAll('form'));
      const selects = Array.from(document.querySelectorAll('select')).map(s => ({
        name: s.name || s.id,
        options: Array.from(s.options).slice(0, 10).map(o => ({ value: o.value, text: o.textContent.trim() })),
        totalOptions: s.options.length,
      }));
      const inputs = Array.from(document.querySelectorAll('input[type="text"], input[type="search"]')).map(i => ({
        name: i.name || i.id,
        placeholder: i.placeholder,
      }));
      const buttons = Array.from(document.querySelectorAll('button, input[type="submit"]')).map(b => ({
        text: b.textContent?.trim() || b.value,
        type: b.type,
      }));
      return { formCount: forms.length, selects, inputs, buttons };
    });

    console.log(`[asamblea-search] Form structure: ${formInfo.selects.length} selects, ${formInfo.inputs.length} inputs, ${formInfo.buttons.length} buttons`);

    // Try to trigger a broad search (empty search or browse-all)
    // First, look for a search/submit button
    const searchButton = await page.$('button[type="submit"], input[type="submit"], button:has-text("Buscar"), button:has-text("buscar"), .btn-search, #btnBuscar');

    if (searchButton) {
      console.log('[asamblea-search] Attempting broad search...');
      await throttle();
      await searchButton.click();
      await page.waitForTimeout(5000);
    }

    // Collect results from the current page
    let pageNum = 0;
    let hasMore = true;

    while (hasMore && pageNum < maxPages) {
      const results = await page.evaluate(() => {
        const items = [];
        // Look for result links
        document.querySelectorAll('a[href]').forEach(a => {
          const href = a.href;
          const text = a.textContent.trim();
          if (!text || text.length < 5) return;
          if (/facebook|twitter|instagram|youtube|whatsapp|mailto:/i.test(href)) return;
          // Decree and law record links
          if (/decreto|ley|reforma|codigo|constitucion|node|content/i.test(href) ||
              /decreto\s+n/i.test(text) || /d\.?\s*l\.?\s+n/i.test(text)) {
            items.push({ href, text: text.substring(0, 300) });
          }
        });
        // Also capture any PDF links
        document.querySelectorAll('a[href$=".pdf"], a[href*=".pdf?"]').forEach(a => {
          items.push({ href: a.href, text: (a.textContent.trim() || 'PDF document').substring(0, 300) });
        });
        return items;
      });

      console.log(`  [page ${pageNum}] Found ${results.length} result links`);

      for (const r of results) {
        if (state.hasSeen(r.href)) continue;
        await state.record(buildItem({
          url: r.href,
          parentUrl: page.url(),
          title: r.text,
          needsBrowser: true,
        }));
      }

      // Try to find and click a "next" pagination link
      const nextButton = await page.$('a:has-text("Siguiente"), a:has-text("siguiente"), a:has-text("Next"), .pager-next a, .pagination .next a, a[rel="next"]');

      if (nextButton) {
        console.log('  [pagination] Going to next page...');
        await throttle();
        await nextButton.click();
        await page.waitForTimeout(3000);
        pageNum++;
      } else {
        hasMore = false;
      }
    }

    // If we got no results at all, dump the page structure for debugging
    if (state.count === 1) { // only the index page
      console.log('[asamblea-search] No search results found. Capturing page structure...');
      const pageContent = await page.evaluate(() => ({
        title: document.title,
        url: window.location.href,
        headings: Array.from(document.querySelectorAll('h1,h2,h3')).map(h => h.textContent.trim()),
        allLinks: Array.from(document.querySelectorAll('a[href]')).slice(0, 30).map(a => ({
          href: a.href,
          text: a.textContent.trim().substring(0, 100),
        })),
        bodySnippet: document.body?.innerText?.substring(0, 3000) || '',
      }));
      console.log('  Page info:', JSON.stringify(pageContent, null, 2));
    }

  } finally {
    await page.context().close();
  }

  return state.stats();
}
