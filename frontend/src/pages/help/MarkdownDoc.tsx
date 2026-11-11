/**
 * @file Minimal dependency-free Markdown renderer used by the Help developer guide.
 *
 * Supports a constrained subset of CommonMark-like syntax sufficient for
 * `docs/mask-and-row-filter-flows.md`: ATX headings, fenced code, pipe tables, lists,
 * horizontal rules, paragraphs, and inline `` `code` `` / `**bold**`.
 */
import type { ReactNode } from "react";
import styles from "./HelpPage.module.css";

/**
 * Parses a single line of text into React nodes with inline code and bold spans.
 *
 * Matching rules: consecutive runs of `` `...` `` become `<code>`; runs of `**...**` become
 * `<strong>`; all other text is emitted as plain strings. Nested or overlapping markup is not
 * supported.
 *
 * @param text - Raw inline markdown fragment (no block structure).
 */
function inline(text: string): ReactNode[] {
  const parts: ReactNode[] = [];
  const pattern = /(`[^`]+`|\*\*[^*]+\*\*)/g;
  let last = 0;
  let match: RegExpExecArray | null;
  let key = 0;
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > last) parts.push(text.slice(last, match.index));
    const token = match[0];
    if (token.startsWith("`")) {
      parts.push(<code key={key++}>{token.slice(1, -1)}</code>);
    } else {
      parts.push(<strong key={key++}>{token.slice(2, -2)}</strong>);
    }
    last = match.index + token.length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts;
}

/**
 * Converts consecutive pipe-table lines into a `<table>` element.
 *
 * Expects GFM-style rows: first line is the header, second is the separator (skipped), and the
 * remainder are body rows. Leading/trailing pipes are stripped; cells are trimmed and passed
 * through {@link inline}.
 *
 * @param lines - Contiguous table lines, each starting with `|`.
 */
function parseTable(lines: string[]): ReactNode {
  const rows = lines.map((line) =>
    line
      .trim()
      .replace(/^\|/, "")
      .replace(/\|$/, "")
      .split("|")
      .map((cell) => cell.trim()),
  );
  const header = rows[0] ?? [];
  const body = rows.slice(2);
  return (
    <table>
      <thead>
        <tr>
          {header.map((cell) => (
            <th key={cell}>{inline(cell)}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {body.map((row, index) => (
          <tr key={index}>
            {row.map((cell, cellIndex) => (
              <td key={`${index}-${cellIndex}`}>{inline(cell)}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/**
 * Lightweight Markdown renderer for the developer guide (no external deps).
 *
 * Block parsing (top to bottom): blank lines skipped; `---` → `<hr>`; fenced ` ``` ` blocks →
 * `<pre><code>` (optional language in `data-lang`); `|` rows → pipe tables; `#` / `##` / `###`
 * headings get slug `id`s (`lower case`, non-`[a-z0-9]` → `-`); `- ` / `* ` → `<ul>`; numbered
 * `N. ` → `<ol>`; otherwise adjacent non-block lines join into a `<p>`.
 *
 * @param props.source - Full markdown document text (CRLF normalized to LF).
 */
export function MarkdownDoc({ source }: { source: string }) {
  const lines = source.replace(/\r\n/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let i = 0;
  let key = 0;

  while (i < lines.length) {
    const line = lines[i] ?? "";
    if (!line.trim()) {
      i += 1;
      continue;
    }
    if (line.trim() === "---") {
      blocks.push(<hr key={key++} />);
      i += 1;
      continue;
    }
    if (line.startsWith("```")) {
      const lang = line.slice(3).trim();
      const body: string[] = [];
      i += 1;
      while (i < lines.length && !(lines[i] ?? "").startsWith("```")) {
        body.push(lines[i] ?? "");
        i += 1;
      }
      i += 1;
      blocks.push(
        <pre key={key++} data-lang={lang || undefined}>
          <code>{body.join("\n")}</code>
        </pre>,
      );
      continue;
    }
    if (line.startsWith("|")) {
      const table: string[] = [];
      while (i < lines.length && (lines[i] ?? "").startsWith("|")) {
        table.push(lines[i] ?? "");
        i += 1;
      }
      blocks.push(<div key={key++}>{parseTable(table)}</div>);
      continue;
    }
    if (line.startsWith("# ")) {
      const text = line.slice(2);
      const id = text.toLowerCase().replace(/[^a-z0-9]+/g, "-");
      blocks.push(
        <h1 key={key++} id={id}>
          {inline(text)}
        </h1>,
      );
      i += 1;
      continue;
    }
    if (line.startsWith("## ")) {
      const text = line.slice(3);
      const id = text.toLowerCase().replace(/[^a-z0-9]+/g, "-");
      blocks.push(
        <h2 key={key++} id={id}>
          {inline(text)}
        </h2>,
      );
      i += 1;
      continue;
    }
    if (line.startsWith("### ")) {
      const text = line.slice(4);
      const id = text.toLowerCase().replace(/[^a-z0-9]+/g, "-");
      blocks.push(
        <h3 key={key++} id={id}>
          {inline(text)}
        </h3>,
      );
      i += 1;
      continue;
    }
    if (line.startsWith("- ") || line.startsWith("* ")) {
      const items: string[] = [];
      while (i < lines.length && ((lines[i] ?? "").startsWith("- ") || (lines[i] ?? "").startsWith("* "))) {
        items.push((lines[i] ?? "").slice(2));
        i += 1;
      }
      blocks.push(
        <ul key={key++}>
          {items.map((item, index) => (
            <li key={index}>{inline(item)}</li>
          ))}
        </ul>,
      );
      continue;
    }
    if (/^\d+\.\s/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\d+\.\s/.test(lines[i] ?? "")) {
        items.push((lines[i] ?? "").replace(/^\d+\.\s/, ""));
        i += 1;
      }
      blocks.push(
        <ol key={key++}>
          {items.map((item, index) => (
            <li key={index}>{inline(item)}</li>
          ))}
        </ol>,
      );
      continue;
    }
    const paragraph: string[] = [line];
    i += 1;
    while (
      i < lines.length &&
      (lines[i] ?? "").trim() &&
      !(lines[i] ?? "").startsWith("#") &&
      !(lines[i] ?? "").startsWith("|") &&
      !(lines[i] ?? "").startsWith("```") &&
      !(lines[i] ?? "").startsWith("- ") &&
      !(lines[i] ?? "").startsWith("* ") &&
      !(lines[i] ?? "").trim().startsWith("---") &&
      !/^\d+\.\s/.test(lines[i] ?? "")
    ) {
      paragraph.push(lines[i] ?? "");
      i += 1;
    }
    blocks.push(<p key={key++}>{inline(paragraph.join(" "))}</p>);
  }

  return <article className={styles.doc}>{blocks}</article>;
}
