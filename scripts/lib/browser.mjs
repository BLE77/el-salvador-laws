/**
 * Shared Playwright browser manager.
 * Launches a single browser instance and provides page creation.
 */

import { chromium } from 'playwright';

let _browser = null;

/**
 * Get or create a shared browser instance.
 * @returns {Promise<import('playwright').Browser>}
 */
export async function getBrowser() {
  if (!_browser || !_browser.isConnected()) {
    console.log('  [browser] Launching Chromium...');
    _browser = await chromium.launch({
      headless: true,
      args: ['--disable-blink-features=AutomationControlled'],
    });
  }
  return _browser;
}

/**
 * Create a new page with sensible defaults.
 * @returns {Promise<import('playwright').Page>}
 */
export async function newPage() {
  const browser = await getBrowser();
  const context = await browser.newContext({
    locale: 'es-SV',
    userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    viewport: { width: 1280, height: 800 },
  });
  const page = await context.newPage();
  page.setDefaultTimeout(30000);
  page.setDefaultNavigationTimeout(45000);
  return page;
}

/**
 * Close the shared browser.
 */
export async function closeBrowser() {
  if (_browser && _browser.isConnected()) {
    await _browser.close();
    _browser = null;
    console.log('  [browser] Closed.');
  }
}
