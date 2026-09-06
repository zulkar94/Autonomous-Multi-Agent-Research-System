/**
 * Minimal Markdown renderer for report bodies.
 *
 * Security: the source string is HTML-escaped *before* any markup is emitted,
 * so nothing in the model output can inject a tag. Only a fixed set of inline
 * constructs is re-introduced afterwards, and link targets are restricted to
 * http/https.
 */

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function safeHref(url: string): string | null {
  return /^https?:\/\//i.test(url) ? url : null;
}

function inline(text: string): string {
  let html = escapeHtml(text);
  html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
  html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_match, label: string, url: string) => {
    const href = safeHref(url.replace(/&amp;/g, "&"));
    return href
      ? `<a href="${escapeHtml(href)}" target="_blank" rel="noopener noreferrer nofollow">${label}</a>`
      : label;
  });
  return html;
}

export function renderMarkdown(source: string): string {
  const out: string[] = [];
  let inList = false;

  const closeList = () => {
    if (inList) {
      out.push("</ul>");
      inList = false;
    }
  };

  for (const rawLine of source.split("\n")) {
    const line = rawLine.trimEnd();

    if (!line.trim()) {
      closeList();
      continue;
    }
    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    if (heading) {
      closeList();
      const level = heading[1].length;
      out.push(`<h${level}>${inline(heading[2])}</h${level}>`);
      continue;
    }
    if (/^[-*]\s+/.test(line)) {
      if (!inList) {
        out.push("<ul>");
        inList = true;
      }
      out.push(`<li>${inline(line.replace(/^[-*]\s+/, ""))}</li>`);
      continue;
    }
    if (/^---+$/.test(line)) {
      closeList();
      out.push("<hr />");
      continue;
    }
    closeList();
    out.push(`<p>${inline(line)}</p>`);
  }
  closeList();
  return out.join("\n");
}
