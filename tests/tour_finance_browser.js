/* Headless pass for the finance tour (run by tests/test_tour.py). Drives the page through the DOM with a stubbed window.claude that records
   every db write and every tour card shown. Invented identities and data only.
   Usage: node tour_finance_browser.js <page.html> <scenario> [json opts]
     opts: {view: query|mapped|preview|none, db: true|false, ask, width, height, theme, rm, slowVp (ms before settings/view_profiles answers),
            lsGeneral (general tour flag already set), prof: none|seen|role, lsFinance, shots, tag}
   Scenarios: walk, entries, wait, seen, missing.
   Prints {checks:[{name,ok,detail}], errors:[...], out:{...}}. */
const path = require("path");
const fs = require("fs");
const PW = process.env.FLO_PW || ["/Users/idod/dev/dispute-desk/node_modules/playwright-core", "/Users/idod/dev/openscreen/node_modules/playwright-core"].find(p => fs.existsSync(p));
const { chromium } = require(PW);
const CHROME = process.env.FLO_CHROME || ["/Users/idod/Library/Caches/ms-playwright/chromium-1243/chrome-mac-arm64/Chromium.app/Contents/MacOS/Chromium", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"].find(p => fs.existsSync(p));
const [, , pagePath, scenario, optArg] = process.argv;
const O = Object.assign({ view: "query", db: true, ask: true, width: 1440, height: 900, theme: "light", rm: false, slowVp: 0, lsGeneral: false, prof: "none", lsFinance: false, shots: "", tag: "" }, optArg ? JSON.parse(optArg) : {});
const checks = [];
const check = (name, ok, detail) => checks.push({ name, ok: !!ok, detail: ok ? "" : String(detail == null ? "" : detail).slice(0, 400) });
const SEC = { "fin-summary": "finance", "fin-sources": "finance", "fin-explorer": "dashboard", "fin-chart": "dashboard", "fin-payouts": "payouts", "fin-partners": "partners", "fin-ask": "finance", "fin-menu": "finance" };
const WHAT = "This is the Finance view: a short tour of the quarter summary, the Revenue explorer, Payouts and Partners, about a minute.";

(async () => {
  const browser = await chromium.launch({ executablePath: CHROME, headless: true });
  const ctx = await browser.newContext({ viewport: { width: O.width, height: O.height }, reducedMotion: O.rm ? "reduce" : "no-preference", colorScheme: O.theme === "dark" ? "dark" : "light" });
  const page = await ctx.newPage();
  const errors = [];
  page.on("console", m => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) errors.push("console: " + m.text().slice(0, 200)); });
  page.on("pageerror", e => errors.push("pageerror: " + String(e.message).slice(0, 200)));
  await page.route("**/*", r => { const u = r.request().url(); if (/^(file|data|about):/.test(u)) r.continue(); else r.abort(); });
  await page.addInitScript(O0 => {
    window.__writes = []; window.__samples = 0; window.__cards = [];
    const prof = O0.prof === "seen" ? { tour_seen_at: "2026-11-01T00:00:00Z" } : O0.prof === "role" ? { role: "Controller", tour_seen_at: "2026-11-01T00:00:00Z" } : null;
    const snap = (d, id) => ({ exists: !!d, id: id || "x", data: () => d });
    const doc = p => ({ get: async () => {
        if (p === "settings/view_profiles") { if (O0.slowVp) await new Promise(r => setTimeout(r, O0.slowVp)); return snap(O0.view === "mapped" ? { profiles: { "u1": "finance" } } : null, p); }
        return snap(/\/profile$/.test(p) ? prof : null, p); },
      set: async d => { window.__writes.push({ op: "set", path: p, keys: Object.keys(d || {}).sort() }); },
      update: async d => { window.__writes.push({ op: "update", path: p, keys: Object.keys(d || {}).sort() }); },
      delete: async () => { window.__writes.push({ op: "delete", path: p }); }, onSnapshot: () => () => {} });
    const coll = () => { const o = { limit: () => o, orderBy: () => o, where: () => o, get: async () => ({ docs: [], size: 0 }), onSnapshot: () => () => {} }; return o; };
    const admin = O0.view === "preview";
    window.claude = { use: async n => {
      if (n === "db") return O0.db ? { doc, collection: coll } : null;
      if (n === "user") return { canEdit: async () => admin, isOwner: async () => admin, me: async () => ({ id: "u1", name: "Sam Tester" }), id: async () => "u1", can: async () => admin };
      if (n === "sample" && O0.ask) { const f = async () => { window.__samples++; return { text: "ok" }; }; f.limits = async () => ({ tools: { maxCount: 24 }, rounds: 6 }); return f; }
      return null; } };
    try {
      if (O0.theme === "dark") localStorage.setItem("partneros.theme", "dark");
      if (O0.lsGeneral) localStorage.setItem("partneros.tour", JSON.stringify({ seen_at: "2026-11-01T00:00:00Z", outcome: "done" }));
      if (O0.lsFinance) localStorage.setItem("partneros.tour.finance", JSON.stringify({ seen_at: "2026-11-01T00:00:00Z", outcome: "done" }));
      if (admin) sessionStorage.setItem("partneros.viewas", "finance");
    } catch (e) {}
    /* every start card that ever appears, with the tour it belongs to */
    new MutationObserver(() => { const p = document.getElementById("tour-pop"); if (!p || !p.dataset.step || !/^offer/.test(p.dataset.step)) return;
      const k = p.dataset.step + ":" + (p.dataset.tour || "?") + ":" + (document.documentElement.classList.contains("vp-finance") ? "F" : "-");
      if (window.__cards[window.__cards.length - 1] !== k) window.__cards.push(k); }).observe(document, { subtree: true, childList: true, attributes: true, attributeFilter: ["data-step"] });
  }, O);
  await page.goto("file://" + path.resolve(pagePath) + (O.view === "query" ? "?view=finance" : ""));
  const E = (fn, arg) => page.evaluate(fn, arg);
  const out = { steps: [], shots: 0 };
  const shot = async name => { if (!O.shots) return; await page.screenshot({ path: path.join(O.shots, (O.tag ? O.tag + "-" : "") + name + ".png") }); out.shots++; };
  const cardUp = kind => E(k => { const p = document.getElementById("tour-pop"), r = document.getElementById("tour"); return !!(p && r && !r.hidden && r.classList.contains("offer") && p.dataset.step === k); }, kind);
  const tourUp = () => E(() => { const r = document.getElementById("tour"); return !!(r && !r.hidden && !r.classList.contains("offer")); });
  const waitCard = async (k, ms) => { const t = Date.now(); while (Date.now() - t < ms) { if (await cardUp(k)) return true; await page.waitForTimeout(120); } return false; };
  const settle = () => page.waitForTimeout(O.rm ? 350 : 1300);
  const curSec = () => E(() => { const p = document.querySelector(".panel:not([hidden])"); return p ? p.id.replace(/^p-/, "") : null; });
  const flags = () => E(() => ({ g: localStorage.getItem("partneros.tour"), f: localStorage.getItem("partneros.tour.finance") }));
  const card = () => E(() => { const p = document.getElementById("tour-pop"); return { tour: p.dataset.tour, t: p.querySelector("h2").textContent, b: p.querySelector("p").textContent, btns: [...p.querySelectorAll("button")].map(x => x.textContent), modal: p.getAttribute("aria-modal"), focusIn: p.contains(document.activeElement) }; });
  const state = () => E(() => {
    const p = document.getElementById("tour-pop"), root = document.getElementById("tour"), id = p.dataset.step;
    const n = p.querySelector(".tour-n"), t = p.querySelector("h2"), b = p.querySelector("p");
    const spotOn = !root.classList.contains("nospot"), s = root.querySelector(".tour-spot").getBoundingClientRect(), pr = p.getBoundingClientRect();
    const vw = document.documentElement.clientWidth, vh = window.innerHeight;
    const panel = document.querySelector(".panel:not([hidden])");
    const cs = getComputedStyle(p), dark = document.documentElement.getAttribute("data-theme") === "dark" || matchMedia("(prefers-color-scheme: dark)").matches;
    return { id, counter: n ? n.textContent : "", title: t ? t.textContent : "", body: b ? b.textContent : "", spotOn, spot: { l: s.left, t: s.top, r: s.right, b: s.bottom },
      pop: { l: pr.left, t: pr.top, r: pr.right, b: pr.bottom }, onScreen: pr.left >= 0 && pr.top >= 0 && pr.right <= vw + 0.5 && pr.bottom <= vh + 0.5, vh,
      sec: panel ? panel.id.replace(/^p-/, "") : null, focusIn: p.contains(document.activeElement), next: (p.querySelector('[data-tour="next"]') || {}).textContent || "",
      back: !!p.querySelector('[data-tour="back"]'), dialog: p.getAttribute("role") === "dialog" && p.getAttribute("aria-modal") === "true", fg: cs.color, bg: cs.backgroundColor, dark,
      scrollW: document.documentElement.scrollWidth - vw };
  });
  const sentences = s => s.split(/(?<=[.?!])\s+(?=[A-Z])/).filter(Boolean).length;
  const copyOk = s => !/[—–]/.test(s.title + s.body) && !/\$|\d+(\.\d+)?%/.test(s.body) && s.title.split(/\s+/).length <= 4 && s.title[0] === s.title[0].toUpperCase() && sentences(s.body) <= 2 && s.body.length > 30 && s.body.length <= 320;
  const stepCheck = (s, tag) => {
    check(`${tag} ${s.id}: a finance step`, !!SEC[s.id], s.id);
    check(`${tag} ${s.id}: section opened`, s.sec === SEC[s.id], `${s.sec} vs ${SEC[s.id]}`);
    check(`${tag} ${s.id}: popover on screen`, s.onScreen, JSON.stringify(s.pop));
    check(`${tag} ${s.id}: spotlight top in view`, !s.spotOn || (s.spot.t >= -1 && s.spot.t < s.vh - 40), JSON.stringify(s.spot));
    const ov = s.spotOn ? Math.max(0, Math.min(s.pop.r, s.spot.r) - Math.max(s.pop.l, s.spot.l)) * Math.max(0, Math.min(s.pop.b, s.spot.b) - Math.max(s.pop.t, s.spot.t)) : 0;
    if (O.width > 600 && s.id !== "fin-menu") check(`${tag} ${s.id}: popover clear of the spotlight`, ov < 400, ov);
    check(`${tag} ${s.id}: focus in the dialog`, s.focusIn && s.dialog, JSON.stringify([s.focusIn, s.dialog]));
    check(`${tag} ${s.id}: copy rules`, copyOk(s), s.title + " | " + s.body);
    check(`${tag} ${s.id}: no horizontal page scroll`, s.scrollW <= 1, s.scrollW);
  };

  if (scenario === "walk") {
    const offered = await waitCard("offer", 12000);
    check("finance welcome on first run", offered, "no offer");
    if (offered) {
      await page.waitForTimeout(400); await shot("00-welcome");
      const c = await card(); out.offer = c;
      check("welcome: finance tour, exact copy, buttons", c.tour === "finance" && c.t === "Welcome to PartnerOS" && c.b === WHAT && c.btns.join("|") === "Start the tour|Not now" && c.modal === "true" && c.focusIn, JSON.stringify(c));
      const s0 = await curSec();
      await page.click('#tour-pop [data-tour="start"]'); await settle();
      const seen = []; let s = await state(), g = 0;
      while (g++ < 20) { seen.push(s); stepCheck(s, "fwd"); await shot(String(seen.length).padStart(2, "0") + "-" + s.id); if (s.next === "Done") break; await page.click('#tour-pop [data-tour="next"]'); await settle(); s = await state(); }
      out.steps = seen.map(x => ({ id: x.id, title: x.title, body: x.body, counter: x.counter, spot: x.spotOn }));
      const n = seen.length;
      check("6 to 8 steps", n >= 6 && n <= 8, n);
      check("counters read i of n", seen.every((x, i) => x.counter === `${i + 1} of ${n}`), seen.map(x => x.counter).join(","));
      check("dark: popover text and card differ", seen.every(x => x.fg !== x.bg), "same colour");
      const back = [];
      for (let i = 0; i < n - 1; i++) { await page.keyboard.press("ArrowLeft"); await settle(); const b = await state(); back.push(b.id); stepCheck(b, "back"); }
      check("ArrowLeft walks back through the same steps", back.join(",") === seen.slice(0, -1).map(x => x.id).reverse().join(","), back.join(","));
      for (let i = 0; i < n - 1; i++) { await page.keyboard.press("ArrowRight"); await settle(); }
      check("ArrowRight returns to the last step", (await state()).id === seen[n - 1].id, (await state()).id);
      await page.click('#tour-pop [data-tour="next"]'); await settle();
      check("Done closes the tour", !(await tourUp()), "open");
      check("Done returns to the starting section", (await curSec()) === s0, `${await curSec()} vs ${s0}`);
      const f = await flags();
      check("Done records the finance flag only", f.f && JSON.parse(f.f).outcome === "done" && (O.lsGeneral ? !!f.g : f.g === null), JSON.stringify(f));
      check("nothing written to the database (read-only view)", (await E(() => window.__writes.length)) === 0, JSON.stringify(await E(() => window.__writes)));
      check("the tour never calls Ask Ido", (await E(() => window.__samples)) === 0, "sample called");
      if (O.shots) { await page.waitForTimeout(300); await shot("99-after"); }
      await page.reload(); await page.waitForTimeout(500);
      check("no second offer after reload", !(await waitCard("offer", 6000)), "offered again");
    }
  }

  if (scenario === "entries") {
    check("finance welcome first", await waitCard("offer", 12000), "no offer");
    await page.click('#tour-pop [data-tour="later"]'); await page.waitForTimeout(200);
    const f0 = await flags(); check("Not now records the finance flag", f0.f && JSON.parse(f0.f).outcome === "not_now" && f0.g === null, JSON.stringify(f0));
    const desk = O.width > 860;
    const restart = async tag => {
      const up = await waitCard("offer-restart", 3000); const c = up ? await card() : null;
      check(`${tag}: finance restart card`, up && c.tour === "finance" && c.t === "Take the tour" && c.b === WHAT && c.btns.join("|") === "Start the tour|Close", JSON.stringify(c));
      if (up) { if (O.shots) await page.waitForTimeout(450); await shot("restart-" + tag.replace(/\s+/g, "-")); await page.click('#tour-pop [data-tour="start"]'); await settle(); const s = await state(); check(`${tag}: lands on the finance step 1`, s.id === "fin-summary" && s.counter.startsWith("1 of"), s.id + " " + s.counter);
        await page.keyboard.press("Escape"); await settle(); check(`${tag}: Escape closes`, !(await tourUp()), "open"); }
    };
    check("sidebar has Take the tour after the profile resolved", await E(() => !!document.getElementById("tour-nav")), "missing");
    if (desk) await page.click("#tour-nav"); else { await page.click("#menu-btn"); await page.waitForTimeout(200); await page.click("#tour-nav"); }
    await restart("sidebar");
    check("focus returns to the launcher", await E(d => document.activeElement && document.activeElement.id === (d ? "tour-nav" : "menu-btn"), desk), await E(() => document.activeElement && (document.activeElement.id || document.activeElement.tagName)));
    if (desk) { await page.click("#ucard"); await page.waitForTimeout(200); await page.click('#umenu [data-um="tour"]'); await restart("user menu"); }
    await page.keyboard.press(process.platform === "darwin" ? "Meta+k" : "Control+k"); await page.waitForTimeout(200);
    await page.keyboard.type("take the tour"); await page.waitForTimeout(150); await page.keyboard.press("Enter");
    await restart("palette");
    await page.click("#tour-nav").catch(() => {}); if (!desk) { await page.click("#menu-btn"); await page.waitForTimeout(200); await page.click("#tour-nav"); }
    if (await waitCard("offer-restart", 2000)) { await page.click('#tour-pop [data-tour="close"]'); await page.waitForTimeout(150); }
    const f1 = await flags(); check("Close and Escape leave the flags as they were", f1.f === f0.f && f1.g === null, JSON.stringify(f1));
    check("no writes", (await E(() => window.__writes.length)) === 0, "wrote");
  }

  if (scenario === "wait") {
    /* the view profile answers late: the first card must already be the finance one, never the general welcome */
    const offered = await waitCard("offer", 15000);
    const cards = await E(() => window.__cards);
    out.cards = cards;
    check("a welcome appears", offered, "none");
    check("only the finance welcome was ever shown", cards.length >= 1 && cards.every(k => /:finance:F$/.test(k)), cards.join(","));
  }

  if (scenario === "seen") {
    const offered = await waitCard("offer", 9000);
    const want = !O.lsFinance;
    check(want ? "general tour seen does not hide the finance welcome" : "finance flag set: no welcome", offered === want, `offered ${offered}`);
    if (offered) { const c = await card(); check("it is the finance welcome", c.tour === "finance", c.tour); await page.click('#tour-pop [data-tour="later"]'); await page.waitForTimeout(200);
      const f = await flags(); check("the general flag is untouched", O.lsGeneral ? JSON.parse(f.g).outcome === "done" : f.g === null, JSON.stringify(f)); }
    check("no writes", (await E(() => window.__writes.length)) === 0, JSON.stringify(await E(() => window.__writes)));
  }

  if (scenario === "missing") {
    await waitCard("offer", 12000); await page.click('#tour-pop [data-tour="later"]');
    await E(() => { document.getElementById("fin-note").hidden = true; });
    await E(() => document.getElementById("tour-nav").click()); await waitCard("offer-restart", 3000);
    await page.click('#tour-pop [data-tour="start"]'); await settle();
    const ids = []; let s = await state(), g = 0;
    while (g++ < 20) { ids.push(s.id); stepCheck(s, "missing"); if (s.next === "Done") break; await page.keyboard.press("ArrowRight"); await settle(); s = await state(); }
    out.ids = ids;
    check("hidden target: its step is skipped", !ids.includes("fin-sources") && ids.length >= 5, ids.join(","));
    await page.keyboard.press("Escape"); await settle();
    check("section restored", (await curSec()) === "finance", await curSec());
  }

  process.stdout.write(JSON.stringify({ checks, errors, out }));
  await browser.close();
})().catch(e => { process.stdout.write(JSON.stringify({ fatal: String(e && e.stack || e).slice(0, 1500), checks, errors: [] })); process.exit(0); });
