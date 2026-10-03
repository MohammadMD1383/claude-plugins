// Renders the favicon PNGs and the 1200x630 social (Open Graph) images into site/assets.
// Run after changing a page title or the logo, then commit the PNGs:
//   NODE_PATH="$(npm root -g)" node site/tools/render-images.cjs
// Needs Playwright with Chromium (npm i -g playwright && npx playwright install chromium).
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

const ASSETS = path.join(__dirname, "..", "assets");
const LOGO = fs.readFileSync(path.join(ASSETS, "favicon.svg"), "utf8");

const OG = {
  default: {
    kicker: "Claude Code plugins · open source",
    title: "Keep your Claude Code usage limits in view",
    sub: "usage-guard hands off before the limit. usage-bar shows context and limits above the prompt.",
  },
  "usage-guard": {
    kicker: "Claude Code plugin",
    title: "usage-guard",
    sub: "Claude conserves, commits and writes HANDOFF.md before the 5-hour or weekly limit cuts it off.",
  },
  "usage-bar": {
    kicker: "Claude Code plugin",
    title: "usage-bar",
    sub: "Context window and 5-hour and weekly limits with reset countdowns, in one bar above the prompt.",
  },
  "usage-limits": {
    kicker: "Guide",
    title: "Claude Code usage limits, explained",
    sub: "The rolling 5-hour window, the weekly limit, model limits, and how to avoid getting cut off.",
  },
  install: {
    kicker: "Guide",
    title: "How to install Claude Code plugins",
    sub: "Marketplaces, install commands, scopes, configuration, updates and removal.",
  },
};

const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;");

function ogHtml({ kicker, title, sub }) {
  return `<!doctype html><html><head><meta charset="utf-8"><style>
  *{margin:0;box-sizing:border-box}
  body{width:1200px;height:630px;background:#0e0f12;color:#ecebe6;font-family:"DejaVu Sans","Liberation Sans",sans-serif;
    padding:72px 80px;display:flex;flex-direction:column;position:relative;overflow:hidden}
  body:before{content:"";position:absolute;inset:auto -120px -260px auto;width:720px;height:720px;border-radius:50%;
    background:radial-gradient(closest-side,rgba(242,168,101,.22),rgba(242,168,101,0))}
  .top{display:flex;align-items:center;gap:18px;font:600 26px "DejaVu Sans Mono",monospace;color:#a6a9b1}
  .top svg{width:56px;height:56px}
  .kicker{margin-top:58px;font:600 26px "DejaVu Sans Mono",monospace;color:#f2a865;letter-spacing:.02em}
  h1{margin-top:18px;font-size:${title.length > 30 ? 66 : 84}px;line-height:1.06;letter-spacing:-.03em;font-weight:800;max-width:1000px}
  p{margin-top:26px;font-size:31px;line-height:1.4;color:#b9bcc4;max-width:980px}
  .bar{margin-top:auto;font:500 24px "DejaVu Sans Mono",monospace;color:#dcdad3;white-space:pre}
  .d{color:#6d717b}.g{color:#6fd08f}.y{color:#e8b84a}.r{color:#f17767}
  </style></head><body>
  <div class="top">${LOGO}<span>mohammadmd1383.github.io/claude-plugins</span></div>
  <div class="kicker">${esc(kicker)}</div>
  <h1>${esc(title)}</h1>
  <p>${esc(sub)}</p>
  <div class="bar"><span class="d">ctx</span> <span class="g">━━━</span><span class="d">───</span> 84k/200k  <span class="d">·</span>  <span class="d">5h</span> <span class="y">━━━━━</span><span class="d">─</span> <span class="y">89%</span> <span class="d">↻ 1h12m</span>  <span class="d">·</span>  <span class="d">week</span> <span class="g">━━</span><span class="d">────</span> 41% <span class="d">↻ 3d4h</span></div>
  </body></html>`;
}

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined });
  const page = await browser.newPage({ viewport: { width: 1200, height: 630 } });
  fs.mkdirSync(path.join(ASSETS, "og"), { recursive: true });
  for (const [slug, data] of Object.entries(OG)) {
    await page.setContent(ogHtml(data));
    await page.screenshot({ path: path.join(ASSETS, "og", `${slug}.jpg`), type: "jpeg", quality: 86 });
  }
  for (const [name, size] of [["favicon-48.png", 48], ["apple-touch-icon.png", 180], ["icon-512.png", 512]]) {
    const pad = name === "apple-touch-icon.png" ? `background:#17181c;padding:${size * 0.12}px;` : "";
    await page.setViewportSize({ width: size, height: size });
    await page.setContent(`<html><body style="margin:0;${pad}">${LOGO.replace("<svg", `<svg width="100%" height="100%" style="display:block"`)}</body></html>`);
    await page.screenshot({ path: path.join(ASSETS, name), omitBackground: !pad });
  }
  await browser.close();
  console.log("rendered", Object.keys(OG).length, "OG images and 3 icons");
})();
