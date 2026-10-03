#!/usr/bin/env python3
"""Build the static site for GitHub Pages. Python 3.9+, stdlib only.

    python3 site/build.py                       # -> _site/, default base URL
    python3 site/build.py --base-url https://example.com --out /tmp/site

Pages live in site/pages/*.html: a `<!--meta {json} -->` header followed by the
page body. The build wraps each page in the shared layout (head, SEO tags,
JSON-LD, header, footer, inlined CSS), writes a Markdown twin next to it for
agents, then generates sitemap.xml, robots.txt, llms.txt and llms-full.txt and
checks every internal link and anchor. It exits non-zero on any error.
"""

import argparse
import datetime as dt
import html
import json
import os
import re
import shutil
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

SITE = Path(__file__).resolve().parent
ROOT = SITE.parent
REPO = "https://github.com/MohammadMD1383/claude-plugins"
AUTHOR = "MohammadMD1383"
AUTHOR_URL = "https://github.com/MohammadMD1383"
SITE_NAME = "Claude Code plugins by MohammadMD1383"
MARKETPLACE = "mohammadmd-plugins"
DEFAULT_BASE_URL = "https://mohammadmd1383.github.io/claude-plugins"


# --------------------------------------------------------------------------- data


def load_plugins():
    market = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text())
    plugins = {}
    for entry in market["plugins"]:
        src = ROOT / entry["source"]
        manifest = json.loads((src / ".claude-plugin/plugin.json").read_text())
        plugins[entry["name"]] = {**entry, **manifest, "dir": src}
    return plugins


def git_date(paths):
    """Last commit date (YYYY-MM-DD) touching any of paths, or None."""
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%cs", "--", *map(str, paths)],
            cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout.strip()
        return out or None
    except (OSError, subprocess.CalledProcessError):
        return None


def human_date(iso):
    d = dt.date.fromisoformat(iso)
    return f"{d.strftime('%B')} {d.day}, {d.year}"


# --------------------------------------------------------------------------- pages


META_RE = re.compile(r"^\s*<!--meta\s*(\{.*?\})\s*-->\s*", re.S)


def load_pages():
    pages = []
    for f in sorted((SITE / "pages").rglob("*.html")):
        text = f.read_text()
        m = META_RE.match(text)
        if not m:
            sys.exit(f"{f}: missing <!--meta {{...}} --> header")
        meta = json.loads(m.group(1))
        meta["body"] = text[m.end():]
        meta["src"] = f
        pages.append(meta)
    return pages


def expand(text, ctx):
    def sub(m):
        key, _, arg = m.group(1).partition(":")
        if key == "v":
            return ctx["plugins"][arg]["version"]
        if key == "desc":
            return html.escape(ctx["plugins"][arg]["description"])
        if key in ctx["vars"]:
            return ctx["vars"][key]
        sys.exit(f"unknown placeholder {{{{{m.group(1)}}}}}")
    return re.sub(r"\{\{\s*([\w:-]+)\s*\}\}", sub, text)


def faq_items(body):
    """(question, answer_text) pairs from <div class="qa"><h3>Q</h3>A</div>."""
    items = []
    for q, a in re.findall(r'<div class="qa">\s*<h3[^>]*>(.*?)</h3>(.*?)</div>', body, re.S):
        clean = lambda s: re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s))).strip()
        items.append((clean(q), clean(a)))
    return items


