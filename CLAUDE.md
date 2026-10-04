# claude-plugins

A Claude Code plugin marketplace (`.claude-plugin/marketplace.json`) with one plugin per `plugins/<name>/`, plus a website generated from the repo by `site/build.py` and deployed to GitHub Pages.

## Keep the repo and the website in sync

The website is generated from the repo, so most changes need no site edits. Do these, though:

- **After any change to a plugin, the marketplace, a README or `site/`**, run `python3 site/build.py --sync` and fix every `error:` it prints before committing. It regenerates the plugin table in `README.md` (never edit the table between the `plugins:start`/`plugins:end` markers by hand) and checks names, versions, README links, `/<plugin>:<command>` and `<plugin>@mohammadmd-plugins` mentions, CI coverage of `tests/`, and every site link and anchor. CI runs the same build.
- **Adding a plugin:** follow "Adding a plugin" in `README.md`. Its page, card, sitemap entry and `llms.txt` line appear automatically.
- **Renaming or removing a skill, command, option or heading:** search `site/pages/` and every `README.md` for the old name. The build catches commands and anchors, but not prose.
- **Bumping a version:** change `plugin.json` only. The site and README read it from there.
- **Plugin docs belong in the plugin's `README.md`**, not in `site/pages/plugins/<name>.html`. Sidecars only hold what a README shouldn't: a search title and description, card highlights, an FAQ.
- **Guides** (`site/pages/guides/`) describe Claude Code itself. When you change behavior a guide describes, update the guide too; the build can't check prose.

## Tests

- Manifests and website: `python3 site/build.py --out /tmp/site`
- usage-guard: `cd plugins/usage-guard && python3 -m unittest discover -s tests -v`
- usage-bar: `cd plugins/usage-bar && claude plugin validate . && claude plugin test .`
