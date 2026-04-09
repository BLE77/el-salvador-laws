#!/usr/bin/env node

/**
 * Inventory-first crawler CLI.
 *
 * Usage:
 *   node scripts/crawl.mjs <source-id>         # crawl one source
 *   node scripts/crawl.mjs --all                # crawl all sources sequentially
 *   node scripts/crawl.mjs --list               # list available sources
 *
 * Sources:
 *   asamblea-year-archive   Asamblea Legislativa year browse
 *   asamblea-search         Asamblea Legislativa search interface
 *   asamblea-annuals        Asamblea Legislativa annual compilations
 *   diario-archive          Diario Oficial public archive
 *   jurisprudencia          Jurisprudencia.gob.sv
 *   asamblea-library        Biblioteca Asamblea Legislativa
 *
 * Options (via env vars):
 *   MAX_PAGES=10            Limit pagination depth
 *   MAX_YEARS=5             Limit years to crawl
 *   DATA_DIR=./data         Override data directory
 */

import { resolve } from 'node:path';
import { closeBrowser } from './lib/browser.mjs';

const CRAWLERS = {
  'asamblea-year-archive': () => import('./crawlers/asamblea-year-archive.mjs'),
  'asamblea-search': () => import('./crawlers/asamblea-search.mjs'),
  'asamblea-annuals': () => import('./crawlers/asamblea-annuals.mjs'),
  'diario-archive': () => import('./crawlers/diario-archive.mjs'),
  'jurisprudencia': () => import('./crawlers/jurisprudencia.mjs'),
  'asamblea-library': () => import('./crawlers/asamblea-library.mjs'),
};

const args = process.argv.slice(2);
const dataDir = resolve(process.env.DATA_DIR || './data');

async function runSource(sourceId) {
  if (!CRAWLERS[sourceId]) {
    console.error(`Unknown source: ${sourceId}`);
    console.error(`Available: ${Object.keys(CRAWLERS).join(', ')}`);
    process.exit(1);
  }

  console.log(`\n${'='.repeat(60)}`);
  console.log(`Starting: ${sourceId}`);
  console.log(`Data dir: ${dataDir}`);
  console.log(`${'='.repeat(60)}\n`);

  const startTime = Date.now();
  const mod = await CRAWLERS[sourceId]();
  const options = {
    maxPages: parseInt(process.env.MAX_PAGES) || undefined,
    maxYears: parseInt(process.env.MAX_YEARS) || undefined,
    startYear: parseInt(process.env.START_YEAR) || undefined,
    detailsPerYear: parseInt(process.env.DETAILS_PER_YEAR) || 1000,
  };

  try {
    const stats = await mod.crawl(dataDir, options);
    const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
    console.log(`\n[done] ${sourceId}: ${stats.total} items discovered in ${elapsed}s`);
    return stats;
  } catch (err) {
    console.error(`\n[fatal] ${sourceId} failed: ${err.message}`);
    console.error(err.stack);
    return { sourceId, total: 0, error: err.message };
  }
}

async function main() {
  if (args.includes('--list') || args.includes('-l')) {
    console.log('Available sources:');
    for (const id of Object.keys(CRAWLERS)) {
      console.log(`  ${id}`);
    }
    return;
  }

  if (args.includes('--help') || args.includes('-h')) {
    console.log(`
Usage:
  node scripts/crawl.mjs <source-id>    Crawl one source
  node scripts/crawl.mjs --all          Crawl all sources
  node scripts/crawl.mjs --list         List sources

Sources: ${Object.keys(CRAWLERS).join(', ')}

Env vars:
  MAX_PAGES=N    Limit pagination depth
  MAX_YEARS=N    Limit years to crawl
  DATA_DIR=path  Override data directory (default: ./data)
`);
    return;
  }

  const allResults = [];

  try {
    if (args.includes('--all')) {
      for (const sourceId of Object.keys(CRAWLERS)) {
        allResults.push(await runSource(sourceId));
      }
    } else if (args.length > 0) {
      for (const sourceId of args) {
        allResults.push(await runSource(sourceId));
      }
    } else {
      console.log('No source specified. Use --list to see available sources or --help for usage.');
      process.exit(1);
    }
  } finally {
    await closeBrowser();
  }

  // Summary
  console.log(`\n${'='.repeat(60)}`);
  console.log('CRAWL SUMMARY');
  console.log(`${'='.repeat(60)}`);
  for (const r of allResults) {
    const status = r.error ? `ERROR: ${r.error}` : `${r.total} items`;
    console.log(`  ${r.sourceId}: ${status}`);
  }
  console.log();
}

main().catch(err => {
  console.error('Fatal error:', err);
  process.exit(1);
});
