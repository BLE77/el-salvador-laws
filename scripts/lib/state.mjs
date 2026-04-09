/**
 * Resumable crawl state manager.
 *
 * Each crawl run appends NDJSON lines to a run file.
 * On resume, already-seen URLs are loaded into a Set so we skip them.
 */

import { createReadStream, existsSync, mkdirSync } from 'node:fs';
import { appendFile, readFile } from 'node:fs/promises';
import { createInterface } from 'node:readline';
import { join } from 'node:path';

export class CrawlState {
  /**
   * @param {string} sourceId - e.g. "asamblea-year-archive"
   * @param {string} dataDir - root data directory
   */
  constructor(sourceId, dataDir) {
    this.sourceId = sourceId;
    this.runDir = join(dataDir, 'runs', sourceId);
    this.runFile = join(this.runDir, 'inventory.ndjson');
    this.seen = new Set();
    this.count = 0;
  }

  /** Load previously discovered URLs so we can skip them. */
  async load() {
    mkdirSync(this.runDir, { recursive: true });
    if (!existsSync(this.runFile)) return;

    const rl = createInterface({
      input: createReadStream(this.runFile, 'utf8'),
      crlfDelay: Infinity,
    });

    for await (const line of rl) {
      if (!line.trim()) continue;
      try {
        const row = JSON.parse(line);
        if (row.discovered_url) {
          this.seen.add(row.discovered_url);
          this.count++;
        }
      } catch { /* skip malformed lines */ }
    }

    console.log(`  [state] Loaded ${this.count} previously discovered items for ${this.sourceId}`);
  }

  /** Check if a URL has already been recorded. */
  hasSeen(url) {
    return this.seen.has(url);
  }

  /** Append one inventory record. */
  async record(item) {
    const row = {
      source: this.sourceId,
      discovered_at: new Date().toISOString(),
      ...item,
    };
    this.seen.add(item.discovered_url);
    this.count++;
    await appendFile(this.runFile, JSON.stringify(row) + '\n', 'utf8');
    return row;
  }

  /** Return summary stats. */
  stats() {
    return { sourceId: this.sourceId, total: this.count };
  }
}
