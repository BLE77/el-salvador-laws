#!/usr/bin/env node

/**
 * PDF downloader for discovered inventory items.
 *
 * Usage:
 *   node scripts/download.mjs                    # download all discovered PDFs
 *   node scripts/download.mjs --source diario-archive
 *   node scripts/download.mjs --year 2024
 *   node scripts/download.mjs --year 2020-2026
 *   node scripts/download.mjs --limit 50
 *   node scripts/download.mjs --dry-run
 *
 * Env vars:
 *   RAW_DIR=B:/el-salvador-laws/raw     Override raw storage path
 *   DATA_DIR=./data                     Override data directory
 *   CONCURRENCY=3                       Parallel downloads (default 3)
 *   DELAY_MS=800                        Delay between downloads (default 800)
 */

import { createReadStream, existsSync, mkdirSync, createWriteStream } from 'node:fs';
import { readFile, appendFile, stat, writeFile } from 'node:fs/promises';
import { createInterface } from 'node:readline';
import { join, resolve } from 'node:path';
import { createHash } from 'node:crypto';
import { pipeline } from 'node:stream/promises';
import { Readable } from 'node:stream';
import https from 'node:https';
import { sleep, withRetry } from './lib/rate-limit.mjs';

// Asamblea has an incomplete certificate chain — allow self-signed certs for gov sites
process.env.NODE_TLS_REJECT_UNAUTHORIZED = '0';

const DATA_DIR = resolve(process.env.DATA_DIR || './data');
const RAW_DIR = resolve(process.env.RAW_DIR || 'B:/el-salvador-laws/raw');
const CONCURRENCY = parseInt(process.env.CONCURRENCY) || 3;
const DELAY_MS = parseInt(process.env.DELAY_MS) || 800;

// Download state tracking
const STATE_FILE = join(DATA_DIR, 'download-state.ndjson');

/** Parse CLI args */
function parseArgs() {
  const args = process.argv.slice(2);
  const opts = { source: null, yearMin: null, yearMax: null, limit: Infinity, dryRun: false };

  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--source' && args[i + 1]) { opts.source = args[++i]; continue; }
    if (args[i] === '--year' && args[i + 1]) {
      const val = args[++i];
      if (val.includes('-')) {
        const [a, b] = val.split('-').map(Number);
        opts.yearMin = a; opts.yearMax = b;
      } else {
        opts.yearMin = opts.yearMax = parseInt(val);
      }
      continue;
    }
    if (args[i] === '--limit' && args[i + 1]) { opts.limit = parseInt(args[++i]); continue; }
    if (args[i] === '--dry-run') { opts.dryRun = true; continue; }
    if (args[i] === '--help' || args[i] === '-h') {
      console.log(`
Usage: node scripts/download.mjs [options]

Options:
  --source <id>      Filter by source (diario-archive, asamblea-year-archive, etc.)
  --year <YYYY>      Download specific year
  --year <MIN-MAX>   Download year range (e.g., 2020-2026)
  --limit <N>        Max files to download
  --dry-run          Show what would be downloaded without downloading
  --help             Show this help

Env vars:
  RAW_DIR            Where to store raw PDFs (default: B:/el-salvador-laws/raw)
  CONCURRENCY        Parallel downloads (default: 3)
  DELAY_MS           Delay between downloads (default: 800)
`);
      process.exit(0);
    }
  }
  return opts;
}

/** Load all inventory NDJSON files and return downloadable items. */
async function loadInventory(opts) {
  const items = [];
  const runsDir = join(DATA_DIR, 'runs');

  if (!existsSync(runsDir)) {
    console.error('No runs/ directory found. Run crawlers first.');
    process.exit(1);
  }

  // Read all inventory files
  const { readdirSync } = await import('node:fs');
  const sources = readdirSync(runsDir);

  for (const source of sources) {
    if (opts.source && source !== opts.source) continue;
    const file = join(runsDir, source, 'inventory.ndjson');
    if (!existsSync(file)) continue;

    const rl = createInterface({
      input: createReadStream(file, 'utf8'),
      crlfDelay: Infinity,
    });

    for await (const line of rl) {
      if (!line.trim()) continue;
      try {
        const row = JSON.parse(line);
        // Only downloadable items: actual PDFs or gazette pages with PDF links
        // Skip decree-page (HTML detail pages) and decree-detail (metadata only)
        if (row.format === 'pdf' || row.document_type === 'gazette-issue-page' ||
            row.document_type === 'direct-pdf') {
          // Year filter
          if (opts.yearMin && row.year) {
            const y = parseInt(row.year);
            if (y < opts.yearMin || y > opts.yearMax) continue;
          }
          items.push(row);
        }
      } catch { /* skip */ }
    }
  }

  return items;
}

/** Load already-downloaded URLs. */
async function loadDownloadState() {
  const done = new Set();
  if (!existsSync(STATE_FILE)) return done;

  const rl = createInterface({
    input: createReadStream(STATE_FILE, 'utf8'),
    crlfDelay: Infinity,
  });

  for await (const line of rl) {
    if (!line.trim()) continue;
    try {
      const row = JSON.parse(line);
      if (row.status === 'downloaded' && row.url) done.add(row.url);
    } catch { /* skip */ }
  }

  return done;
}

/** Record a download result. */
async function recordDownload(entry) {
  await appendFile(STATE_FILE, JSON.stringify(entry) + '\n', 'utf8');
}

/** Compute SHA256 of a file. */
async function sha256File(filePath) {
  const data = await readFile(filePath);
  return createHash('sha256').update(data).digest('hex');
}

