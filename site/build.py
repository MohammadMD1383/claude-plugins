#!/usr/bin/env python3
"""Build the marketplace website for GitHub Pages. Python 3.9+, stdlib only.

    python3 site/build.py                       # -> _site/, default base URL
    python3 site/build.py --base-url https://example.com --out /tmp/site
    python3 site/build.py --og-spec             # social image specs, for tools/render-images.cjs

The plugin list comes from .claude-plugin/marketplace.json, so adding a plugin
there adds it to the home page, /plugins/, the footer, llms.txt and the
sitemap. Each plugin's page is generated from its README.md and manifests; an
optional sidecar site/pages/plugins/<name>.html (meta "plugin": "<name>") adds a
search title, card highlights and extra sections such as an FAQ. The build also
regenerates (--sync) or checks the plugin table in the root README.md, and
fails when the repo and the site disagree (see check_repo).

Pages in site/pages/**/*.html are a `<!--meta {json} -->` header followed by the
body. The build wraps each in the shared layout (head, SEO tags, JSON-LD,
header, footer, inlined CSS), writes a Markdown twin next to it for agents,
generates sitemap.xml, robots.txt, llms.txt and llms-full.txt, and checks every
internal link and anchor. It exits non-zero on any error.
"""

import argparse
import datetime as dt
import html
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

import markdown

SITE = Path(__file__).resolve().parent
ROOT = SITE.parent
OWNER_REPO = "MohammadMD1383/claude-plugins"
REPO = f"https://github.com/{OWNER_REPO}"
RAW = f"https://raw.githubusercontent.com/{OWNER_REPO}/main"
AUTHOR = "MohammadMD1383"
AUTHOR_URL = "https://github.com/MohammadMD1383"
SITE_NAME = "Claude Code plugins by MohammadMD1383"
DEFAULT_BASE_URL = "https://mohammadmd1383.github.io/claude-plugins"


# --------------------------------------------------------------------------- data


def load_market():
    market = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text())
    plugins = {}
    for entry in market["plugins"]:
        src = (ROOT / entry["source"]).resolve()
        manifest = json.loads((src / ".claude-plugin/plugin.json").read_text())
        plugins[entry["name"]] = {
            **manifest,
            "name": entry["name"],
            "summary": entry.get("description") or manifest.get("description", ""),
            "description": manifest.get("description") or entry.get("description", ""),
            "category": entry.get("category") or manifest.get("category") or "other",
            "repository": manifest.get("repository") or f"{REPO}/tree/main/{src.relative_to(ROOT).as_posix()}",
            "dir": src,
            "rel": src.relative_to(ROOT).as_posix(),
        }
    return market, plugins


def git(*args):
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def git_date(paths, first=False):
    """Last (or first) commit date, YYYY-MM-DD, touching any of paths; None if untracked."""
    out = git("log", "--format=%cs", "--", *map(str, paths)).splitlines()
    return (out[-1] if first else out[0]) if out else None


def human_date(iso):
    d = dt.date.fromisoformat(iso)
    return f"{d.strftime('%B')} {d.day}, {d.year}"


def clip(text, limit=158):
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[:limit - 1].rsplit(" ", 1)[0].rstrip(",;:.") + "…"


def title_case(slug):
    return slug.replace("-", " ").replace("_", " ").title()


def join_names(names):
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


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
        meta.setdefault("sources", [])
        pages.append(meta)
    return pages


def readme_link(plugin, plugins, ctx):
    """Rewrite a README link: other plugins -> their site page, repo files -> GitHub."""
    def link(url, image=False):
        if re.match(r"^[a-z][a-z0-9+.-]*:|^#|^//", url, re.I):
            return url
        path, _, frag = url.partition("#")
        target = posixpath.normpath(posixpath.join(plugin["rel"], path)) if path else plugin["rel"]
        link.checked.append((url, target))
        for other in plugins.values():
            if target.rstrip("/") == other["rel"] or target == other["rel"] + "/README.md":
                return f"{ctx['base_path']}/plugins/{other['name']}/" + (f"#{frag}" if frag else "")
        if image:
            return f"{RAW}/{target}"
        kind = "tree" if (ROOT / target).is_dir() else "blob"
        return f"{REPO}/{kind}/main/{target}" + (f"#{frag}" if frag else "")
    link.checked = []
    return link


