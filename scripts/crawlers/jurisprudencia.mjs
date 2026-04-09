/**
 * Crawler: Jurisprudencia.gob.sv
 *
 * Strategy:
 * 1. Load the main page
 * 2. Discover the navigation/search structure
 * 3. Browse legislation category
 * 4. Paginate through results to discover document URLs
 */

import { newPage } from '../lib/browser.mjs';
import { CrawlState } from '../lib/state.mjs';
import { buildItem } from '../lib/classify.mjs';
import { createThrottle, withRetry } from '../lib/rate-limit.mjs';

const BASE_URL = 'https://www.jurisprudencia.gob.sv/';

export async function crawl(dataDir, options = {}) {
  const state = new CrawlState('jurisprudencia', dataDir);
  await state.load();

  const throttle = createThrottle(2500);
  const page = await newPage();
  const maxPages = options.maxPages || 15;

  try {
    console.log('[jurisprudencia] Loading main page...');
    await throttle();
    await withRetry(() => page.goto(BASE_URL, { waitUntil: 'networkidle' }));
    await page.waitForTimeout(3000);

    if (!state.hasSeen(BASE_URL)) {
      await state.record(buildItem({
        url: BASE_URL,
        parentUrl: null,
        title: 'Jurisprudencia.gob.sv (index)',
        needsBrowser: true,
        extra: { document_type: 'source-index' },
      }));
    }

    // Analyze page structure
    const structure = await page.evaluate(() => {
      const navLinks = Array.from(document.querySelectorAll('nav a, .menu a, .nav a, header a')).map(a => ({
        href: a.href, text: a.textContent.trim().substring(0, 100),
      }));
      const mainLinks = Array.from(document.querySelectorAll('main a, .content a, article a, #content a')).map(a => ({
        href: a.href, text: a.textContent.trim().substring(0, 100),
      }));
      const allLinks = Array.from(document.querySelectorAll('a[href]')).map(a => ({
        href: a.href, text: a.textContent.trim().substring(0, 100),
      })).filter(l => l.text.length > 2);
      const forms = Array.from(document.querySelectorAll('form')).map(f => ({
        action: f.action, method: f.method,
        inputs: Array.from(f.querySelectorAll('input, select')).map(i => ({
          tag: i.tagName, name: i.name, type: i.type, placeholder: i.placeholder,
        })),
      }));
      return {
        title: document.title,
        navLinks: navLinks.slice(0, 20),
        mainLinks: mainLinks.slice(0, 20),
        allLinks: allLinks.slice(0, 40),
        forms,
        headings: Array.from(document.querySelectorAll('h1,h2,h3')).map(h => h.textContent.trim()),
      };
    });

    console.log(`[jurisprudencia] Page: "${structure.title}"`);
    console.log(`  Nav links: ${structure.navLinks.length}, Main links: ${structure.mainLinks.length}, Forms: ${structure.forms.length}`);

    // Look for legislation/laws section links
    const lawSectionLinks = [...structure.navLinks, ...structure.mainLinks, ...structure.allLinks].filter(l =>
      /legislaci[oó]n|leyes|decretos|norma|c[oó]digo|reglamento|ley\b/i.test(l.text) ||
      /legislaci[oó]n|leyes|decretos|norma/i.test(l.href)
    );

    console.log(`[jurisprudencia] Found ${lawSectionLinks.length} legislation-related links`);

    // Record and optionally drill into legislation links
    const visitedSections = new Set();
    for (const sl of lawSectionLinks) {
      if (state.hasSeen(sl.href)) continue;

      await state.record(buildItem({
        url: sl.href,
        parentUrl: BASE_URL,
        title: sl.text,
        needsBrowser: true,
      }));

      // Drill into the first few unique sections
      const domain = new URL(sl.href).hostname;
      if (domain.includes('jurisprudencia.gob.sv') && !visitedSections.has(sl.href) && visitedSections.size < 5) {
        visitedSections.add(sl.href);
        console.log(`  [section] Drilling into: ${sl.text}`);
        await throttle();

        try {
          await withRetry(() => page.goto(sl.href, { waitUntil: 'networkidle' }));
          await page.waitForTimeout(3000);

          // Collect document links within this section
          let pageNum = 0;
          let hasMore = true;

          while (hasMore && pageNum < maxPages) {
            const docLinks = await page.evaluate(() => {
              const items = [];
              document.querySelectorAll('a[href]').forEach(a => {
                const href = a.href;
                const text = a.textContent.trim();
                if (!text || text.length < 5) return;
                if (/facebook|twitter|instagram|youtube|whatsapp|mailto:/i.test(href)) return;
                if (/documento|doc|detalle|view|\.pdf|decreto|ley|codigo|reglamento/i.test(href) ||
                    /decreto|ley|c[oó]digo|reglamento|constituci/i.test(text)) {
                  items.push({ href, text: text.substring(0, 300) });
                }
              });
              return items;
            });

            console.log(`    [page ${pageNum}] Found ${docLinks.length} document links`);

            for (const dl of docLinks) {
              if (state.hasSeen(dl.href)) continue;
              await state.record(buildItem({
                url: dl.href,
                parentUrl: sl.href,
                title: dl.text,
                needsBrowser: true,
              }));
            }

            // Try pagination
            const nextBtn = await page.$('a:has-text("Siguiente"), a:has-text("siguiente"), .next a, a[rel="next"], .pagination .next');
            if (nextBtn) {
              await throttle();
              await nextBtn.click();
              await page.waitForTimeout(3000);
              pageNum++;
            } else {
              hasMore = false;
            }
          }
        } catch (err) {
          console.log(`    [error] Failed to drill into ${sl.text}: ${err.message}`);
        }
      }
    }

    // Record all remaining on-domain links
    for (const link of structure.allLinks) {
      if (state.hasSeen(link.href)) continue;
      if (link.href.includes('jurisprudencia.gob.sv') && link.href !== BASE_URL) {
        await state.record(buildItem({
          url: link.href,
          parentUrl: BASE_URL,
          title: link.text,
          needsBrowser: true,
        }));
      }
    }

  } finally {
    await page.context().close();
  }

  return state.stats();
}
