"""Small Markdown -> HTML renderer for plugin READMEs. Stdlib only.

Covers what GitHub READMEs in this repo use: ATX headings (with GitHub-style
ids), paragraphs, fenced code, pipe tables, nested lists, blockquotes, rules,
and inline code, bold, italic, links, images and autolinks. Raw HTML is
escaped, not passed through. Links are handed to a `link` callback so the
caller can rewrite repo-relative paths.
"""

import html
import re

FENCE = re.compile(r"^(\s*)(`{3,}|~{3,})\s*([\w+-]*)")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
LIST_ITEM = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
HR = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")


def slugify(text, seen):
    """GitHub-compatible heading id, de-duplicated with -1, -2..."""
    base = re.sub(r"[^\w\- ]", "", text.lower()).strip().replace(" ", "-")
    slug, n = base, 0
    while slug in seen:
        n += 1
        slug = f"{base}-{n}"
    seen.add(slug)
    return slug


def plain(text):
    """Inline Markdown reduced to plain text (for heading ids and summaries)."""
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    return re.sub(r"[`*_]", "", text).strip()


class Renderer:
    def __init__(self, link=lambda url, image=False: url):
        self.link = link
        self.ids = set()
        self.headings = []  # (level, text, id)

    # ------------------------------------------------------------ inline

    def inline(self, text):
        codes = []

        def stash(m):
            codes.append(f"<code>{html.escape(m.group(2))}</code>")
            return f"\x00{len(codes) - 1}\x00"

        text = re.sub(r"(`+)(.+?)\1", stash, text)
        text = html.escape(text, quote=False)

        def image(m):
            src = html.escape(self.link(html.unescape(m.group(2)), image=True))
            return f'<img src="{src}" alt="{m.group(1)}" loading="lazy" decoding="async">'

        def anchor(m):
            href = html.escape(self.link(html.unescape(m.group(2))))
            ext = ' rel="noopener"' if href.startswith("http") else ""
            return f'<a href="{href}"{ext}>{m.group(1)}</a>'

        text = re.sub(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+&quot;[^&]*&quot;)?\)", image, text)
        text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)(?:\s+&quot;[^&]*&quot;)?\)", anchor, text)
        text = re.sub(r"(?<![\"'=>])\b(https?://[^\s<)]+[^\s<).,;:!?])", r'<a href="\1" rel="noopener">\1</a>', text)
        text = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", r"<strong>\1</strong>", text)
        text = re.sub(r"(?<![\w*])\*(?=\S)(.+?)(?<=\S)\*(?![\w*])", r"<em>\1</em>", text)
        text = re.sub(r"(?<![\w])_(?=\S)(.+?)(?<=\S)_(?![\w])", r"<em>\1</em>", text)
        text = text.replace("  \n", "<br>\n")
        return re.sub(r"\x00(\d+)\x00", lambda m: codes[int(m.group(1))], text)

    # ------------------------------------------------------------ blocks

    def render(self, md):
        return self.blocks(md.replace("\r\n", "\n").split("\n"))

    def blocks(self, lines):
        out, i = [], 0
        while i < len(lines):
            line = lines[i]
            if not line.strip():
                i += 1
                continue
            m = FENCE.match(line)
            if m:
                fence, lang, body = m.group(2), m.group(3), []
                i += 1
                while i < len(lines) and not lines[i].strip().startswith(fence):
                    body.append(lines[i])
                    i += 1
                i += 1
                attr = f' data-lang="{lang}"' if lang else ""
                out.append(f"<pre{attr}><code>{html.escape(chr(10).join(body))}</code></pre>")
                continue
            m = HEADING.match(line)
            if m:
                level, text = len(m.group(1)), m.group(2)
                hid = slugify(plain(text), self.ids)
                self.headings.append((level, plain(text), hid))
                out.append(f'<h{level} id="{hid}">{self.inline(text)}</h{level}>')
                i += 1
                continue
            if HR.match(line):
                out.append("<hr>")
                i += 1
                continue
            if "|" in line and i + 1 < len(lines) and TABLE_SEP.match(lines[i + 1]):
                rows = [line]
                i += 2
                while i < len(lines) and "|" in lines[i] and lines[i].strip():
                    rows.append(lines[i])
                    i += 1
                out.append(self.table(rows))
                continue
            if line.lstrip().startswith(">"):
                quote = []
                while i < len(lines) and lines[i].lstrip().startswith(">"):
                    quote.append(re.sub(r"^\s*>\s?", "", lines[i]))
                    i += 1
                out.append(f"<blockquote>{self.blocks(quote)}</blockquote>")
                continue
            if LIST_ITEM.match(line):
                i, block = self.list_block(lines, i)
                out.append(block)
                continue
            para = []
            while i < len(lines) and lines[i].strip() and not (
                    FENCE.match(lines[i]) or HEADING.match(lines[i]) or LIST_ITEM.match(lines[i])
                    or lines[i].lstrip().startswith(">")):
                para.append(lines[i].strip() + ("  " if lines[i].endswith("  ") else ""))
                i += 1
            out.append(f"<p>{self.inline(chr(10).join(para))}</p>")
        return "\n".join(out)

    def list_block(self, lines, i):
        first = LIST_ITEM.match(lines[i])
        indent = len(first.group(1))
        ordered = first.group(2)[0].isdigit()
        items = []
        while i < len(lines):
            m = LIST_ITEM.match(lines[i])
            if not m or len(m.group(1)) != indent or m.group(2)[0].isdigit() != ordered:
                break
            body = [m.group(3)]
            i += 1
            # Continuation lines and nested blocks belong to this item.
            while i < len(lines):
                nxt = lines[i]
                if not nxt.strip():
                    if i + 1 < len(lines) and lines[i + 1].startswith(" " * (indent + 2)):
                        body.append("")
                        i += 1
                        continue
                    break
                sub = LIST_ITEM.match(nxt)
                if sub and len(sub.group(1)) <= indent:
                    break
                if not sub and not nxt.startswith(" ") and indent == 0 and not body[-1] == "":
                    body.append(nxt)  # lazy continuation
                    i += 1
                    continue
                body.append(nxt[indent + 2:] if nxt.startswith(" " * (indent + 2)) else nxt.strip())
                i += 1
            items.append(body)
        tag = "ol" if ordered else "ul"
        html_items = []
        for body in items:
            head, rest = [body[0]], []
            for j, b in enumerate(body[1:], start=1):
                if LIST_ITEM.match(b) or FENCE.match(b) or not b.strip():
                    rest = body[j:]
                    break
                head.append(b)
            inner = self.inline(" ".join(h.strip() for h in head))
            if rest:
                inner += "\n" + self.blocks(rest)
            html_items.append(f"<li>{inner}</li>")
        return i, f"<{tag}>{''.join(html_items)}</{tag}>"

    def table(self, rows):
        def cells(row):
            row = row.strip()
            row = row[1:] if row.startswith("|") else row
            row = row[:-1] if row.endswith("|") and not row.endswith("\\|") else row
            return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", row)]
        head = "".join(f"<th>{self.inline(c)}</th>" for c in cells(rows[0]))
        body = "".join("<tr>" + "".join(f"<td>{self.inline(c)}</td>" for c in cells(r)) + "</tr>" for r in rows[1:])
        return f'<div class="table"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def render(md, link=lambda url, image=False: url):
    r = Renderer(link)
    return r.render(md), r.headings