def plugin_page(plugin, plugins, ctx, extra=None):
    """A plugin's page: facts and install from its manifests, then its README.md.

    `extra` is an optional site/pages/plugins/<name>.html sidecar. Its meta
    overrides the generated title, h1, description, etc., and its body (FAQ and
    the like) goes after the README, so nothing the README says is duplicated.
    """
    name = plugin["name"]
    extra = extra or {}
    readme = plugin["dir"] / "README.md"
    md = readme.read_text() if readme.exists() else ""
    md = re.sub(r"\A\s*#\s+[^\n]*\n", "", md)  # the page supplies its own h1
    link = readme_link(plugin, plugins, ctx)
    body, headings = markdown.render(md, link)
    extra_body = extra.get("body", "")
    headings += [(2, re.sub(r"<[^>]+>", "", t), hid) for hid, t in re.findall(r'<h2 id="([^"]+)">(.*?)</h2>', extra_body)]
    toc = "".join(f'<li><a href="#{hid}">{html.escape(text)}</a></li>' for level, text, hid in headings if level == 2)
    page = {
        "title": f"{name}: Claude Code Plugin"[:60],
        "h1": f"{name}: a Claude Code plugin",
        "description": clip(plugin["description"]),
        "lede": plugin["description"],
        "published": git_date([readme], first=True) or dt.date.today().isoformat(),
        **{k: v for k, v in extra.items() if k not in ("body", "src", "sources")},
        "path": f"/plugins/{name}/",
        "type": "plugin",
        "plugin": name,
        "og": name,
        "breadcrumb": [["Plugins", "/plugins/"], [name, None]],
        "src": extra.get("src", readme),
        "sources": [readme.relative_to(ROOT).as_posix(), f"{plugin['rel']}/.claude-plugin/plugin.json",
                    *extra.get("sources", [])],
        "readme_links": link.checked,
    }
    page["body"] = f"""<article class="prose article">
<h1>{html.escape(page["h1"])}</h1>
<!--stamp-->
<p class="lede">{html.escape(page["lede"])}</p>
{{{{facts:{name}}}}}
{"" if any(hid == "install" for _, _, hid in headings) else f"{{{{install:{name}}}}}"}
{f'<nav class="toc" aria-label="On this page"><h2>On this page</h2><ol>{toc}</ol></nav>' if len(headings) > 2 else ""}
<div class="readme">
{body}
</div>
{extra_body}
{{{{related:{name}}}}}
</article>
"""
    return page


# --------------------------------------------------------------------------- components


def plugin_url(ctx, name):
    return f"{ctx['base_path']}/plugins/{name}/"


def install_block(ctx, names, with_marketplace=True):
    lines = [f"/plugin marketplace add {OWNER_REPO}"] if with_marketplace else []
    lines += [f"/plugin install {n}@{ctx['market_name']}" for n in names]
    return f'<pre data-lang="text"><code>{html.escape(chr(10).join(lines))}</code></pre>'


def plugin_card(ctx, p, heading="h3"):
    highlights = ctx["plugin_pages"].get(p["name"], {}).get("highlights", [])
    items = "".join(f"<li>{html.escape(h)}</li>" for h in highlights)
    return f"""<article class="card">
<p class="card-meta"><span class="cat">{html.escape(title_case(p["category"]))}</span><span class="ver">v{html.escape(p["version"])}</span></p>
<{heading}><a href="{plugin_url(ctx, p["name"])}">{html.escape(p["name"])}</a></{heading}>
<p>{html.escape(p["summary"])}</p>
{f"<ul>{items}</ul>" if items else ""}
<pre data-lang="text"><code>/plugin install {html.escape(p["name"])}@{ctx["market_name"]}</code></pre>
</article>"""


