/**
 * Crawler: Biblioteca Asamblea Legislativa
 *
 * Strategy:
 * 1. Load the library catalog
 * 2. Discover catalog categories and browse sections
 * 3. Collect record links for codes, constitutions, compilations
 */

import { newPage } from '../lib/browser.mjs';
import { CrawlState } from '../lib/state.mjs';
import { buildItem } from '../lib/classify.mjs';
import { createThrottle, withRetry } from '../lib/rate-limit.mjs';

const BASE_URL = 'https://biblioteca.asamblea.gob.sv/';

export async function crawl(dataDir, options = {}) {
  const state = new CrawlState('asamblea-library', dataDir);
  await state.load();

  const throttle = createThrottle(2000);
  const page = await newPage();
  const maxRecords = options.maxRecords || 200;

  try {
    console.log('[asamblea-library] Loading catalog...');
    await throttle();
    await withRetry(() => page.goto(BASE_URL, { waitUntil: 'networkidle' }));
    await page.waitForTimeout(3000);

    if (!state.hasSeen(BASE_URL)) {
      await state.record(buildItem({
        url: BASE_URL,
        parentUrl: null,
        title: 'Biblioteca Asamblea Legislativa (catalog)',
        needsBrowser: true,
        extra: { document_type: 'source-index' },
      }));
    }

    // Analyze the catalog structure
    const structure = await page.evaluate(() => {
      const links = Array.from(document.querySelectorAll('a[href]')).map(a => ({
        href: a.href, text: a.textContent.trim().substring(0, 150),
      })).filter(l => l.text.length > 2);

      const forms = Array.from(document.querySelectorAll('form')).map(f => ({
        action: f.action, method: f.method,
        inputs: Array.from(f.querySelectorAll('input, select, textarea')).map(i => ({
          tag: i.tagName, name: i.name, type: i.type, placeholder: i.placeholder,
        })),
      }));

      return {
        title: document.title,
        links: links.slice(0, 50),
        linkCount: links.length,
        forms,
        headings: Array.from(document.querySelectorAll('h1,h2,h3,h4')).map(h => h.textContent.trim()),
        bodySnippet: document.body?.innerText?.substring(0, 3000) || '',
      };
    });

    console.log(`[asamblea-library] Page: "${structure.title}"`);
    console.log(`  Links: ${structure.linkCount}, Forms: ${structure.forms.length}`);

    // Collect all on-domain links first
    const catalogLinks = structure.links.filter(l =>
      l.href.includes('biblioteca.asamblea.gob.sv') && l.href !== BASE_URL
    );

    // Categorize: search pages, record pages, browse pages
    const sectionLinks = catalogLinks.filter(l =>
      /buscar|search|catalogo|coleccion|browse|legisl|codigo|constituci|record|item/i.test(l.href) ||
      /buscar|catalogo|coleccion|codigo|constituci|legisl/i.test(l.text)
    );

    console.log(`[asamblea-library] Found ${sectionLinks.length} catalog sections`);

    // Record section links
    for (const sl of sectionLinks) {
      if (state.hasSeen(sl.href)) continue;
      await state.record(buildItem({
        url: sl.href,
        parentUrl: BASE_URL,
        title: sl.text,
        needsBrowser: true,
      }));
    }

    // Drill into catalog sections to find individual records
    let totalRecords = 0;
    const visitedSections = new Set();

    for (const sl of sectionLinks) {
      if (totalRecords >= maxRecords) break;
      if (visitedSections.has(sl.href)) continue;
      if (!sl.href.includes('biblioteca.asamblea.gob.sv')) continue;
      visitedSections.add(sl.href);

      console.log(`  [section] Drilling into: ${sl.text}`);
      await throttle();

      try {
        await withRetry(() => page.goto(sl.href, { waitUntil: 'networkidle' }));
        await page.waitForTimeout(2000);

        const recordLinks = await page.evaluate(() => {
          const items = [];
          document.querySelectorAll('a[href]').forEach(a => {
            const href = a.href;
            const text = a.textContent.trim();
            if (!text || text.length < 5) return;
            if (/facebook|twitter|instagram|youtube|whatsapp|mailto:/i.test(href)) return;
            if (/record|item|detalle|view|\.pdf|codigo|constituci|ley|decreto/i.test(href) ||
                /c[oó]digo|constituci|ley|decreto|reglamento|compilaci/i.test(text)) {
              items.push({ href, text: text.substring(0, 300) });
            }
          });
          return items;
        });

        console.log(`    Found ${recordLinks.length} record links`);

        for (const rl of recordLinks) {
          if (totalRecords >= maxRecords) break;
          if (state.hasSeen(rl.href)) continue;
          await state.record(buildItem({
            url: rl.href,
            parentUrl: sl.href,
            title: rl.text,
            needsBrowser: true,
          }));
          totalRecords++;
        }
      } catch (err) {
        console.log(`    [error] Failed: ${err.message}`);
      }
    }

    // Record remaining catalog links not yet seen
    for (const cl of catalogLinks) {
      if (state.hasSeen(cl.href)) continue;
      await state.record(buildItem({
        url: cl.href,
        parentUrl: BASE_URL,
        title: cl.text,
        needsBrowser: true,
      }));
    }

  } finally {
    await page.context().close();
  }

  return state.stats();
}