def json_ld(page, ctx):
    url = ctx["base_url"] + page["path"]
    site_url = ctx["base_url"] + "/"
    person = {"@type": "Person", "@id": AUTHOR_URL + "#person", "name": AUTHOR,
              "url": AUTHOR_URL, "sameAs": [AUTHOR_URL]}
    website = {"@type": "WebSite", "@id": site_url + "#website", "url": site_url,
               "name": SITE_NAME, "inLanguage": "en", "publisher": {"@id": person["@id"]}}
    image = ctx["base_url"] + page["og_image"]
    webpage = {
        "@type": "WebPage", "@id": url + "#webpage", "url": url, "name": page["title"],
        "description": page["description"], "inLanguage": "en",
        "isPartOf": {"@id": website["@id"]}, "primaryImageOfPage": image,
        "datePublished": page["published"], "dateModified": page["modified"],
    }
    graph = [person, website, webpage]

    crumbs = page.get("breadcrumb")
    if crumbs:
        items = [{"@type": "ListItem", "position": 1, "name": "Home", "item": site_url}]
        # Section links like /#plugins are not pages of their own, so they stay out of the trail.
        trail = [(n, h) for n, h in crumbs if not (h and "#" in h)]
        for i, (name, href) in enumerate(trail, start=2):
            items.append({"@type": "ListItem", "position": i, "name": name,
                          "item": ctx["base_url"] + href if href else url})
        graph.append({"@type": "BreadcrumbList", "@id": url + "#breadcrumb", "itemListElement": items})
        webpage["breadcrumb"] = {"@id": url + "#breadcrumb"}

    kind = page.get("type")
    if kind == "plugin":
        p = ctx["plugins"][page["plugin"]]
        app = {
            "@type": "SoftwareApplication", "@id": url + "#software", "name": p["name"],
            "alternateName": p.get("displayName"), "description": p["description"],
            "applicationCategory": "DeveloperApplication",
            "applicationSubCategory": "Claude Code plugin",
            "operatingSystem": page.get("os", "macOS, Linux, Windows"),
            "softwareVersion": p["version"], "license": "https://opensource.org/licenses/MIT",
            "url": url, "downloadUrl": p["repository"], "sameAs": [p["repository"]],
            "keywords": ", ".join(p.get("keywords", [])), "image": image,
            "author": {"@id": person["@id"]}, "isAccessibleForFree": True,
            "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD"},
            "dateModified": page["modified"],
        }
        if page.get("requirements"):
            app["softwareRequirements"] = page["requirements"]
        graph.append(app)
        webpage["mainEntity"] = {"@id": app["@id"]}
        graph.append({
            "@type": "SoftwareSourceCode", "name": p["name"], "codeRepository": p["repository"],
            "programmingLanguage": page.get("language"), "license": "https://opensource.org/licenses/MIT",
            "author": {"@id": person["@id"]}, "targetProduct": {"@id": app["@id"]},
        })
    elif kind == "article":
        graph.append({
            "@type": "TechArticle", "@id": url + "#article", "headline": page["h1"],
            "description": page["description"], "image": image, "url": url,
            "mainEntityOfPage": {"@id": webpage["@id"]}, "inLanguage": "en",
            "author": {"@id": person["@id"]}, "publisher": {"@id": person["@id"]},
            "datePublished": page["published"], "dateModified": page["modified"],
            "about": page.get("about", []), "proficiencyLevel": "Beginner",
        })
    elif kind == "home":
        webpage["@type"] = ["WebPage", "CollectionPage"]
        webpage["mainEntity"] = {
            "@type": "ItemList", "itemListElement": [
                {"@type": "ListItem", "position": i, "url": f"{ctx['base_url']}/plugins/{name}/", "name": name}
                for i, name in enumerate(ctx["plugins"], start=1)
            ],
        }

    faqs = faq_items(page["body"])
    if faqs:
        graph.append({
            "@type": "FAQPage", "@id": url + "#faq",
            "mainEntity": [{"@type": "Question", "name": q,
                            "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in faqs],
        })

    data = json.dumps({"@context": "https://schema.org", "@graph": graph}, ensure_ascii=False, separators=(",", ":"))
    return data.replace("</", "<\\/")


# --------------------------------------------------------------------------- layout


ICON_GITHUB = ('<svg aria-hidden="true" width="18" height="18" viewBox="0 0 16 16"><path fill="currentColor" d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"/></svg>')
LOGO = ('<svg aria-hidden="true" width="28" height="28" viewBox="0 0 32 32"><rect width="32" height="32" rx="8" fill="#17181c"/>'
        '<rect x="7" y="18" width="4" height="7" rx="1.5" fill="#5fbf7f"/><rect x="14" y="13" width="4" height="12" rx="1.5" fill="#e3b341"/>'
        '<rect x="21" y="7" width="4" height="18" rx="1.5" fill="#ef6b5b"/></svg>')

COPY_JS = """document.querySelectorAll('pre>code').forEach(function(c){var b=document.createElement('button');b.type='button';b.className='copy';b.textContent='Copy';b.setAttribute('aria-label','Copy code to clipboard');b.addEventListener('click',function(){navigator.clipboard.writeText(c.innerText.replace(/\\n$/,'')).then(function(){b.textContent='Copied';setTimeout(function(){b.textContent='Copy'},1500)})});c.parentNode.appendChild(b)})"""


def nav_html(page, base):
    links = [("Plugins", f"{base}/#plugins"), ("Usage limits", f"{base}/guides/claude-code-usage-limits/"),
             ("Install guide", f"{base}/guides/install-claude-code-plugins/")]
    items = []
    for label, href in links:
        cur = ' aria-current="page"' if href == base + page["path"] else ""
        items.append(f'<li><a href="{href}"{cur}>{label}</a></li>')
    items.append(f'<li><a class="gh" href="{REPO}" rel="noopener">{ICON_GITHUB}<span>GitHub</span></a></li>')
    return "".join(items)


def render(page, ctx):
    base, base_url = ctx["base_path"], ctx["base_url"]
    url = base_url + page["path"]
    body = expand(page["body"], ctx)
    page["body"] = body
    title = page["title"]
    desc = page["description"]
    og_image = base_url + page["og_image"]
    is_404 = page.get("type") == "404"
    robots = "noindex" if is_404 else "index, follow, max-image-preview:large, max-snippet:-1, max-video-preview:-1"

    head = [
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{html.escape(title)}</title>",
        f'<meta name="description" content="{html.escape(desc)}">',
        f'<meta name="robots" content="{robots}">',
        f'<meta name="author" content="{AUTHOR}">',
        '<meta name="color-scheme" content="light dark">',
        '<meta name="theme-color" content="#faf8f4" media="(prefers-color-scheme: light)">',
        '<meta name="theme-color" content="#0e0f12" media="(prefers-color-scheme: dark)">',
    ]
    if not is_404:
        head += [
            f'<link rel="canonical" href="{url}">',
            f'<link rel="alternate" type="text/markdown" href="{url}index.md" title="Markdown version">',
        ]
    head += [
        f'<link rel="icon" href="{base}/assets/favicon.svg" type="image/svg+xml">',
        f'<link rel="icon" href="{base}/assets/favicon-48.png" type="image/png" sizes="48x48">',
        f'<link rel="apple-touch-icon" href="{base}/assets/apple-touch-icon.png">',
        f'<link rel="sitemap" type="application/xml" href="{base}/sitemap.xml">',
        f'<meta property="og:site_name" content="{SITE_NAME}">',
        f'<meta property="og:type" content="{"article" if page.get("type") == "article" else "website"}">',
        f'<meta property="og:title" content="{html.escape(page.get("og_title", title))}">',
        f'<meta property="og:description" content="{html.escape(desc)}">',
        f'<meta property="og:url" content="{url}">',
        f'<meta property="og:image" content="{og_image}">',
        '<meta property="og:image:width" content="1200">',
        '<meta property="og:image:height" content="630">',
        f'<meta property="og:image:alt" content="{html.escape(page.get("og_title", title))}">',
        '<meta property="og:locale" content="en_US">',
        '<meta name="twitter:card" content="summary_large_image">',
    ]
    if page.get("type") == "article":
        head += [f'<meta property="article:published_time" content="{page["published"]}">',
                 f'<meta property="article:modified_time" content="{page["modified"]}">']
    for name, value in ctx["verify"].items():
        if value:
            head.append(f'<meta name="{name}" content="{html.escape(value)}">')
    if not is_404:
        head.append(f'<script type="application/ld+json">{json_ld(page, ctx)}</script>')
    head.append(f"<style>{ctx['css']}</style>")

    crumbs = ""
    if page.get("breadcrumb"):
        parts = [f'<li><a href="{base}/">Home</a></li>']
        for name, href in page["breadcrumb"]:
            parts.append(f'<li><a href="{base}{href}">{name}</a></li>' if href
                         else f'<li aria-current="page">{name}</li>')
        crumbs = f'<nav class="crumbs wrap" aria-label="Breadcrumb"><ol>{"".join(parts)}</ol></nav>'

    stamp = ""
    if not is_404 and page.get("type") != "home":
        stamp = (f'<p class="stamp">By <a href="{AUTHOR_URL}" rel="author noopener">{AUTHOR}</a> · '
                 f'Updated <time datetime="{page["modified"]}">{human_date(page["modified"])}</time></p>')
    body = body.replace("<!--stamp-->", stamp)

    year = dt.date.today().year
    return f"""<!doctype html>
<html lang="en">
<head>
{chr(10).join(head)}
</head>
<body>
<a class="skip" href="#main">Skip to content</a>
<header class="site">
<div class="wrap bar">
<a class="brand" href="{base}/">{LOGO}<span>claude-plugins</span></a>
<nav aria-label="Main"><ul>{nav_html(page, base)}</ul></nav>
</div>
</header>
{crumbs}
<main id="main">
{body}
</main>
<footer class="site">
<div class="wrap foot">
<div>
<p class="brand-sm">{LOGO}<span>claude-plugins</span></p>
<p>Free, open-source plugins for Claude Code, MIT licensed. Independent community project; not affiliated with or endorsed by Anthropic.</p>
</div>
<nav aria-label="Footer">
<h2>Plugins</h2>
<ul><li><a href="{base}/plugins/usage-guard/">usage-guard</a></li><li><a href="{base}/plugins/usage-bar/">usage-bar</a></li></ul>
</nav>
<nav aria-label="Guides">
<h2>Guides</h2>
<ul><li><a href="{base}/guides/claude-code-usage-limits/">Claude Code usage limits</a></li><li><a href="{base}/guides/install-claude-code-plugins/">Install Claude Code plugins</a></li></ul>
</nav>
<nav aria-label="Project">
<h2>Project</h2>
<ul><li><a href="{REPO}" rel="noopener">Source on GitHub</a></li><li><a href="{REPO}/issues" rel="noopener">Report an issue</a></li><li><a href="{base}/llms.txt">llms.txt</a></li></ul>
</nav>
</div>
<p class="wrap copy-line">© {year} {AUTHOR}. Content under the MIT license.</p>
</footer>
<script>{COPY_JS}</script>
</body>
</html>
"""


def label_cells(doc):
    """Copy each column's <th> text onto its <td>s as data-label, for stacked tables on phones."""
    def table(m):
        t = m.group(0)
        heads = [re.sub(r"<[^>]+>", "", h).strip() for h in re.findall(r"<th[^>]*>(.*?)</th>", t, re.S)]
        def row(r):
            cells = iter(heads)
            return re.sub(r"<td>", lambda _: f'<td data-label="{html.escape(next(cells, ""))}">', r.group(0))
        return re.sub(r"<tr>.*?</tr>", row, t, flags=re.S)
    return re.sub(r"<table>.*?</table>", table, doc, flags=re.S)


def strip_indent(doc):
    """Drop leading indentation outside <pre>; safe because HTML collapses it anyway."""
    parts = re.split(r"(<pre[\s>].*?</pre>)", doc, flags=re.S)
    return "".join(p if p.startswith("<pre") else re.sub(r"\n[ \t]+", "\n", p) for p in parts)


def minify_css(css):
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    css = re.sub(r"\s+", " ", css)
    css = re.sub(r"\s*([{}:;,>])\s*", r"\1", css)
    return css.replace(";}", "}").strip()


# --------------------------------------------------------------------------- markdown twin


class ToMarkdown(HTMLParser):
    """Small HTML -> Markdown converter for the content we write ourselves."""

    BLOCK = {"p", "h1", "h2", "h3", "h4", "li", "pre", "tr", "blockquote", "figcaption", "dt", "dd"}

    def __init__(self, base_url, page_url):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.page_url = page_url
        self.out, self.buf = [], []
        self.lists, self.href = [], []
        self.pre = False
        self.skip = 0
        self.row, self.table, self.cell = None, None, None
        self.quote = False

    def flush(self, prefix=""):
        text = re.sub(r"[ \t\n]+", " ", "".join(self.buf)).strip() if not self.pre else "".join(self.buf)
        self.buf = []
        if text:
            self.out.append(prefix + text)

    def absolute(self, href):
        if href.startswith("#"):
            return self.page_url + href
        if href.startswith(("http://", "https://", "mailto:")):
            return href
        parsed = urlparse(self.base_url)
        return f"{parsed.scheme}://{parsed.netloc}{href}"

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if self.skip or a.get("aria-hidden") == "true" or a.get("data-md") == "skip" or tag in ("script", "style", "svg", "button"):
            self.skip += 1 if tag not in ("br", "img", "hr", "input") else 0
            return
        if tag in ("h1", "h2", "h3", "h4"):
            self.flush()
        elif tag == "p":
            self.flush()
        elif tag in ("ul", "ol"):
            self.flush()
            self.lists.append([tag, 0])
        elif tag == "li":
            self.flush()
        elif tag == "pre":
            self.flush()
            self.pre = True
            self.lang = a.get("data-lang", "")
        elif tag in ("code", "kbd") and not self.pre:
            self.buf.append("`")
        elif tag in ("strong", "b"):
            self.buf.append("**")
        elif tag in ("em", "i"):
            self.buf.append("*")
        elif tag == "a":
            self.href.append(a.get("href", ""))
            self.buf.append("[")
        elif tag == "br":
            self.buf.append("\n" if self.pre else " ")
        elif tag == "table":
            self.flush()
            self.table = []
        elif tag == "tr":
            self.row = []
        elif tag in ("td", "th"):
            self.cell = []
            self.saved, self.buf = self.buf, []
        elif tag == "blockquote":
            self.flush()
            self.quote = True
        elif tag == "img":
            self.buf.append(f"![{a.get('alt', '')}]({self.absolute(a.get('src', ''))})")
        elif tag == "hr":
            self.flush()
            self.out.append("---")

    def handle_endtag(self, tag):
        if self.skip:
            if tag not in ("br", "img", "hr", "input"):
                self.skip -= 1
            return
        if tag in ("h1", "h2", "h3", "h4"):
            self.flush("#" * int(tag[1]) + " ")
        elif tag in ("p", "figcaption", "dt", "dd"):
            self.flush("> " if self.quote else "")
        elif tag == "li":
            depth = len(self.lists) - 1
            kind = self.lists[-1] if self.lists else ["ul", 0]
            kind[1] += 1
            marker = f"{kind[1]}." if kind[0] == "ol" else "-"
            self.flush("  " * depth + marker + " ")
        elif tag in ("ul", "ol"):
            self.flush()
            if self.lists:
                self.lists.pop()
        elif tag == "pre":
            code = "".join(self.buf).strip("\n")
            self.buf = []
            self.pre = False
            self.out.append(f"```{self.lang}\n{code}\n```")
        elif tag in ("code", "kbd") and not self.pre:
            self.buf.append("`")
        elif tag in ("strong", "b"):
            self.buf.append("**")
        elif tag in ("em", "i"):
            self.buf.append("*")
        elif tag == "a":
            href = self.href.pop() if self.href else ""
            self.buf.append(f"]({self.absolute(href)})")
        elif tag in ("td", "th"):
            text = re.sub(r"\s+", " ", "".join(self.buf)).strip().replace("|", "\\|")
            self.buf = self.saved
            self.row.append(text)
        elif tag == "tr":
            self.table.append(self.row)
        elif tag == "table":
            rows = self.table or []
            if rows:
                lines = ["| " + " | ".join(rows[0]) + " |", "|" + " --- |" * len(rows[0])]
                lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
                self.out.append("\n".join(lines))
            self.table = None
        elif tag == "blockquote":
            self.flush("> ")
            self.quote = False

    def handle_data(self, data):
        if not self.skip:
            self.buf.append(data)

    def result(self):
        self.flush()
        item = re.compile(r"\s*(?:-|\d+\.) ")
        parts = []
        for block in self.out:
            # Consecutive list items stay tight; everything else is a paragraph.
            tight = parts and item.match(block) and item.match(parts[-1].rsplit("\n", 1)[-1])
            parts.append(("\n" if tight else "\n\n") + block if parts else block)
        return re.sub(r"\n{3,}", "\n\n", "".join(parts)).strip() + "\n"


def markdown_twin(page, ctx):
    main = page["body"].replace("<!--stamp-->", "")
    url = ctx["base_url"] + page["path"]
    conv = ToMarkdown(ctx["base_url"], url)
    conv.feed(main)
    header = (f"> Canonical: {url}\n> Author: {AUTHOR} ({AUTHOR_URL}) · Updated: {page['modified']}"
              f" · License: MIT\n\n")
    body = conv.result()
    # Put the provenance block right under the H1.
    first, _, rest = body.partition("\n\n")
    return f"{first}\n\n{header}{rest}" if first.startswith("# ") else header + body


# --------------------------------------------------------------------------- checks


class LinkCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links, self.ids, self.h1 = [], set(), 0
        self.imgs = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if "id" in a:
            self.ids.add(a["id"])
        if tag == "a" and "href" in a:
            self.links.append(a["href"])
        if tag == "link" and a.get("rel") in ("icon", "apple-touch-icon", "alternate", "sitemap"):
            self.links.append(a["href"])
        if tag == "h1":
            self.h1 += 1
        if tag == "img":
            self.imgs.append(a)


def check_site(out, ctx, pages):
    errors, warnings = [], []
    parsed = {}
    for f in out.rglob("*.html"):
        c = LinkCollector()
        c.feed(f.read_text())
        parsed[f] = c
    origin = ctx["base_url"]
    for f, c in parsed.items():
        rel = f.relative_to(out)
        if c.h1 != 1:
            errors.append(f"{rel}: expected exactly one <h1>, found {c.h1}")
        for img in c.imgs:
            if "alt" not in img or "width" not in img or "height" not in img:
                errors.append(f"{rel}: <img> needs alt, width and height")
        for href in c.links:
            if href.startswith(origin):
                href = ctx["base_path"] + href[len(origin):]
            if not href.startswith("/") and not href.startswith("#"):
                continue
            path, _, frag = href.partition("#")
            if path:
                if not path.startswith(ctx["base_path"] + "/"):
                    errors.append(f"{rel}: link {href} escapes the base path")
                    continue
                target = out / path[len(ctx["base_path"]) + 1:]
                if path.endswith("/"):
                    target = target / "index.html"
            else:
                target = f
            if not target.exists():
                errors.append(f"{rel}: broken link {href}")
            elif frag and target.suffix == ".html":
                tc = parsed.get(target)
                if tc and frag not in tc.ids:
                    errors.append(f"{rel}: missing anchor #{frag} in {target.relative_to(out)}")
    for p in pages:
        if p.get("type") == "404":
            continue
        if len(p["title"]) > 60:
            warnings.append(f"{p['path']}: title is {len(p['title'])} chars (>60 may be truncated)")
        if not 70 <= len(p["description"]) <= 160:
            warnings.append(f"{p['path']}: description is {len(p['description'])} chars (aim for 70-160)")
    return errors, warnings


# --------------------------------------------------------------------------- main


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base-url", default=os.environ.get("SITE_URL") or DEFAULT_BASE_URL,
                    help="absolute site URL, no trailing slash (default: %(default)s)")
    ap.add_argument("--out", default=str(ROOT / "_site"))
    args = ap.parse_args()

    base_url = args.base_url.rstrip("/")
    base_path = urlparse(base_url).path.rstrip("/")
    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    plugins = load_plugins()
    pages = load_pages()
    ctx = {
        "base_url": base_url, "base_path": base_path, "plugins": plugins,
        "css": minify_css((SITE / "assets/style.css").read_text()),
        "verify": {
            "google-site-verification": os.environ.get("GOOGLE_SITE_VERIFICATION", "").strip(),
            "msvalidate.01": os.environ.get("BING_SITE_VERIFICATION", "").strip(),
        },
        "vars": {"base": base_path, "repo": REPO, "market": MARKETPLACE,
                 "plugin_count": str(len(plugins))},
    }

    today = dt.date.today().isoformat()
    for p in pages:
        sources = [p["src"], *(ROOT / s for s in p.get("sources", []))]
        p["modified"] = max(filter(None, [git_date(sources), p["published"]]))
        dirty = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", *map(str, sources)], cwd=ROOT)
        if dirty.returncode == 1:
            p["modified"] = today  # uncommitted edits: treat as changed today
        slug = p.get("og", "default")
        if not (SITE / "assets/og" / f"{slug}.jpg").exists():
            slug = "default"
        p["og_image"] = f"/assets/og/{slug}.jpg"  # relative to the base URL

    # Static assets and verbatim files (e.g. a googleXXXX.html verification file).
    shutil.copytree(SITE / "assets", out / "assets", ignore=shutil.ignore_patterns("style.css"))
    for f in (SITE / "static").iterdir():
        if f.name != ".gitkeep":
            (shutil.copytree if f.is_dir() else shutil.copy2)(f, out / f.name)

    twins = []
    for p in pages:
        doc = strip_indent(label_cells(render(p, ctx)))
        if p.get("type") == "404":
            (out / "404.html").write_text(doc)
            continue
        dest = out / p["path"].lstrip("/")
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "index.html").write_text(doc)
        md = markdown_twin(p, ctx)
        (dest / "index.md").write_text(md)
        twins.append((p, md))

    order = {"home": 0, "plugin": 1, "article": 2}
    listed = sorted((p for p in pages if p.get("type") != "404"),
                    key=lambda p: (order.get(p["type"], 9), p["path"]))

    sitemap = ['<?xml version="1.0" encoding="UTF-8"?>',
               '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for p in listed:
        sitemap.append(f"<url><loc>{base_url}{p['path']}</loc><lastmod>{p['modified']}</lastmod></url>")
    sitemap.append("</urlset>")
    (out / "sitemap.xml").write_text("\n".join(sitemap) + "\n")

    (out / "robots.txt").write_text(
        "# Everyone, including AI search and assistant crawlers, is welcome.\n"
        "User-agent: *\nAllow: /\n\n"
        f"Sitemap: {base_url}/sitemap.xml\n")

    llms = [f"# {SITE_NAME}", "",
            "> Free, open-source (MIT) plugins for Claude Code, Anthropic's agentic coding CLI. "
            "They make Claude Code aware of claude.ai plan usage limits (5-hour and weekly windows) "
            "and show context-window and limit usage in the terminal. Independent project, not affiliated with Anthropic.",
            "",
            f"Install: `/plugin marketplace add MohammadMD1383/claude-plugins`, then "
            f"`/plugin install <plugin>@{MARKETPLACE}`. Source: {REPO}",
            "", "Every page is also available as Markdown by appending `index.md` to its URL.", ""]
    sections = [("Plugins", "plugin"), ("Guides", "article"), ("Site", "home")]
    for heading, kind in sections:
        llms.append(f"## {heading}")
        llms.append("")
        for p in listed:
            if p["type"] == kind:
                llms.append(f"- [{p.get('h1', p['title'])}]({base_url}{p['path']}index.md): {p['description']}")
        llms.append("")
    llms += ["## Optional", "",
             f"- [Full text of every page]({base_url}/llms-full.txt): all pages concatenated as Markdown",
             f"- [Source repository]({REPO}): plugin code, tests and READMEs", ""]
    (out / "llms.txt").write_text("\n".join(llms))
    (out / "llms-full.txt").write_text(
        f"# {SITE_NAME}: full text\n\n" + "\n\n---\n\n".join(md for _, md in twins))

    # IndexNow (Bing, Yandex, and the search behind several AI assistants): publish the key file.
    indexnow = os.environ.get("INDEXNOW_KEY", "").strip()
    if indexnow:
        if not re.fullmatch(r"[A-Za-z0-9-]{8,128}", indexnow):
            sys.exit("INDEXNOW_KEY must be 8-128 letters, digits or dashes")
        (out / f"{indexnow}.txt").write_text(indexnow)

    errors, warnings = check_site(out, ctx, pages)
    for w in warnings:
        print("warning:", w)
    for e in errors:
        print("error:", e)
    if errors:
        sys.exit(1)
    print(f"built {len(pages)} pages into {out} for {base_url}/")


if __name__ == "__main__":
    main()
