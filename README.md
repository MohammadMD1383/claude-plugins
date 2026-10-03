# claude-plugins

A collection of [Claude Code](https://claude.com/claude-code) plugins by [MohammadMD1383](https://github.com/MohammadMD1383).

**Website:** [mohammadmd1383.github.io/claude-plugins](https://mohammadmd1383.github.io/claude-plugins/) · guides: [Claude Code usage limits](https://mohammadmd1383.github.io/claude-plugins/guides/claude-code-usage-limits/), [installing Claude Code plugins](https://mohammadmd1383.github.io/claude-plugins/guides/install-claude-code-plugins/)

## Install

Add the marketplace once, then install whichever plugins you want:

```text
/plugin marketplace add MohammadMD1383/claude-plugins
/plugin install <plugin>@mohammadmd-plugins
```

## Plugins

| Plugin | Description | Install |
| --- | --- | --- |
| [usage-guard](plugins/usage-guard) | Makes Claude aware of your claude.ai usage limits so it conserves budget, checkpoints and writes a hand-off before being cut off. | `/plugin install usage-guard@mohammadmd-plugins` |
| [usage-bar](plugins/usage-bar) | A minimal bar above the prompt: context window used/total, 5-hour and weekly limits with reset countdowns. Every section is optional. A mod; needs Claude Code 2.1.288+. | `/plugin install usage-bar@mohammadmd-plugins` |

## Repository layout

```text
.claude-plugin/marketplace.json   marketplace catalog (lists every plugin)
plugins/<name>/                   one self-contained plugin per directory
  .claude-plugin/plugin.json
  README.md, hooks/, skills/, scripts/, tests/ ...
site/                             the website (GitHub Pages), see below
.github/workflows/test.yml        CI
.github/workflows/pages.yml       builds and deploys the website on push to main
```

## Adding a plugin

1. Create `plugins/<name>/` with `.claude-plugin/plugin.json` and a `README.md`.
2. Add an entry to `.claude-plugin/marketplace.json` with `"source": "./plugins/<name>"`.
3. Add the plugin to the table above and a test job to CI if it has tests.

## Website

`site/` is a dependency-free static site generator (Python 3.9+, stdlib only). Pages live in `site/pages/*.html` as a JSON `<!--meta-->` header plus body HTML; plugin versions are read from each `plugin.json`, so they never go stale.

```bash
python3 site/build.py --base-url http://localhost:8000   # -> _site/
python3 -m http.server -d _site                          # preview at http://localhost:8000/
```

The build wraps every page with canonical, Open Graph and JSON-LD tags, writes a Markdown twin of each page (`index.md`) plus `llms.txt` and `llms-full.txt` for AI agents, generates `sitemap.xml` and `robots.txt`, and fails on broken links or anchors. After changing a page title, re-render the social images with `NODE_PATH="$(npm root -g)" node site/tools/render-images.cjs` (needs Playwright). Repository variables `GOOGLE_SITE_VERIFICATION`, `BING_SITE_VERIFICATION` and `INDEXNOW_KEY` are picked up by the deploy workflow.

## License

MIT, see [LICENSE](LICENSE).
