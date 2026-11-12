/* Headless browser pass for the guided tour (run by tests/test_tour.py, or by hand on a locally built page). It needs no test hook: it drives
   the page through the DOM (the offer card, Take the tour, the user menu, the palette, keys) with a stubbed window.claude.
   Usage: node tour_browser.js <page.html> <scenario> [json opts]
     opts: {role: owner|editor|viewer|none, ask: true|false, prof: none|role|seen, width, height, theme: light|dark, rm: true|false, shots: dir, tag}
   Scenarios: walk (offer, every step forward, back to the start, forward to Done), later, escoffer, skip, restart, returning, missing, keys.
   Prints one JSON object: {checks:[{name,ok,detail}], errors:[...], out:{...}}. */
const path = require("path");
const fs = require("fs");
const PW = process.env.FLO_PW || ["/Users/idod/dev/dispute-desk/node_modules/playwright-core", "/Users/idod/dev/openscreen/node_modules/playwright-core"].find(p => fs.existsSync(p));
const { chromium } = require(PW);
const CHROME = process.env.FLO_CHROME || ["/Users/idod/Library/Caches/ms-playwright/chromium-1243/chrome-mac-arm64/Chromium.app/Contents/MacOS/Chromium", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"].find(p => fs.existsSync(p));
const [, , pagePath, scenario, optArg] = process.argv;
const O = Object.assign({ role: "owner", ask: true, prof: "none", width: 1440, height: 900, theme: "light", rm: false, shots: "", tag: "" }, optArg ? JSON.parse(optArg) : {});

const checks = [];
const check = (name, ok, detail) => checks.push({ name, ok: !!ok, detail: ok ? "" : String(detail == null ? "" : detail).slice(0, 400) });
/* where each step points and which section it opens */
const SEC = { dashboard: "overview", attention: "overview", numbers: "overview", revenue: "dashboard", partners: "partners", outreach: "outreach", events: "events", payouts: "payouts", leads: "leads", settings: "settings", search: "overview", ask: "overview", menu: "overview" };

(async () => {
  const browser = await chromium.launch({ executablePath: CHROME, headless: true });
  const ctx = await browser.newContext({ viewport: { width: O.width, height: O.height }, reducedMotion: O.rm ? "reduce" : "no-preference", colorScheme: O.theme === "dark" ? "dark" : "light" });
  const page = await ctx.newPage();
  const errors = [];
  page.on("console", m => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) errors.push("console: " + m.text().slice(0, 200)); });
  page.on("pageerror", e => errors.push("pageerror: " + String(e.message).slice(0, 200)));
  await page.route("**/*", r => { const u = r.request().url(); if (u.startsWith("file:") || u.startsWith("data:") || u.startsWith("about:")) r.continue(); else r.abort(); });
  await page.addInitScript(O0 => {
    window.__writes = []; window.__samples = 0;
    const prof = O0.prof === "role" ? { role: "Partner manager" } : O0.prof === "seen" ? { tour_seen_at: "2026-11-01T00:00:00Z" } : null;
    const snap = (d, id) => ({ exists: !!d, id: id || "x", data: () => d });
    const doc = p => ({ get: async () => snap(/\/profile$/.test(p) ? prof : null, p), set: async d => { window.__writes.push({ op: "set", path: p, keys: Object.keys(d || {}).sort() }); },
      update: async d => { window.__writes.push({ op: "update", path: p }); }, delete: async () => { window.__writes.push({ op: "delete", path: p }); }, onSnapshot: () => () => {} });
    const coll = () => { const o = { limit: () => o, orderBy: () => o, where: () => o, get: async () => ({ docs: [], size: 0 }), onSnapshot: () => () => {} }; return o; };
    const db = { doc, collection: coll };
    const role = O0.role;
    window.claude = { use: async n => {
      if (n === "db") return role === "none" ? null : db;
      if (n === "user") return { canEdit: async () => role !== "viewer", isOwner: async () => role === "owner", me: async () => ({ id: "u1", name: "Sam Tester" }), id: async () => "u1", can: async () => role !== "viewer" };
      if (n === "sample" && O0.ask) { const f = async () => { window.__samples++; return { text: "ok" }; }; f.limits = async () => ({ tools: { maxCount: 24 }, rounds: 6 }); return f; }
      return null; } };
    if (O0.theme === "dark") try { localStorage.setItem("partneros.theme", "dark"); } catch (e) {}
  }, O);
  await page.goto("file://" + path.resolve(pagePath));
  const E = (fn, arg) => page.evaluate(fn, arg);
  const out = { steps: [], shots: 0 };
  const shot = async name => { if (!O.shots) return; await page.screenshot({ path: path.join(O.shots, (O.tag ? O.tag + "-" : "") + name + ".png") }); out.shots++; };
  const offerUp = () => E(() => { const p = document.getElementById("tour-pop"), r = document.getElementById("tour"); return !!(p && r && !r.hidden && p.dataset.step === "offer"); });
  const tourUp = () => E(() => { const r = document.getElementById("tour"); return !!(r && !r.hidden && !r.classList.contains("offer")); });
  const waitOffer = async ms => { const t = Date.now(); while (Date.now() - t < ms) { if (await offerUp()) return true; await page.waitForTimeout(150); } return false; };
  const settle = () => page.waitForTimeout(O.rm ? 300 : 1300);
  const curSec = () => E(() => { const p = document.querySelector(".panel:not([hidden])"); return p ? p.id.replace(/^p-/, "") : null; });
  const flag = () => E(() => { try { return localStorage.getItem("partneros.tour"); } catch (e) { return "ERR"; } });

  /* one step's state: id, counter, copy, spotlight vs the target's own rect, popover on screen and clear of the target */
  const state = () => E(SEC0 => {
    const p = document.getElementById("tour-pop"), root = document.getElementById("tour"), id = p.dataset.step;
    const n = p.querySelector(".tour-n"), t = p.querySelector("h2"), b = p.querySelector("p");
    const nav = k => document.querySelector(`#nav a[data-s="${k}"]`);
    const kp = () => { const bl = document.querySelector('#p-overview [data-b="kpis"]'); const cs = [...document.getElementById("ov-kpis").children]; const top = cs.length ? cs[0].offsetTop : 0; return [bl.querySelector(".chips")].concat(cs.filter(c => c.offsetTop === top)); };
    const T = { dashboard: () => [nav("overview")], attention: () => [document.getElementById("ov-attn")], numbers: kp, revenue: () => [nav("dashboard")], partners: () => [nav("partners")], outreach: () => [nav("outreach")],
      events: () => [nav("events")], payouts: () => [nav("payouts")], leads: () => [nav("leads")], settings: () => [nav("settings"), nav("runs")], search: () => [document.getElementById("srch")], ask: () => [document.getElementById("flo-fab")],
      menu: () => [document.getElementById("tour-nav"), document.getElementById("ucard")] };
    const vis = e => e && e.getClientRects().length && e.getBoundingClientRect().width > 0 && getComputedStyle(e).visibility !== "hidden";
    const ALT = ["dashboard", "revenue", "partners", "outreach", "events", "payouts", "leads", "settings", "search", "menu"];
    let els = (T[id] ? T[id]() : []).filter(vis);
    if (!els.length && ALT.includes(id) && window.innerWidth <= 860) els = [document.getElementById("menu-btn")].filter(vis);
    let u = null; els.forEach(e => { const q = e.getBoundingClientRect(); u = u ? { l: Math.min(u.l, q.left), t: Math.min(u.t, q.top), r: Math.max(u.r, q.right), b: Math.max(u.b, q.bottom) } : { l: q.left, t: q.top, r: q.right, b: q.bottom }; });
    const spotOn = !root.classList.contains("nospot"), s = root.querySelector(".tour-spot").getBoundingClientRect(), pr = p.getBoundingClientRect();
    const vw = document.documentElement.clientWidth, vh = window.innerHeight;
    const aligned = !spotOn || (u && Math.abs(s.left - (u.l - 6)) <= 2 && Math.abs(s.top - (u.t - 6)) <= 2 && Math.abs(s.right - (u.r + 6)) <= 2 && Math.abs(s.bottom - (u.b + 6)) <= 2);
    const onScreen = pr.left >= 0 && pr.top >= 0 && pr.right <= vw + 0.5 && pr.bottom <= vh + 0.5;
    const inView = !u || (u.t >= -1 && u.b <= vh + 1);
    const overlap = u ? Math.max(0, Math.min(pr.right, u.r) - Math.max(pr.left, u.l)) * Math.max(0, Math.min(pr.bottom, u.b) - Math.max(pr.top, u.t)) : 0;
    const panel = document.querySelector(".panel:not([hidden])");
    const a = document.activeElement;
    return { id, counter: n ? n.textContent : "", title: t ? t.textContent : "", body: b ? b.textContent : "", spotOn, hasTarget: els.length > 0, aligned, onScreen, inView, overlap: Math.round(overlap),
      sec: panel ? panel.id.replace(/^p-/, "") : null, wantSec: SEC0[id], focusIn: p.contains(a), next: (p.querySelector('[data-tour="next"]') || {}).textContent || "",
      back: !!p.querySelector('[data-tour="back"]'), dialog: p.getAttribute("role") === "dialog" && p.getAttribute("aria-modal") === "true" && !!document.getElementById(p.getAttribute("aria-labelledby")),
      z: +getComputedStyle(root).zIndex, transition: getComputedStyle(root.querySelector(".tour-spot")).transitionDuration };
  }, SEC);
  const copyOk = s => !/[—–]/.test(s.title + s.body) && s.title.split(/\s+/).length >= 1 && s.title.split(/\s+/).length <= 4 && s.body.length > 20 && s.body.length < 260;
  const stepCheck = (s, tag) => {
    check(`${tag} ${s.id}: section opened`, s.sec === s.wantSec, `${s.sec} vs ${s.wantSec}`);
    check(`${tag} ${s.id}: spotlight aligned or no target`, s.aligned && (s.spotOn === s.hasTarget), JSON.stringify(s));
    check(`${tag} ${s.id}: popover on screen`, s.onScreen, JSON.stringify(s));
    check(`${tag} ${s.id}: target in view`, s.inView, JSON.stringify(s));
    check(`${tag} ${s.id}: focus in the labelled dialog`, s.focusIn && s.dialog, JSON.stringify(s));
    check(`${tag} ${s.id}: copy rules`, copyOk(s), s.title + " | " + s.body);
  };
  const startSec = async () => curSec();

  /* wait for the first-run offer (the page settles first) */
  const offered = await waitOffer(9000);
  out.offered = offered;
  const want = !(O.prof === "role" || O.prof === "seen");

  if (scenario === "returning") {
    await page.waitForTimeout(2500);
    check("returning user: no offer", !(await offerUp()) && !offered, "offer shown");
    check("returning user: nothing written", (await E(() => window.__writes.length)) === 0, JSON.stringify(await E(() => window.__writes)));
  } else {
    check("offer appears on first run", offered === want, `offered ${offered}`);
  }

  if (scenario === "walk") {
    await page.waitForTimeout(400);
    await shot("00-welcome");
    const offer = await E(() => { const p = document.getElementById("tour-pop"); return { t: p.querySelector("h2").textContent, b: p.querySelector("p").textContent, modal: p.getAttribute("aria-modal"), btns: [...p.querySelectorAll("button")].map(x => x.textContent), av: !!p.querySelector(".ido-av") }; });
    out.offer = offer;
    check("offer: Start the tour and Not now, not modal", offer.btns.join("|") === "Start the tour|Not now" && offer.modal === "false", JSON.stringify(offer));
    check("offer: mascot only with Ask Ido", offer.av === !!O.ask, JSON.stringify(offer));
    const s0 = await startSec();
    await E(() => window.scrollTo(0, 0));
    await page.click('#tour-pop [data-tour="start"]');
    await settle();
    let s = await state(), seen = [], guard = 0;
    while (guard++ < 30) {
      seen.push(s);
      stepCheck(s, "fwd");
      await shot(String(seen.length).padStart(2, "0") + "-" + s.id);
      if (s.next === "Done") break;
      await page.click('#tour-pop [data-tour="next"]'); await settle(); s = await state();
    }
    out.steps = seen.map(x => ({ id: x.id, title: x.title, body: x.body, counter: x.counter, spot: x.spotOn, overlap: x.overlap }));
    const n = seen.length;
    check("counters read i of n", seen.every((x, i) => x.counter === `${i + 1} of ${n}`), seen.map(x => x.counter).join(","));
    check("Back hidden only on the first step", seen.every((x, i) => x.back === (i > 0)), seen.map(x => x.back).join(","));
    check("tour sits above every layer", seen[0].z >= 100, seen[0].z);
    if (O.rm) check("reduced motion: no spotlight transition", seen.every(x => /^0s(, 0s)*$/.test(x.transition)), seen[0].transition);
    /* back to the first step with the Left arrow, ids in reverse */
    const back = [];
    for (let i = 0; i < n - 1; i++) { await page.keyboard.press("ArrowLeft"); await settle(); const b = await state(); back.push(b.id); stepCheck(b, "back"); }
    check("ArrowLeft walks back through the same steps", back.join(",") === seen.slice(0, -1).map(x => x.id).reverse().join(","), back.join(","));
    await page.keyboard.press("ArrowLeft"); await settle();
    check("ArrowLeft on the first step stays", (await state()).id === seen[0].id, "moved");
    for (let i = 0; i < n - 1; i++) { await page.keyboard.press("ArrowRight"); await settle(); }
    check("ArrowRight returns to the last step", (await state()).id === seen[n - 1].id, (await state()).id);
    await page.click('#tour-pop [data-tour="next"]'); await settle();
    check("Done closes the tour", !(await tourUp()), "still open");
    if (O.shots) { await page.waitForTimeout(300); if (O.width > 860) { const b = await E(() => { const r = document.getElementById("side").getBoundingClientRect(); return { x: 0, y: 0, width: Math.ceil(r.width), height: Math.ceil(r.height) }; }); await page.screenshot({ path: path.join(O.shots, (O.tag ? O.tag + "-" : "") + "99-restart-button.png"), clip: b }); }
      else { await page.click("#menu-btn"); await page.waitForTimeout(250); await page.screenshot({ path: path.join(O.shots, (O.tag ? O.tag + "-" : "") + "99-restart-button.png") }); await page.click("#menu-btn"); } out.shots++; }
    check("Done returns to the starting section", (await curSec()) === s0, `${await curSec()} vs ${s0}`);
    const f = await flag();
    check("Done records the local flag", f && JSON.parse(f).outcome === "done", f);
    const w = await E(() => window.__writes);
    const canMine = O.role === "owner" || O.role === "editor";
    check("only the own profile doc is written, once, when writable", canMine ? (w.length === 1 && w[0].path === "data/users/u1/profile" && w[0].keys.includes("tour_seen_at") && w[0].keys.includes("tour_outcome")) : w.length === 0, JSON.stringify(w));
    check("the tour never calls Ask Ido", (await E(() => window.__samples)) === 0, "sample called");
    /* reload: the offer never comes back */
    await page.waitForTimeout(500); await page.reload(); const again = await waitOffer(5000);
    check("no second offer after reload", !again, "offered again");
  }

  if (scenario === "later") {
    await page.click('#tour-pop [data-tour="later"]'); await page.waitForTimeout(200);
    check("Not now closes the offer", !(await offerUp()), "still up");
    const f = await flag(); check("Not now records the flag", f && JSON.parse(f).outcome === "not_now", f);
    await page.waitForTimeout(500); await page.reload(); check("Not now: no offer after reload", !(await waitOffer(5000)), "offered");
  }

  if (scenario === "escoffer") {
    await E(() => document.querySelector('#tour-pop [data-tour="start"]').focus());
    await page.keyboard.press("Escape"); await page.waitForTimeout(200);
    const f = await flag(); check("Escape on the offer counts as Not now", !(await offerUp()) && f && JSON.parse(f).outcome === "not_now", f);
  }

  if (scenario === "skip") {
    await page.click('#tour-pop [data-tour="later"]');
    await E(() => { localStorage.removeItem("partneros.tour"); });
    await E(() => { const a = document.querySelector('#nav a[data-s="leads"]'); a.click(); });
    await page.waitForTimeout(300);
    await E(() => window.scrollTo(0, 120)); const y0 = await E(() => window.scrollY);
    await E(() => document.getElementById("tour-nav").click()); await settle();
    for (let i = 0; i < 4; i++) { await page.keyboard.press("ArrowRight"); await settle(); }
    check("mid-tour the section moved", (await curSec()) !== "leads", await curSec());
    await page.click('#tour-pop [data-tour="skip"]'); await settle();
    check("Skip closes the tour", !(await tourUp()), "open");
    check("Skip returns to the starting section", (await curSec()) === "leads", await curSec());
    check("Skip restores the scroll", Math.abs((await E(() => window.scrollY)) - y0) <= 2, `${await E(() => window.scrollY)} vs ${y0}`);
    const f = await flag(); check("Skip records the flag", f && JSON.parse(f).outcome === "skipped", f);
    check("focus returns to Take the tour", await E(() => document.activeElement && document.activeElement.id === "tour-nav"), await E(() => document.activeElement && (document.activeElement.id || document.activeElement.tagName)));
  }

  if (scenario === "restart") {
    await page.click('#tour-pop [data-tour="later"]');
    const desk = O.width > 860;
    /* 1. the sidebar item (at narrow widths: inside the menu button's list) */
    if (desk) await page.click("#tour-nav");
    else { await page.click("#menu-btn"); await page.waitForTimeout(150); await page.click("#tour-nav"); }
    await settle();
    check("Take the tour in the sidebar starts the tour", await tourUp(), "not open");
    check("restart starts at step 1", (await state()).counter.startsWith("1 of"), (await state()).counter);
    await page.keyboard.press("Escape"); await settle();
    check("Escape closes the tour", !(await tourUp()), "open");
    check("focus returns to the launcher (the menu button when the menu closed)", await E(d => document.activeElement && document.activeElement.id === (d ? "tour-nav" : "menu-btn"), desk), await E(() => document.activeElement && (document.activeElement.id || document.activeElement.tagName)));
    /* 2. the user menu (desktop only: the user card is hidden under 860px) */
    if (desk) {
      await page.click("#ucard"); await page.waitForTimeout(200);
      const has = await E(() => !!document.querySelector('#umenu [data-um="tour"]'));
      check("user menu has Take the tour", has, "missing");
      if (has) { await page.click('#umenu [data-um="tour"]'); await settle(); check("user menu item starts the tour", await tourUp() && await E(() => document.getElementById("umenu").hidden), "not open");
        await page.keyboard.press("Escape"); await settle(); check("focus returns to the user card", await E(() => document.activeElement && document.activeElement.id === "ucard"), "no"); }
    }
    /* 3. the palette command */
    await page.keyboard.press(process.platform === "darwin" ? "Meta+k" : "Control+k"); await page.waitForTimeout(200);
    await page.keyboard.type("take the tour"); await page.waitForTimeout(150);
    const row = await E(() => { const o = document.querySelector('#pal-list [aria-selected="true"] .pt'); return o ? o.textContent : ""; });
    check("palette finds Take the tour", row === "Take the tour", row);
    await page.keyboard.press("Enter"); await settle();
    check("palette command starts the tour", await tourUp() && await E(() => document.getElementById("pal").hidden), "not open");
    await page.keyboard.press("Escape"); await settle();
  }

  if (scenario === "missing") {
    await page.click('#tour-pop [data-tour="later"]');
    /* the Needs attention block hidden by Customize before the start: the step leaves the plan */
    await E(() => { document.querySelector('#p-overview [data-b="attention"]').hidden = true; });
    await E(() => document.getElementById("tour-nav").click()); await settle();
    const ids = []; let s = await state(), g = 0;
    while (g++ < 30) { ids.push(s.id); if (s.next === "Done") break; await page.keyboard.press("ArrowRight"); await settle(); s = await state(); }
    check("hidden block: its step is skipped, never a spotlight on nothing", !ids.includes("attention") && ids.length > 5, ids.join(","));
    await page.keyboard.press("Escape"); await settle();
    /* the block disappears after the start: the step drops out on arrival and the counter shrinks */
    await E(() => { document.querySelector('#p-overview [data-b="attention"]').hidden = false; });
    await E(() => document.getElementById("tour-nav").click()); await settle();
    const n0 = (await state()).counter;
    await E(() => { document.querySelector('#p-overview [data-b="attention"]').hidden = true; });
    await page.keyboard.press("ArrowRight"); await page.waitForTimeout(1200);
    const s2 = await state();
    check("target gone on arrival: step dropped, next step shown", s2.id === "numbers" && +s2.counter.split(" of ")[1] === +n0.split(" of ")[1] - 1, JSON.stringify([n0, s2.id, s2.counter]));
    await page.keyboard.press("Escape"); await settle();
    out.ids = ids;
  }

  if (scenario === "keys") {
    await page.click('#tour-pop [data-tour="later"]');
    await E(() => document.getElementById("tour-nav").click()); await settle();
    const th0 = await E(() => document.documentElement.getAttribute("data-theme"));
    await page.keyboard.press("t"); await page.keyboard.press("/"); await page.keyboard.press(process.platform === "darwin" ? "Meta+k" : "Control+k"); await page.waitForTimeout(250);
    check("page shortcuts do nothing during the tour", (await E(() => document.documentElement.getAttribute("data-theme"))) === th0 && await E(() => document.getElementById("pal").hidden && document.getElementById("flo").hidden), "a shortcut fired");
    check("still on step 1", (await state()).counter.startsWith("1 of"), (await state()).counter);
    for (let i = 0; i < 6; i++) await page.keyboard.press("Tab");
    check("Tab stays inside the popover", (await state()).focusIn, "focus left");
    /* clicks on the dimmed page and on the spotlit element do nothing */
    const before = await state();
    await page.mouse.click(5, O.height - 5); await page.waitForTimeout(150);
    const r = await E(() => { const a = document.querySelector('#nav a[data-s="overview"]').getBoundingClientRect(); return [a.left + a.width / 2, a.top + a.height / 2]; });
    await page.mouse.click(r[0], r[1]); await page.waitForTimeout(150);
    const after = await state();
    check("clicks outside the popover change nothing", after.id === before.id && await tourUp(), JSON.stringify([before.id, after.id]));
    /* scroll and resize keep the spotlight on its target */
    await page.keyboard.press("ArrowRight"); await settle();
    await E(() => window.scrollBy(0, 60)); await page.waitForTimeout(120);
    const sc = await state(); check("scroll keeps the spotlight aligned", sc.aligned, JSON.stringify(sc));
    await page.setViewportSize({ width: O.width - 100, height: O.height - 60 }); await page.waitForTimeout(250);
    const rs = await state(); check("resize keeps the spotlight aligned", rs.aligned && rs.onScreen, JSON.stringify(rs));
  }

  out.errors = errors.length;
  process.stdout.write(JSON.stringify({ checks, errors, out }));
  await browser.close();
})().catch(e => { process.stdout.write(JSON.stringify({ fatal: String(e && e.stack || e).slice(0, 1500), checks, errors: [] })); process.exit(0); });
