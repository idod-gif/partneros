/* Net-new revenue rule, in a headless browser on the synthetic page (tests/flo_fixture.py). Usage: node net_new_browser.js <page.html>
   Replaces the page's snapshot stores with invented ones, runs ovCompute, prints {checks:[{name,ok,detail}], errors:[]}. Invented data only. */
const path = require("path");
const fs = require("fs");
const PW = process.env.FLO_PW || ["/Users/idod/dev/dispute-desk/node_modules/playwright-core", "/Users/idod/dev/openscreen/node_modules/playwright-core"].find(p => fs.existsSync(p));
const { chromium } = require(PW);
const CHROME = process.env.FLO_CHROME || ["/Users/idod/Library/Caches/ms-playwright/chromium-1243/chrome-mac-arm64/Chromium.app/Contents/MacOS/Chromium", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"].find(p => fs.existsSync(p));
const checks = [];
const check = (name, ok, detail) => checks.push({ name, ok: !!ok, detail: ok ? "" : String(detail == null ? "" : detail).slice(0, 300) });
(async () => {
  const browser = await chromium.launch({ executablePath: CHROME, headless: true });
  const page = await (await browser.newContext({ viewport: { width: 1500, height: 1000 } })).newPage();
  const errors = [];
  page.on("console", m => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) errors.push("console: " + m.text().slice(0, 200)); });
  page.on("pageerror", e => errors.push("pageerror: " + String(e.message).slice(0, 200)));
  await page.route("**/*", r => { const u = r.request().url(); if (u.startsWith("file:") || u.startsWith("data:") || u.startsWith("about:")) r.continue(); else r.abort(); });
  await page.addInitScript(() => { window.claude = { use: async () => null }; });
  await page.goto("file://" + path.resolve(process.argv[2]));
  await page.waitForFunction(() => window.__T && window.__T.ST.dash, null, { timeout: 20000 });
  const r = await page.evaluate(() => {
    const T = window.__T, D = T.ST.dash;
    const st = (id, bm, extra) => Object.assign({ lead_id: 9000 + id, hs_id: "n" + id, partner: "P" + id, store: "s" + id + ".example", scope: "payout", by_month: bm, paying_since: "2026-09-10", lifetime: { stripe: 1, shopify: 0, total: 1 } }, extra || {});
    D.stores = [
      st(1, { "2026-10": 100 }),                                   /* first month in period: counts */
      st(2, { "2026-09": 50, "2026-10": 70, "2026-11": 30 }),      /* first month before period: no */
      st(3, { "2026-11": 40 }, { paying_since: "2025-06-01" }),    /* paying_since before the window: no */
      st(4, { "2026-11": 25 }, { paying_since: null }),            /* missing paying_since: counts */
      st(5, { "2026-10": 10 }, { paying_since: "2026-03-01" }),    /* inside window, before first revenue: counts */
      st(6, { "2026-10": 999 }, { scope: "program" }),             /* program scope: never */
      st(7, { "2026-01": 5 }),                                     /* no revenue in period: adds nothing */
      st(8, { "2026-10": 0, "2026-11": 12 })                       /* zero month then revenue: first positive month in period */
    ];
    const V = T.ovCompute("alltime", "all");
    const sum = ids => ids.reduce((a, i) => a + T.storeEarned(D.stores[i - 1], V.openP.months), 0);
    const out = { open: V.openP.label, netRev: V.netRev, netStores: V.netStores, expRev: sum([1, 4, 5, 8]),
      helper: D.stores.map(s => T.netNewEarned(D, s, V.openP.months)) };
    out.keys = ["ytd", "q1", "q2", "q3", "oct"].map(k => { const v = T.ovCompute(k, "all"); return [k, v.netRev, v.netStores]; });
    T.LBS.shopify = false; const off = T.ovCompute("alltime", "all"); T.LBS.shopify = true; const on = T.ovCompute("alltime", "all");
    out.shop = [off.netRev, on.netRev, off.netStores, on.netStores];
    D.stores = [st(1, { "2026-09": 100, "2026-10": 5 })];
    const e = T.ovCompute("alltime", "all"); out.empty = [e.netRev, e.netStores];
    return out;
  });
  const near = (a, b) => Math.abs(a - b) < 1e-6;
  check("net-new revenue equals the open-period sum of the qualifying stores", near(r.netRev, r.expRev), JSON.stringify([r.netRev, r.expRev]));
  check("count is the qualifying stores with revenue above zero", r.netStores === 4, r.netStores);
  check("helper: first month in period counts", r.helper[0] === 100, r.helper[0]);
  check("helper: first month before the period does not", r.helper[1] === null, r.helper[1]);
  check("helper: paying_since before the window excludes", r.helper[2] === null, r.helper[2]);
  check("helper: missing paying_since still counts", r.helper[3] === 25, r.helper[3]);
  check("helper: paying_since inside the window before first revenue counts", r.helper[4] === 10, r.helper[4]);
  check("program-scope store never reaches the card", r.netRev === r.expRev, JSON.stringify([r.netRev, r.expRev]));
  check("helper: no revenue in the period adds nothing", r.helper[6] === null, r.helper[6]);
  check("helper: zero month then revenue counts from the first positive month", r.helper[7] === 12, r.helper[7]);
  check("card value is the same in every period view", r.keys.every(k => near(k[1], r.netRev) && k[2] === r.netStores), JSON.stringify(r.keys));
  check("Shopify switch does not move the card and the count agrees", near(r.shop[0], r.shop[1]) && near(r.shop[0], r.netRev) && r.shop[2] === r.shop[3], JSON.stringify(r.shop));
  check("empty state: no net-new store gives zero and zero", r.empty[0] === 0 && r.empty[1] === 0, JSON.stringify(r.empty));
  await browser.close();
  console.log(JSON.stringify({ checks, errors, out: { open: r.open } }));
})().catch(e => { console.log(JSON.stringify({ fatal: String(e.stack || e).slice(0, 500), checks, errors: [] })); });
