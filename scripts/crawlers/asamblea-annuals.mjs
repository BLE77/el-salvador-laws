/**
 * Crawler: Asamblea Legislativa - Anuarios Legislativos
 *
 * Strategy:
 * 1. Load the annuals page
 * 2. Discover links to yearly compilations
 * 3. For each annual, discover individual decree/document links
 */

import { newPage } from '../lib/browser.mjs';
import { CrawlState } from '../lib/state.mjs';
import { buildItem } from '../lib/classify.mjs';
import { createThrottle, withRetry } from '../lib/rate-limit.mjs';

const BASE_URL = 'https://www.asamblea.gob.sv/leyes-y-decretos/anuarios-legislativos';

export async function crawl(dataDir, options = {}) {
  const state = new CrawlState('asamblea-annuals', dataDir);
  await state.load();

  const throttle = createThrottle(2000);
  const page = await newPage();
  const maxAnnuals = options.maxAnnuals || Infinity;

  try {
    console.log('[asamblea-annuals] Loading annuals page...');
    await throttle();
    await withRetry(() => page.goto(BASE_URL, { waitUntil: 'networkidle' }));
    await page.waitForTimeout(3000);

    if (!state.hasSeen(BASE_URL)) {
      await state.record(buildItem({
        url: BASE_URL,
        parentUrl: null,
        title: 'Asamblea Legislativa - Anuarios Legislativos (index)',
        needsBrowser: true,
        extra: { document_type: 'source-index' },
      }));
    }

    // Find annual links - could be direct links or a list of years
    const annualLinks = await page.evaluate(() => {
      const items = [];
      document.querySelectorAll('a[href]').forEach(a => {
        const href = a.href;
        const text = a.textContent.trim();
        if (!text || text.length < 2) return;
        if (/facebook|twitter|instagram|youtube|whatsapp|mailto:/i.test(href)) return;
        // Annual compilation links
        if (/anuario|compilacion|year|año|anio|\b(19|20)\d{2}\b/i.test(text) ||
            /anuario|compilacion/i.test(href) ||
            /\.pdf$/i.test(href)) {
          items.push({ href, text: text.substring(0, 200) });
        }
      });
      return items;
    });

    console.log(`[asamblea-annuals] Found ${annualLinks.length} annual links`);

    let processed = 0;
    for (const al of annualLinks) {
      if (processed >= maxAnnuals) break;
      if (state.hasSeen(al.href)) {
        console.log(`  [skip] Already seen: ${al.text}`);
        continue;
      }

      await state.record(buildItem({
        url: al.href,
        parentUrl: BASE_URL,
        title: al.text,
        needsBrowser: true,
      }));

      // If it's not a PDF, drill into it
      if (!al.href.toLowerCase().endsWith('.pdf')) {
        console.log(`  [annual] Drilling into: ${al.text}`);
        await throttle();

        try {
          await withRetry(() => page.goto(al.href, { waitUntil: 'networkidle' }));
          await page.waitForTimeout(2000);

          const innerLinks = await page.evaluate(() => {
            const items = [];
            document.querySelectorAll('a[href]').forEach(a => {
              const href = a.href;
              const text = a.textContent.trim();
              if (!text || text.length < 3) return;
              if (/facebook|twitter|instagram|youtube|whatsapp|mailto:/i.test(href)) return;
              if (/decreto|ley|reforma|codigo|constitucion|\.pdf/i.test(href) ||
                  /decreto|ley|reforma|codigo/i.test(text)) {
                items.push({ href, text: text.substring(0, 200) });
              }
            });
            return items;
          });

          console.log(`    Found ${innerLinks.length} inner links`);

          for (const il of innerLinks) {
            if (state.hasSeen(il.href)) continue;
            await state.record(buildItem({
              url: il.href,
              parentUrl: al.href,
              title: il.text,
              needsBrowser: true,
            }));
          }
        } catch (err) {
          console.log(`  [error] Failed to drill into ${al.text}: ${err.message}`);
        }
      }

      processed++;
    }

    // Fallback: dump page structure if nothing found
    if (annualLinks.length === 0) {
      console.log('[asamblea-annuals] No annual links found. Capturing page structure...');
      const info = await page.evaluate(() => ({
        title: document.title,
        headings: Array.from(document.querySelectorAll('h1,h2,h3')).map(h => h.textContent.trim()),
        allLinks: Array.from(document.querySelectorAll('a[href]')).slice(0, 40).map(a => ({
          href: a.href, text: a.textContent.trim().substring(0, 100),
        })),
        bodySnippet: document.body?.innerText?.substring(0, 2000) || '',
      }));
      console.log('  Page info:', JSON.stringify(info, null, 2));
    }

  } finally {
    await page.context().close();
  }

  return state.stats();
}