/** Build local file path for a URL. */
function localPath(item) {
  const source = item.source || 'unknown';
  const year = item.year || 'unknown';

  // For Asamblea decree PDFs, use decree number + GUID
  const url = item.discovered_url || '';
  let filename = item.filename;
  if (!filename) {
    const urlFilename = url.split('/').pop() || '';
    if (urlFilename.endsWith('.pdf')) {
      // Use the GUID filename from the URL
      const decreeNo = item.decree_no ? `decreto-${item.decree_no}_` : '';
      filename = `${decreeNo}${urlFilename}`;
    } else {
      const id = item.diario_id || urlFilename || 'file';
      filename = `${id}.pdf`;
    }
  }
  return join(RAW_DIR, source, year, filename);
}

/** Download one file. */
async function downloadOne(item) {
  const dest = localPath(item);
  const dir = join(dest, '..');
  mkdirSync(dir, { recursive: true });

  // Skip if already on disk
  if (existsSync(dest)) {
    try {
      const s = await stat(dest);
      if (s.size > 0) return { url: item.discovered_url, path: dest, status: 'exists', size: s.size };
    } catch { /* continue */ }
  }

  const url = item.discovered_url;

  const res = await withRetry(async () => {
    const r = await fetch(url, {
      headers: {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'application/pdf,*/*',
      },
      redirect: 'follow',
    });
    if (!r.ok) throw new Error(`HTTP ${r.status} for ${url}`);
    return r;
  }, 3, 2000);

  // Stream to disk
  const body = Readable.fromWeb(res.body);
  await pipeline(body, createWriteStream(dest));

  const s = await stat(dest);
  const sha = await sha256File(dest);

  return { url, path: dest, status: 'downloaded', size: s.size, sha256: sha };
}

/** Run downloads with concurrency control. */
async function downloadBatch(items, opts) {
  let completed = 0;
  let failed = 0;
  let skipped = 0;
  const total = items.length;

  console.log(`\nDownloading ${total} files (concurrency: ${CONCURRENCY}, delay: ${DELAY_MS}ms)`);
  console.log(`Saving to: ${RAW_DIR}\n`);

  // Process in chunks
  for (let i = 0; i < total; i += CONCURRENCY) {
    const batch = items.slice(i, i + CONCURRENCY);
    const results = await Promise.allSettled(
      batch.map(async (item) => {
        try {
          const result = await downloadOne(item);
          if (result.status === 'exists') {
            skipped++;
          } else {
            completed++;
            await recordDownload({
              url: result.url,
              path: result.path,
              status: 'downloaded',
              size: result.size,
              sha256: result.sha256,
              downloaded_at: new Date().toISOString(),
            });
          }
          return result;
        } catch (err) {
          failed++;
          console.error(`  [fail] ${item.discovered_url}: ${err.message}`);
          await recordDownload({
            url: item.discovered_url,
            status: 'failed',
            error: err.message,
            attempted_at: new Date().toISOString(),
          });
          return null;
        }
      })
    );

    // Progress
    const pct = Math.round(((i + batch.length) / total) * 100);
    process.stdout.write(`\r  [${pct}%] ${completed} downloaded, ${skipped} existing, ${failed} failed (${i + batch.length}/${total})`);

    // Rate limit
    if (i + CONCURRENCY < total) await sleep(DELAY_MS);
  }

  console.log('\n');
  return { completed, skipped, failed, total };
}

async function main() {
  const opts = parseArgs();

  console.log('='.repeat(60));
  console.log('PDF Downloader');
  console.log('='.repeat(60));
  console.log(`Raw dir:     ${RAW_DIR}`);
  console.log(`Data dir:    ${DATA_DIR}`);
  if (opts.source) console.log(`Source:      ${opts.source}`);
  if (opts.yearMin) console.log(`Year range:  ${opts.yearMin}-${opts.yearMax}`);
  if (opts.limit < Infinity) console.log(`Limit:       ${opts.limit}`);
  if (opts.dryRun) console.log(`Mode:        DRY RUN`);

  // Load inventory
  const allItems = await loadInventory(opts);
  console.log(`\nInventory: ${allItems.length} downloadable items found`);

  // Filter already downloaded
  const alreadyDone = await loadDownloadState();
  const pending = allItems.filter(i => !alreadyDone.has(i.discovered_url));
  console.log(`Already downloaded: ${alreadyDone.size}`);
  console.log(`Pending: ${pending.length}`);

  // Apply limit
  const toDownload = pending.slice(0, opts.limit);

  if (opts.dryRun) {
    console.log('\nDry run - would download:');
    for (const item of toDownload.slice(0, 20)) {
      console.log(`  ${item.discovered_url} -> ${localPath(item)}`);
    }
    if (toDownload.length > 20) console.log(`  ... and ${toDownload.length - 20} more`);
    return;
  }

  if (toDownload.length === 0) {
    console.log('\nNothing to download.');
    return;
  }

  const stats = await downloadBatch(toDownload, opts);

  console.log('='.repeat(60));
  console.log('DOWNLOAD SUMMARY');
  console.log('='.repeat(60));
  console.log(`  Downloaded: ${stats.completed}`);
  console.log(`  Existing:   ${stats.skipped}`);
  console.log(`  Failed:     ${stats.failed}`);
  console.log(`  Total:      ${stats.total}`);
}

main().catch(err => {
  console.error('Fatal error:', err);
  process.exit(1);
});
