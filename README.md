# claude-plugins

A collection of [Claude Code](https://claude.com/claude-code) plugins by [MohammadMD1383](https://github.com/MohammadMD1383).

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
| [agy-link](plugins/agy-link) | Use Antigravity-hosted Claude models through a local gateway (OmniRoute, 9router): shows the Antigravity 5-hour and weekly quota above the prompt, and can point Claude Code at the gateway. A mod; needs Claude Code with mods (2.1.287+). Read the terms-of-service warning in its README first. | `/plugin install agy-link@mohammadmd-plugins` |

## Repository layout

```text
.claude-plugin/marketplace.json   marketplace catalog (lists every plugin)
plugins/<name>/                   one self-contained plugin per directory
  .claude-plugin/plugin.json
  README.md, hooks/, skills/, scripts/, tests/ ...
.github/workflows/test.yml        CI
docs/                             research notes and hand-offs (not plugins)
```

## Adding a plugin

1. Create `plugins/<name>/` with `.claude-plugin/plugin.json` and a `README.md`.
2. Add an entry to `.claude-plugin/marketplace.json` with `"source": "./plugins/<name>"`.
3. Add the plugin to the table above and a test job to CI if it has tests.

## License

MIT, see [LICENSE](LICENSE).