def plugin_cards(ctx, by_category=False):
    plugins = list(ctx["plugins"].values())
    if not by_category:
        return '<div class="cards">' + "".join(plugin_card(ctx, p) for p in plugins) + "</div>"
    cats = {}
    for p in plugins:
        cats.setdefault(p["category"], []).append(p)
    if len(cats) == 1:
        return '<div class="cards">' + "".join(plugin_card(ctx, p, "h2") for p in plugins) + "</div>"
    out = []
    for cat in sorted(cats):
        cid = "category-" + re.sub(r"[^a-z0-9]+", "-", cat.lower())
        out.append(f'<h2 id="{cid}">{html.escape(title_case(cat))}</h2><div class="cards">'
                   + "".join(plugin_card(ctx, p) for p in cats[cat]) + "</div>")
    return "".join(out)


def guide_list(ctx, exclude=None):
    items = [f'<li><a href="{ctx["base_path"]}{g["path"]}">{html.escape(g.get("h1", g["title"]))}'
             f'<span>{html.escape(g["description"])}</span></a></li>'
             for g in ctx["guides"] if g["path"] != exclude]
    return f'<ul class="related">{"".join(items)}</ul>' if items else ""


def facts(ctx, name):
    p = ctx["plugins"][name]
    rows = [("Version", p["version"]), ("License", p.get("license", "MIT")), ("Category", title_case(p["category"]))]
    page = ctx["plugin_pages"].get(name, {})
    if page.get("requirements"):
        rows.append(("Needs", page["requirements"]))
    return ('<ul class="facts" aria-label="Plugin facts">'
            + "".join(f"<li><b>{k}</b> <span>{html.escape(v)}</span></li>" for k, v in rows) + "</ul>")


def related(ctx, name):
    others = [p for p in ctx["plugins"].values() if p["name"] != name]
    same = [p for p in others if p["category"] == ctx["plugins"][name]["category"]]
    picks = (same + [p for p in others if p not in same])[:3]
    items = [f'<li><a href="{plugin_url(ctx, p["name"])}">{html.escape(p["name"])}<span>{html.escape(clip(p["summary"], 110))}</span></a></li>'
             for p in picks]
    for path in ctx["plugin_pages"].get(name, {}).get("related_guides", []):
        g = next((g for g in ctx["guides"] if g["path"] == path), None)
        if not g:
            sys.exit(f"plugin {name}: related_guides names {path}, which is not a guide")
        items.append(f'<li><a href="{ctx["base_path"]}{path}">{html.escape(g.get("nav", g["title"]))}'
                     f'<span>{html.escape(clip(g["description"], 110))}</span></a></li>')
    items.append(f'<li><a href="{html.escape(ctx["plugins"][name]["repository"])}" rel="noopener">Source code'
                 f'<span>{html.escape(name)} on GitHub: code, tests and README.</span></a></li>')
    return f'<h2 id="related">Related</h2><ul class="related">{"".join(items)}</ul>'


def hero_terminal(ctx):
    names = list(ctx["plugins"])
    first = names[0]
    rows = [
        f'<span class="d">$</span> claude plugin marketplace add {OWNER_REPO}',
        f'<span class="g">Successfully added marketplace: {ctx["market_name"]}</span>',
        f'<span class="d">$</span> claude plugin install {first}@{ctx["market_name"]}',
        f'<span class="g">Successfully installed plugin: {first}@{ctx["market_name"]} (scope: user)</span>',
        "",
        f'<span class="d"># {len(names)} plugin{"s" if len(names) != 1 else ""} available:</span>',
    ]
    shown = names[:6]
    rows.append('<span class="d">#</span>   ' + '<span class="d">,</span> '.join(f'<span class="c">{html.escape(n)}</span>' for n in shown)
                + (f' <span class="d">and {len(names) - len(shown)} more</span>' if len(names) > len(shown) else ""))
    return ('<figure class="term" aria-label="Installing a plugin from this marketplace in a terminal">'
            '<div class="term-top" aria-hidden="true"><i></i><i></i><i></i><span>~ — zsh</span></div>'
            f'<div class="term-body"><pre>{chr(10).join(rows)}</pre></div></figure>')


