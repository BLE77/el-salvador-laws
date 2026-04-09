#!/usr/bin/env node

/**
 * Merge all per-source inventory NDJSON files into a single inventory.ndjson.
 *
 * Usage: node scripts/merge-inventory.mjs
 */

import { readdirSync, createReadStream, existsSync } from 'node:fs';
import { writeFile } from 'node:fs/promises';
import { createInterface } from 'node:readline';
import { join, resolve } from 'node:path';

const dataDir = resolve(process.env.DATA_DIR || './data');
const runsDir = join(dataDir, 'runs');
const outFile = join(dataDir, 'inventory.ndjson');

async function main() {
  if (!existsSync(runsDir)) {
    console.log('No runs directory found. Run a crawl first.');
    return;
  }

  const sources = readdirSync(runsDir, { withFileTypes: true })
    .filter(d => d.isDirectory())
    .map(d => d.name);

  const seen = new Set();
  const rows = [];

  for (const source of sources) {
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
        const key = row.discovered_url;
        if (key && !seen.has(key)) {
          seen.add(key);
          rows.push(row);
        }
      } catch { /* skip */ }
    }
  }

  const output = rows.map(r => JSON.stringify(r)).join('\n') + '\n';
  await writeFile(outFile, output, 'utf8');

  console.log(`Merged ${rows.length} unique items from ${sources.length} sources -> ${outFile}`);

  // Type breakdown
  const types = {};
  for (const r of rows) {
    const t = r.document_type || 'unknown';
    types[t] = (types[t] || 0) + 1;
  }
  console.log('\nBy document type:');
  for (const [t, c] of Object.entries(types).sort((a, b) => b[1] - a[1])) {
    console.log(`  ${t}: ${c}`);
  }
}

main().catch(console.error);
