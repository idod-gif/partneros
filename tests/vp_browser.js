/* View profiles in a headless browser on the synthetic page (tests/flo_fixture.py). Invented identities only.
   Usage: node vp_browser.js <page.html> '<json opts>'
   opts: {me:{id,email,viewProfile,viewProfileStatus}, canEdit, settings: doc|null, settingsError: bool, viewas: "finance"|null, clickPreview: bool, wait}
   Prints {nav, sec, flash, preview, card, warnings, errors}: flash = a full navigation was ever visible (not hidden by vp-wait) for a viewer
   who ends on the Finance view. */
const path = require("path"), fs = require("fs");
const PW = process.env.FLO_PW || ["/Users/idod/dev/dispute-desk/node_modules/playwright-core", "/Users/idod/dev/openscreen/node_modules/playwright-core"].find(p => fs.existsSync(p));
const { chromium } = require(PW);
const CHROME = process.env.FLO_CHROME || ["/Users/idod/Library/Caches/ms-playwright/chromium-1243/chrome-mac-arm64/Chromium.app/Contents/MacOS/Chromium", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"].find(p => fs.existsSync(p));
const [, , pagePath, optArg] = process.argv;
const O = Object.assign({ canEdit: false, settings: null, settingsError: false, viewas: null, wait: 1500 }, JSON.parse(optArg || "{}"));
(async () => {
  const b = await chromium.launch({ executablePath: CHROME, headless: true });
  const ctx = await b.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage(), errors = [], warnings = [];
  page.on("pageerror", e => errors.push(String(e.message).slice(0, 200)));
  page.on("console", m => { if (m.type() === "warning") warnings.push(m.text().slice(0, 200)); });
  await page.route("**/*", r => { const u = r.request().url(); if (/^(file|data|about):/.test(u)) r.continue(); else r.abort(); });
  await page.addInitScript(O0 => {
    window.__reads = 0;
    const snap = (d, id) => ({ exists: !!d, id: id || "x", data: () => d });
    const doc = p => ({ get: async () => {
        if (p === "settings/view_profiles") { window.__reads++; if (O0.settingsError) throw Object.assign(new Error("unavailable"), { code: "unavailable" }); return snap(O0.settings, p); }
        return snap(/\/profile$/.test(p) ? { tour_seen_at: "2026-11-01T00:00:00Z" } : null, p); },
      set: async () => {}, update: async () => {}, delete: async () => {}, onSnapshot: () => () => {} });
    const coll = () => { const o = { limit: () => o, orderBy: () => o, where: () => o, get: async () => ({ docs: [], size: 0 }), onSnapshot: () => () => {} }; return o; };
    window.__flash = false;
    new MutationObserver(() => { const n = document.getElementById("nav"); if (!n) return;
      const full = !!n.querySelector('a[data-s="outreach"]'); if (full && !document.documentElement.classList.contains("vp-wait")) window.__flash = true; })
      .observe(document, { subtree: true, childList: true, attributes: true, attributeFilter: ["class"] });
    window.claude = { use: async n => {
      if (n === "db") return { doc, collection: coll };
      if (n === "user") return { canEdit: async () => !!O0.canEdit, isOwner: async () => !!O0.canEdit, me: async () => O0.me, id: async () => O0.me && O0.me.id, can: async x => x === "data.write" ? true : null };
      return null; } };
    try { localStorage.setItem("partneros.tour", "done"); localStorage.removeItem("partneros.vp"); if (O0.viewas) sessionStorage.setItem("partneros.viewas", O0.viewas); } catch (e) {}
  }, O);
  await page.goto("file://" + path.resolve(pagePath));
  await page.waitForTimeout(O.wait);
  let card = null;
  if (O.canEdit) {
    await page.evaluate(() => { const a = document.querySelector('#nav a[data-s="settings"]'); if (a) a.click(); });
    await page.waitForTimeout(500);
    card = await page.evaluate(() => { const c = document.getElementById("vp-settings"); return c && !c.hidden ? { text: c.innerText.slice(0, 400), btn: !!document.getElementById("vp-preview") } : null; });
    if (O.clickPreview && card && card.btn) { await page.click("#vp-preview"); await page.waitForTimeout(O.wait); }
  }
  const r = await page.evaluate(() => ({ nav: [...document.querySelectorAll("#nav a[data-s]")].map(a => a.dataset.s), sec: (document.querySelector(".panel:not([hidden])") || {}).id,
    flash: window.__flash, reads: window.__reads, preview: !!(document.getElementById("vp-banner") && !document.getElementById("vp-banner").hidden), waiting: document.documentElement.classList.contains("vp-wait") }));
  console.log(JSON.stringify(Object.assign(r, { card, warnings, errors })));
  await b.close();
})();