def expand(text, ctx, page):
    def sub(m):
        key, _, arg = m.group(1).partition(":")
        if key == "v":
            return html.escape(ctx["plugins"][arg]["version"])
        if key == "desc":
            return html.escape(ctx["plugins"][arg]["description"])
        if key == "facts":
            return facts(ctx, arg)
        if key == "install":
            return install_block(ctx, [arg])
        if key == "related":
            return related(ctx, arg)
        if key == "plugin_cards":
            return plugin_cards(ctx, by_category=arg == "by-category")
        if key == "guide_list":
            return guide_list(ctx, exclude=page["path"])
        if key == "hero_terminal":
            return hero_terminal(ctx)
        if key in ctx["vars"]:
            return ctx["vars"][key]
        sys.exit(f"{page['path']}: unknown placeholder {{{{{m.group(1)}}}}}")
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
               "name": SITE_NAME, "description": ctx["market_desc"], "inLanguage": "en",
               "publisher": {"@id": person["@id"]}}
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
        for i, (name, href) in enumerate(crumbs, start=2):
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
        graph.append({k: v for k, v in app.items() if v})
        webpage["mainEntity"] = {"@id": app["@id"]}
        code = {
            "@type": "SoftwareSourceCode", "name": p["name"], "codeRepository": p["repository"],
            "programmingLanguage": page.get("language"), "license": "https://opensource.org/licenses/MIT",
            "author": {"@id": person["@id"]}, "targetProduct": {"@id": app["@id"]},
        }
        graph.append({k: v for k, v in code.items() if v})
    elif kind == "article":
        graph.append({
            "@type": "TechArticle", "@id": url + "#article", "headline": page["h1"],
            "description": page["description"], "image": image, "url": url,
            "mainEntityOfPage": {"@id": webpage["@id"]}, "inLanguage": "en",
            "author": {"@id": person["@id"]}, "publisher": {"@id": person["@id"]},
            "datePublished": page["published"], "dateModified": page["modified"],
            "about": page.get("about", []), "proficiencyLevel": "Beginner",
        })
    elif kind in ("home", "collection"):
        webpage["@type"] = ["WebPage", "CollectionPage"]
        if page.get("list") == "guides":
            entries = [(g.get("h1", g["title"]), ctx["base_url"] + g["path"]) for g in ctx["guides"]]
        else:
            entries = [(n, ctx["base_url"] + f"/plugins/{n}/") for n in ctx["plugins"]]
        webpage["mainEntity"] = {"@type": "ItemList", "itemListElement": [
            {"@type": "ListItem", "position": i, "name": name, "url": link}
            for i, (name, link) in enumerate(entries, start=1)]}

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

NAV = [("Plugins", "/plugins/"), ("Guides", "/guides/"), ("Install", "/guides/install-claude-code-plugins/")]


def nav_html(page, base):
    # The longest nav path that prefixes this page's path is the current section.
    current = max((h for _, h in NAV if page["path"].startswith(h)), key=len, default=None)
    items = []
    for label, href in NAV:
        cur = ' aria-current="page"' if href == current else ""
        items.append(f'<li><a href="{base}{href}"{cur}>{label}</a></li>')
    items.append(f'<li><a class="gh" href="{REPO}" rel="noopener">{ICON_GITHUB}<span>GitHub</span></a></li>')
    return "".join(items)


