#!/usr/bin/env node

/**
 * Print inventory statistics from crawl runs.
 *
 * Usage: node scripts/inventory-stats.mjs
 */

import { readdirSync, createReadStream, existsSync } from 'node:fs';
import { createInterface } from 'node:readline';
import { join, resolve } from 'node:path';

const dataDir = resolve(process.env.DATA_DIR || './data');
const runsDir = join(dataDir, 'runs');

async function loadSource(source) {
  const file = join(runsDir, source, 'inventory.ndjson');
  if (!existsSync(file)) return [];

  const rows = [];
  const rl = createInterface({
    input: createReadStream(file, 'utf8'),
    crlfDelay: Infinity,
  });

  for await (const line of rl) {
    if (!line.trim()) continue;
    try { rows.push(JSON.parse(line)); } catch { /* skip */ }
  }
  return rows;
}

async function main() {
  if (!existsSync(runsDir)) {
    console.log('No runs directory found. Run a crawl first.');
    return;
  }

  const sources = readdirSync(runsDir, { withFileTypes: true })
    .filter(d => d.isDirectory())
    .map(d => d.name);

  console.log('INVENTORY STATISTICS');
  console.log('='.repeat(60));

  let grandTotal = 0;

  for (const source of sources) {
    const rows = await loadSource(source);
    grandTotal += rows.length;

    const types = {};
    const formats = {};
    const years = {};

    for (const r of rows) {
      types[r.document_type || 'unknown'] = (types[r.document_type || 'unknown'] || 0) + 1;
      formats[r.format || 'unknown'] = (formats[r.format || 'unknown'] || 0) + 1;
      if (r.year) years[r.year] = (years[r.year] || 0) + 1;
    }

    console.log(`\n${source}: ${rows.length} items`);
    console.log('  Types:', Object.entries(types).sort((a, b) => b[1] - a[1]).map(([t, c]) => `${t}(${c})`).join(', '));
    console.log('  Formats:', Object.entries(formats).sort((a, b) => b[1] - a[1]).map(([f, c]) => `${f}(${c})`).join(', '));
    if (Object.keys(years).length > 0) {
      const sorted = Object.keys(years).sort();
      console.log(`  Year range: ${sorted[0]} - ${sorted[sorted.length - 1]} (${Object.keys(years).length} distinct years)`);
    }
  }

  console.log(`\n${'='.repeat(60)}`);
  console.log(`Grand total: ${grandTotal} items across ${sources.length} sources`);
}

main().catch(console.error);
