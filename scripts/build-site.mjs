import { promises as fs } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const rootDir = path.resolve(__dirname, "..");
const contentDir = path.join(rootDir, "content");
const publicDir = path.join(rootDir, "public");
const dataDir = path.join(rootDir, "data");
const distDir = path.join(rootDir, "dist");

async function main() {
  await fs.rm(distDir, { recursive: true, force: true });
  await fs.mkdir(distDir, { recursive: true });

  const markdownFiles = await listFiles(contentDir, ".md");
  const pages = [];

  for (const filePath of markdownFiles) {
    const relPath = path.relative(contentDir, filePath);
    const source = await fs.readFile(filePath, "utf8");
    const title = findTitle(source) || humanize(path.basename(relPath, ".md"));
    const outputRel = relPath.replace(/\.md$/i, ".html");

    pages.push({
      title,
      sourceRel: relPath,
      outputRel,
      markdown: source
    });
  }

  pages.sort((a, b) => {
    if (a.outputRel === "index.html") return -1;
    if (b.outputRel === "index.html") return 1;
    return a.title.localeCompare(b.title);
  });

  await copyDir(publicDir, distDir);
  await copyDir(dataDir, path.join(distDir, "data"));

  for (const page of pages) {
    const html = renderPage(page, pages);
    const outputPath = path.join(distDir, page.outputRel);
    await fs.mkdir(path.dirname(outputPath), { recursive: true });
    await fs.writeFile(outputPath, html);

    const markdownOut = path.join(distDir, "markdown", page.sourceRel);
    await fs.mkdir(path.dirname(markdownOut), { recursive: true });
    await fs.writeFile(markdownOut, page.markdown);
  }

  const searchIndex = pages.map((page) => ({
    title: page.title,
    url: page.outputRel === "index.html" ? "./index.html" : `./${page.outputRel}`,
    source_markdown: `./markdown/${page.sourceRel}`
  }));

  await fs.writeFile(
    path.join(distDir, "search-index.json"),
    JSON.stringify(searchIndex, null, 2) + "\n"
  );

  console.log(`Built ${pages.length} pages into ${distDir}`);
}

function renderPage(page, pages) {
  const depth = page.outputRel.split("/").length - 1;
  const prefix = depth === 0 ? "./" : "../".repeat(depth);
  const siteTitle = "El Salvador Law Atlas";
  const fullTitle = page.title === siteTitle ? siteTitle : `${page.title} | ${siteTitle}`;
  const nav = pages
    .map((candidate) => {
      const href = relativeHref(page.outputRel, candidate.outputRel);
      const current = candidate.outputRel === page.outputRel ? ' aria-current="page"' : "";
      return `<li><a href="${href}"${current}>${escapeHtml(candidate.title)}</a></li>`;
    })
    .join("");

  const body = renderMarkdown(page.markdown);
  const markdownHref = `${prefix}markdown/${page.sourceRel}`;
  const inventoryHref = `${prefix}data/source-inventory.json`;
  const hasCatalog = page.outputRel === "index.html";

  return `<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>${escapeHtml(fullTitle)}</title>
    <meta name="description" content="Research hub for a markdown-first, AI-searchable El Salvador law corpus.">
    <link rel="icon" href="${prefix}favicon.svg" type="image/svg+xml">
    <link rel="stylesheet" href="${prefix}styles.css">
  </head>
  <body>
    <div class="shell">
      <div class="topbar">
        <span class="badge">Research snapshot: 2026-04-06</span>
        <span class="badge">Markdown-first corpus plan</span>
        <span class="badge">Official-first source strategy</span>
      </div>
      <header class="hero">
        <h1>El Salvador Law Atlas</h1>
        <p>A searchable research hub for building an AI-friendly corpus of Salvadoran laws, gazettes, codes, regulations, and source metadata.</p>
      </header>
      <div class="layout">
        <aside class="sidebar">
          <h2>Pages</h2>
          <ul>${nav}</ul>
        </aside>
        <main>
          <article class="panel">
            ${body}
            <div class="page-tools">
              <a class="tool-link" href="${markdownHref}">Raw markdown</a>
              <a class="tool-link" href="${inventoryHref}">Source inventory JSON</a>
            </div>
          </article>
          ${
            hasCatalog
              ? `<section class="catalog" data-source-catalog="${inventoryHref}">
                  <h2>Source Catalog</h2>
                  <p><strong><span data-catalog-count>0</span></strong> tracked sources across official and secondary channels.</p>
                  <div class="catalog-controls">
                    <input class="catalog-input" data-catalog-input type="search" placeholder="Search by name, scope, notes, or use case">
                    <div class="chips">
                      <button class="chip is-active" data-filter-tier="all" type="button">all</button>
                      <button class="chip" data-filter-tier="official" type="button">official</button>
                      <button class="chip" data-filter-tier="secondary" type="button">secondary</button>
                    </div>
                  </div>
                  <div class="catalog-grid" data-catalog-grid></div>
                </section>`
              : ""
          }
          <section class="note">
            <strong>Why this matters:</strong> The hard part is not just OCR. It is preserving the link between enactment, official publication, amendment history, and article-level text so AI can answer with the right law version.
          </section>
          <div class="footer">
            This prototype favors raw provenance and machine readability over polished consolidation. Official publication status remains controlling.
          </div>
        </main>
      </div>
    </div>
    <script src="${prefix}app.js" defer></script>
  </body>
</html>`;
}