def footer_html(ctx):
    base = ctx["base_path"]
    plugins = list(ctx["plugins"])
    links = "".join(f'<li><a href="{base}/plugins/{n}/">{html.escape(n)}</a></li>' for n in plugins[:8])
    links += f'<li><a href="{base}/plugins/">All plugins</a></li>'
    guides = "".join(f'<li><a href="{base}{g["path"]}">{html.escape(g.get("nav", g.get("h1", g["title"])))}</a></li>'
                     for g in ctx["guides"][:6])
    return f"""<footer class="site">
<div class="wrap foot">
<div>
<p class="brand-sm">{LOGO}<span>claude-plugins</span></p>
<p>A free, open-source Claude Code plugin marketplace, MIT licensed. Independent community project; not affiliated with or endorsed by Anthropic.</p>
</div>
<nav aria-label="Plugins">
<h2>Plugins</h2>
<ul>{links}</ul>
</nav>
<nav aria-label="Guides">
<h2>Guides</h2>
<ul>{guides}</ul>
</nav>
<nav aria-label="Project">
<h2>Project</h2>
<ul><li><a href="{REPO}" rel="noopener">Source on GitHub</a></li><li><a href="{REPO}/issues" rel="noopener">Report an issue</a></li><li><a href="{base}/llms.txt">llms.txt</a></li></ul>
</nav>
</div>
<p class="wrap copy-line">© {dt.date.today().year} {AUTHOR}. Content under the MIT license.</p>
</footer>"""


def render(page, ctx):
    base, base_url = ctx["base_path"], ctx["base_url"]
    url = base_url + page["path"]
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
            parts.append(f'<li><a href="{base}{href}">{html.escape(name)}</a></li>' if href
                         else f'<li aria-current="page">{html.escape(name)}</li>')
        crumbs = f'<nav class="crumbs wrap" aria-label="Breadcrumb"><ol>{"".join(parts)}</ol></nav>'

    stamp = ""
    if page.get("type") in ("plugin", "article"):
        stamp = (f'<p class="stamp">By <a href="{AUTHOR_URL}" rel="author noopener">{AUTHOR}</a> · '
                 f'Updated <time datetime="{page["modified"]}">{human_date(page["modified"])}</time></p>')
    body = page["body"].replace("<!--stamp-->", stamp)

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
{footer_html(ctx)}
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
    css = re.sub(r"\s*([{};,>])\s*", r"\1", css)
    css = re.sub(r":\s+", ":", css)  # never strip the space *before* ":", it is a descendant combinator
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
        self.li_prefix = ""

    def flush(self, prefix=""):
        text = re.sub(r"[ \t\n]+", " ", "".join(self.buf)).strip() if not self.pre else "".join(self.buf)
        self.buf = []
        if text:
            # The first block inside a list item carries its marker; later ones are indented under it.
            if self.li_prefix:
                prefix, self.li_prefix = self.li_prefix + prefix, ""
            elif self.lists:
                prefix = "  " * len(self.lists) + prefix
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
            kind = self.lists[-1] if self.lists else ["ul", 0]
            kind[1] += 1
            marker = f"{kind[1]}." if kind[0] == "ol" else "-"
            self.li_prefix = "  " * (len(self.lists) - 1) + marker + " "
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
            self.flush()
            self.li_prefix = ""
        elif tag in ("ul", "ol"):
            self.flush()
            if self.lists:
                self.lists.pop()
        elif tag == "pre":
            code = "".join(self.buf).strip("\n")
            self.buf = []
            self.pre = False
            if self.li_prefix:  # a code block opening a list item
                self.out.append(self.li_prefix.rstrip())
                self.li_prefix = ""
            pad = "  " * len(self.lists)
            fence = f"```{self.lang}\n{code}\n```"
            self.out.append("\n".join(pad + line if line else line for line in fence.split("\n")))
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
            if "alt" not in img:
                errors.append(f"{rel}: <img> needs alt text")
            elif "width" not in img or "height" not in img:
                warnings.append(f"{rel}: <img src={img.get('src')}> has no width/height (layout shift)")
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


# --------------------------------------------------------------------------- repo consistency

