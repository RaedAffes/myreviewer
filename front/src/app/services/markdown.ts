/**
 * Minimal, dependency-free markdown renderer for the split-plan report.
 * Supports the subset emitted by the planner: headings, paragraphs, bold,
 * inline code, bullet lists, horizontal rules, blockquotes.
 *
 * Contents are escaped before rendering so model-generated text can never
 * inject raw HTML.
 */

export function renderMarkdown(markdown: string): string {
  const lines = markdown.split('\n');
  const out: string[] = [];
  let inList = false;
  let inQuote = false;

  const close = () => {
    if (inList) {
      out.push('</ul>');
      inList = false;
    }
    if (inQuote) {
      out.push('</blockquote>');
      inQuote = false;
    }
  };

  for (const raw of lines) {
    const line = raw.trimEnd();
    const trimmed = line.trim();

    if (!trimmed) {
      close();
      continue;
    }
    if (/^---+\s*$/.test(trimmed)) {
      close();
      out.push('<hr>');
      continue;
    }
    const fence = trimmed.match(/^```\s*$/);
    if (fence) {
      close();
      out.push('<pre class="code-fence"></pre>');
      continue;
    }
    const heading = trimmed.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      close();
      const level = heading[1].length;
      out.push(`<h${level}>${inline(heading[2])}</h${level}>`);
      continue;
    }
    const quote = trimmed.match(/^>\s?(.*)$/);
    if (quote) {
      if (!inQuote) {
        out.push('<blockquote>');
        inQuote = true;
      }
      out.push(inline(quote[1]));
      continue;
    }
    const bullet = trimmed.match(/^[-*]\s+(.*)$/);
    if (bullet) {
      if (!inList) {
        out.push('<ul>');
        inList = true;
      }
      out.push(`<li>${inline(bullet[1])}</li>`);
      continue;
    }
    const ordered = trimmed.match(/^(\d+)[.)]\s+(.*)$/);
    if (ordered) {
      if (!inList) {
        out.push('<ol>');
        inList = true;
      }
      out.push(`<li>${inline(ordered[2])}</li>`);
      continue;
    }
    close();
    out.push(`<p>${inline(trimmed)}</p>`);
  }
  close();
  return out.join('\n');
}

function inline(text: string): string {
  const escaped = escapeHtml(text);
  return escaped
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/\*([^*]+)\*/g, '<em>$1</em>');
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}