function renderMarkdown(source) {
  const lines = source.replace(/\r\n/g, "\n").split("\n");
  const output = [];
  let paragraph = [];
  let list = [];
  let listType = null;
  let quote = [];
  let code = [];
  let codeLang = "";
  let inCode = false;

  const flushParagraph = () => {
    if (!paragraph.length) return;
    output.push(`<p>${renderInline(paragraph.join(" "))}</p>`);
    paragraph = [];
  };

  const flushList = () => {
    if (!list.length || !listType) return;
    const items = list.map((item) => `<li>${renderInline(item)}</li>`).join("");
    output.push(`<${listType}>${items}</${listType}>`);
    list = [];
    listType = null;
  };

  const flushQuote = () => {
    if (!quote.length) return;
    output.push(`<blockquote><p>${renderInline(quote.join(" "))}</p></blockquote>`);
    quote = [];
  };

  const flushCode = () => {
    if (!code.length && !codeLang) return;
    const escaped = escapeHtml(code.join("\n"));
    output.push(`<pre><code class="language-${escapeHtml(codeLang || "plain")}">${escaped}</code></pre>`);
    code = [];
    codeLang = "";
  };

  for (const line of lines) {
    if (inCode) {
      if (line.startsWith("```")) {
        inCode = false;
        flushCode();
      } else {
        code.push(line);
      }
      continue;
    }

    if (line.startsWith("```")) {
      flushParagraph();
      flushList();
      flushQuote();
      inCode = true;
      codeLang = line.slice(3).trim();
      continue;
    }

    if (!line.trim()) {
      flushParagraph();
      flushList();
      flushQuote();
      continue;
    }

    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      flushParagraph();
      flushList();
      flushQuote();
      const level = heading[1].length;
      const text = heading[2].trim();
      output.push(`<h${level} id="${slugify(text)}">${renderInline(text)}</h${level}>`);
      continue;
    }

    const ul = line.match(/^[-*]\s+(.*)$/);
    if (ul) {
      flushParagraph();
      flushQuote();
      if (listType && listType !== "ul") {
        flushList();
      }
      listType = "ul";
      list.push(ul[1]);
      continue;
    }

    const ol = line.match(/^\d+\.\s+(.*)$/);
    if (ol) {
      flushParagraph();
      flushQuote();
      if (listType && listType !== "ol") {
        flushList();
      }
      listType = "ol";
      list.push(ol[1]);
      continue;
    }

    const block = line.match(/^>\s?(.*)$/);
    if (block) {
      flushParagraph();
      flushList();
      quote.push(block[1]);
      continue;
    }

    flushList();
    flushQuote();
    paragraph.push(line.trim());
  }

  flushParagraph();
  flushList();
  flushQuote();

  return output.join("\n");
}

function renderInline(text) {
  let rendered = escapeHtml(text);
  rendered = rendered.replace(/`([^`]+)`/g, "<code>$1</code>");
  rendered = rendered.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2">$1</a>');
  rendered = rendered.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  rendered = rendered.replace(/\*([^*]+)\*/g, "<em>$1</em>");
  return rendered;
}

function findTitle(source) {
  const match = source.match(/^#\s+(.+)$/m);
  return match ? match[1].trim() : "";
}

function humanize(input) {
  return input
    .replace(/[-_]/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function slugify(value) {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

function escapeHtml(value) {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function relativeHref(from, to) {
  const fromDir = path.dirname(from);
  const rel = path.relative(fromDir, to).replaceAll(path.sep, "/");
  return rel.startsWith(".") ? rel : `./${rel}`;
}

async function listFiles(dir, extension) {
  const output = [];
  const entries = await fs.readdir(dir, { withFileTypes: true });

  for (const entry of entries) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      output.push(...(await listFiles(full, extension)));
    } else if (entry.isFile() && entry.name.endsWith(extension)) {
      output.push(full);
    }
  }

  return output;
}

async function copyDir(source, destination) {
  await fs.mkdir(destination, { recursive: true });
  const entries = await fs.readdir(source, { withFileTypes: true });

  for (const entry of entries) {
    const from = path.join(source, entry.name);
    const to = path.join(destination, entry.name);
    if (entry.isDirectory()) {
      await copyDir(from, to);
    } else if (entry.isFile()) {
      await fs.copyFile(from, to);
    }
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