README_START = "<!-- plugins:start"
README_END = "<!-- plugins:end -->"


def readme_table(ctx):
    """The root README's plugin table, generated from marketplace.json."""
    rows = ["| Plugin | Description | Install |", "| --- | --- | --- |"]
    for p in ctx["plugins"].values():
        desc = p["summary"].replace("|", "\\|")
        needs = ctx["plugin_pages"].get(p["name"], {}).get("requirements")
        if needs:
            desc += f" Needs {needs}."
        rows.append(f"| [{p['name']}]({p['rel']}) | {desc} | `/plugin install {p['name']}@{ctx['market_name']}` |")
    return (f"{README_START} (generated from .claude-plugin/marketplace.json: run `python3 site/build.py --sync`, "
            f"don't edit by hand) -->\n" + "\n".join(rows) + f"\n{README_END}")


def sync_readme(ctx, write):
    """Rewrite (or, with write=False, just compare) the table in README.md. Returns True if in sync."""
    path = ROOT / "README.md"
    text = path.read_text()
    m = re.search(re.escape(README_START) + r".*?" + re.escape(README_END), text, re.S)
    if not m:
        sys.exit(f"README.md: add the plugin table markers {README_START} ... --> and {README_END}")
    new = text[:m.start()] + readme_table(ctx) + text[m.end():]
    if write and new != text:
        path.write_text(new)
    return new == text


def check_repo(market, plugins, pages, ctx):
    """Catch the things that go stale when a plugin is added, renamed or changed."""
    errors = []
    ci = (ROOT / ".github/workflows/test.yml").read_text() if (ROOT / ".github/workflows/test.yml").exists() else ""
    for entry in market["plugins"]:
        name, p = entry["name"], plugins[entry["name"]]
        manifest = json.loads((p["dir"] / ".claude-plugin/plugin.json").read_text())
        where = f"plugins/{name}"
        if manifest.get("name") != name:
            errors.append(f"{where}: marketplace.json calls it {name!r} but plugin.json says {manifest.get('name')!r}")
        for field in ("version", "description"):
            if not manifest.get(field):
                errors.append(f"{where}/.claude-plugin/plugin.json: missing {field!r} (the website shows it)")
        if not entry.get("description"):
            errors.append(f".claude-plugin/marketplace.json: {name} has no description (used on plugin cards)")
        if not (p["dir"] / "README.md").exists():
            errors.append(f"{where}: no README.md (its website page is generated from it)")
        if (p["dir"] / "tests").is_dir() and where not in ci:
            errors.append(f"{where}: has tests/ but .github/workflows/test.yml never mentions {where}")
    for page in pages:
        for url, target in page.get("readme_links", []):
            if not (ROOT / target).exists():
                errors.append(f"plugins/{page['plugin']}/README.md: link {url} points at missing {target}")

    # Commands and install ids mentioned anywhere must exist.
    texts = {f"site page {p['path']}": p["body"] for p in pages}
    texts["README.md"] = (ROOT / "README.md").read_text()
    for p in plugins.values():
        if (p["dir"] / "README.md").exists():
            texts[f"plugins/{p['name']}/README.md"] = (p["dir"] / "README.md").read_text()
    for where, text in texts.items():
        for plug, cmd in set(re.findall(r"(?<![\w/.-])/([a-z0-9][a-z0-9-]*):([a-z0-9][a-z0-9-]*)", text)):
            if plug in plugins:
                d = plugins[plug]["dir"]
                if not ((d / "skills" / cmd / "SKILL.md").exists() or (d / "commands" / f"{cmd}.md").exists()):
                    errors.append(f"{where}: mentions /{plug}:{cmd}, but {plug} has no skill or command {cmd!r}")
        for plug in set(re.findall(r"([a-z0-9][a-z0-9-]*)@" + re.escape(ctx["market_name"]) + r"\b", text)):
            if plug not in plugins:
                errors.append(f"{where}: mentions {plug}@{ctx['market_name']}, which is not in marketplace.json")
    if not sync_readme(ctx, write=False):
        errors.append("README.md: the plugin table is out of date; run `python3 site/build.py --sync`")
    return errors


# --------------------------------------------------------------------------- main


def og_specs(pages, ctx):
    """What tools/render-images.cjs draws for each page's 1200x630 social image."""
    specs = {}
    for p in pages:
        slug = p.get("og")
        if not slug or slug in specs:
            continue
        if p.get("type") == "plugin":
            plugin = ctx["plugins"][p["plugin"]]
            specs[slug] = {"kicker": "Claude Code plugin", "title": plugin["name"],
                           "sub": clip(plugin["summary"], 150),
                           "foot": f"/plugin install {plugin['name']}@{ctx['market_name']}"}
        else:
            specs[slug] = {"kicker": p.get("og_kicker", "Guide" if p.get("type") == "article" else "Claude Code plugins"),
                           "title": p.get("og_title", p.get("h1", p["title"])),
                           "sub": clip(p.get("og_sub", p["description"]), 150),
                           "foot": f"/plugin marketplace add {OWNER_REPO}"}
    return [{"slug": k, **v} for k, v in specs.items()]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base-url", default=os.environ.get("SITE_URL") or DEFAULT_BASE_URL,
                    help="absolute site URL, no trailing slash (default: %(default)s)")
    ap.add_argument("--out", default=str(ROOT / "_site"))
    ap.add_argument("--og-spec", action="store_true", help="print social image specs as JSON and exit")
    ap.add_argument("--sync", action="store_true", help="regenerate the plugin table in README.md, then build")
    args = ap.parse_args()

    base_url = args.base_url.rstrip("/")
    base_path = urlparse(base_url).path.rstrip("/")

    market, plugins = load_market()
    pages = load_pages()
    # site/pages/plugins/<name>.html files are sidecars to generated plugin pages, not pages.
    sidecars = {p["plugin"]: p for p in pages if p.get("plugin")}
    pages = [p for p in pages if not p.get("plugin")]
    for name, side in sidecars.items():
        if name not in plugins:
            sys.exit(f"{side['src']}: describes plugin {name!r}, which is not in marketplace.json")
        side["sources"] = [side["src"].relative_to(ROOT).as_posix()]
    plugin_pages = sidecars
    ctx = {
        "base_url": base_url, "base_path": base_path, "plugins": plugins, "plugin_pages": plugin_pages,
        "market_name": market["name"],
        "market_desc": market.get("metadata", {}).get("description", SITE_NAME),
        "css": minify_css((SITE / "assets/style.css").read_text()),
        "verify": {
            "google-site-verification": os.environ.get("GOOGLE_SITE_VERIFICATION", "").strip(),
            "msvalidate.01": os.environ.get("BING_SITE_VERIFICATION", "").strip(),
        },
        "vars": {"base": base_path, "repo": REPO, "market": market["name"], "owner_repo": OWNER_REPO,
                 "plugin_count": str(len(plugins)), "plugin_names": join_names(list(plugins)),
                 "plugin_word": "plugin" if len(plugins) == 1 else "plugins"},
    }
    ctx["guides"] = sorted((p for p in pages if p.get("type") == "article"), key=lambda p: p.get("order", 50))
    for name, plugin in plugins.items():
        pages.append(plugin_page(plugin, plugins, ctx, sidecars.get(name)))
    ctx["guides"] = sorted((p for p in pages if p.get("type") == "article"), key=lambda p: p.get("order", 50))

    if args.og_spec:
        print(json.dumps(og_specs(pages, ctx), indent=1))
        return
    if args.sync and not sync_readme(ctx, write=True):
        print("updated the plugin table in README.md")

    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    today = dt.date.today().isoformat()
    warnings = []
    for p in pages:
        sources = [p["src"], *(ROOT / s for s in p["sources"])]
        p["modified"] = max(filter(None, [git_date(sources), p["published"]]))
        dirty = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", *map(str, sources)], cwd=ROOT)
        if dirty.returncode == 1:
            p["modified"] = today  # uncommitted edits: treat as changed today
        slug = p.get("og", "default")
        if not (SITE / "assets/og" / f"{slug}.jpg").exists():
            if p.get("type") != "404":
                warnings.append(f"{p['path']}: no social image assets/og/{slug}.jpg, using the default "
                                "(run site/tools/render-images.cjs)")
            slug = "default"
        p["og_image"] = f"/assets/og/{slug}.jpg"  # relative to the base URL

    # Expand placeholders first: FAQ extraction and the Markdown twins read the final body.
    for p in pages:
        p["body"] = expand(p["body"], ctx, p)
    repo_errors = check_repo(market, plugins, pages, ctx)

    # Static assets and verbatim files (e.g. a googleXXXX.html verification file).
    shutil.copytree(SITE / "assets", out / "assets", ignore=shutil.ignore_patterns("style.css"))
    for f in (SITE / "static").iterdir():
        if f.name != ".gitkeep":
            (shutil.copytree if f.is_dir() else shutil.copy2)(f, out / f.name)

    twins = {}
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
        twins[p["path"]] = md

    order = {"home": 0, "collection": 1, "plugin": 2, "article": 3}
    listed = sorted((p for p in pages if p.get("type") != "404"),
                    key=lambda p: (order.get(p["type"], 9), list(plugins).index(p["plugin"]) if p.get("plugin") else 0,
                                   p.get("order", 50), p["path"]))

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
            f"> {ctx['market_desc']}: a free, open-source (MIT) plugin marketplace for Claude Code, "
            f"Anthropic's agentic coding tool. It currently lists {len(plugins)} {ctx['vars']['plugin_word']} "
            f"({ctx['vars']['plugin_names']}). Independent project, not affiliated with Anthropic.",
            "",
            f"Install: `/plugin marketplace add {OWNER_REPO}` once, then "
            f"`/plugin install <plugin>@{market['name']}`. Source: {REPO}",
            "", "Every page is also available as Markdown by appending `index.md` to its URL.", ""]
    for heading, kind in [("Plugins", "plugin"), ("Guides", "article"), ("Site", ("home", "collection"))]:
        llms += [f"## {heading}", ""]
        for p in listed:
            if p["type"] == kind or p["type"] in kind:
                llms.append(f"- [{p.get('h1', p['title'])}]({base_url}{p['path']}index.md): {p['description']}")
        llms.append("")
    llms += ["## Optional", "",
             f"- [Full text of every page]({base_url}/llms-full.txt): all pages concatenated as Markdown",
             f"- [Marketplace catalog]({REPO}/blob/main/.claude-plugin/marketplace.json): machine-readable plugin list",
             f"- [Source repository]({REPO}): plugin code, tests and READMEs", ""]
    (out / "llms.txt").write_text("\n".join(llms))
    (out / "llms-full.txt").write_text(
        f"# {SITE_NAME}: full text\n\n" + "\n\n---\n\n".join(twins[p["path"]] for p in listed))

    # IndexNow (Bing, Yandex, and the search behind several AI assistants): publish the key file.
    indexnow = os.environ.get("INDEXNOW_KEY", "").strip()
    if indexnow:
        if not re.fullmatch(r"[A-Za-z0-9-]{8,128}", indexnow):
            sys.exit("INDEXNOW_KEY must be 8-128 letters, digits or dashes")
        (out / f"{indexnow}.txt").write_text(indexnow)

    errors, more = check_site(out, ctx, pages)
    errors = repo_errors + errors
    for w in warnings + more:
        print("warning:", w)
    for e in errors:
        print("error:", e)
    if errors:
        sys.exit(1)
    print(f"built {len(pages)} pages ({len(plugins)} plugins) into {out} for {base_url}/")


if __name__ == "__main__":
    main()
