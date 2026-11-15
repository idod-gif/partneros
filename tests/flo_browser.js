/* Headless browser pass for Ask Ido's revenue and leads tools (run by tests/test_flo_tools.py, or by hand on a locally built page).
   Usage: node flo_browser.js <page.html> <role: editor|viewer> <leads: fixture|generated|none> [snap: yes|no]
   The page must carry the test hook (window.__T). Prints one JSON object: {checks:[{tool,name,ok,detail}], errors:[...], out:{...}}.
   Every comparison reads the tool result and the value the page itself renders (the Payouts Earned total and Top partners, Leads tiles and table) for the same filter. */
const path = require("path");
const fs = require("fs");
const PW = process.env.FLO_PW || ["/Users/idod/dev/dispute-desk/node_modules/playwright-core", "/Users/idod/dev/openscreen/node_modules/playwright-core"].find(p => fs.existsSync(p));
const { chromium } = require(PW);
const CHROME = process.env.FLO_CHROME || ["/Users/idod/Library/Caches/ms-playwright/chromium-1243/chrome-mac-arm64/Chromium.app/Contents/MacOS/Chromium", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"].find(p => fs.existsSync(p));
const [, , pagePath, role, leadsMode, snapArg] = process.argv;

const checks = [];
const check = (tool, name, ok, detail) => checks.push({ tool, name, ok: !!ok, detail: ok ? "" : String(detail == null ? "" : detail).slice(0, 300) });
const num = s => +String(s).replace(/[^0-9.\-]/g, "");

(async () => {
  const browser = await chromium.launch({ executablePath: CHROME, headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1500, height: 1000 }, reducedMotion: "reduce" });
  const page = await ctx.newPage();
  const errors = [];
  page.on("console", m => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) errors.push("console: " + m.text().slice(0, 200)); });
  page.on("pageerror", e => errors.push("pageerror: " + String(e.message).slice(0, 200)));
  await page.route("**/*", r => { const u = r.request().url(); if (u.startsWith("file:") || u.startsWith("data:") || u.startsWith("about:")) r.continue(); else r.abort(); });
  await page.addInitScript(({ role, leadsMode, partners }) => {
    const day = n => { const d = new Date(Date.now() - n * 86400000); return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0"); };
    const mk = (id, partner, company, status, cf, coll, ago, extra) => Object.assign({ id: String(id), lead_id: id, partner, company, store_handle: company.toLowerCase().replace(/\W+/g, "") + ".myshopify.com", status, cf_status: cf, collected_usd: coll,
      referred_at: ago == null ? null : day(ago) + "T10:00:00Z", lost_at: status === "lost" ? day(Math.max(0, (ago || 0) - 3)) + "T10:00:00Z" : null, missing_deal: false, has_contact: false, paying: cf === "paying", updated_at: day(1) + "T09:00:00Z" }, extra || {});
    const gen = names => {
      const out = []; let n = 5000;
      const sts = [["approved", "paying", 120.25], ["pending", "open", 0], ["lost", "churned", 0], ["approved", "installed", 15.5], ["pending", "billing_pending", 0], ["approved", "paying", 40]];
      names.forEach((p, pi) => { for (let k = 0; k < 6; k++) { const s = sts[(k + pi) % sts.length]; out.push(mk(n++, p, "Shop " + pi + "-" + k, s[0], s[1], s[2], [0, 9, 40, 100, 200, 420][k], { missing_deal: k === 1 })); } });
      out.push(mk(n++, "", "Orphan Store", "pending", null, 0, 5));
      out.push(mk(n++, names[0], "owner" + "@" + "mail-example.io", "approved", "paying", 10, 12, { email: "lead" + "@" + "contact-example.io", phone: ["+1", "555", "010", "0000"].join(" "), contact_name: "Pat Contact", has_contact: true }));
      out.push(mk(n++, names[0], "Undated Store", "approved", "installed", 0, null));
      return out;
    };
    const names = () => { const T = window.__T; const d = T ? T.ST.docs : []; return (partners && partners.length ? partners : d.slice(0, 5).map(x => x.name)); };
    const coll = (path, docs) => { const o = { limit: () => o, orderBy: () => o, where: () => o, get: async () => { if (path === "leads" && leadsMode === "none") throw new Error("leads unreadable"); return { docs: docs().map(d => ({ exists: true, id: String(d.id), data: () => d })), size: docs().length }; } }; return o; };
    const db = { doc: () => ({ get: async () => ({ exists: false, data: () => null }), set: async () => {}, update: async () => {}, delete: async () => {} }),
      collection: p => coll(p, () => p === "leads" && leadsMode !== "none" ? gen(names()) : []) };
    window.claude = { use: async name => {
      if (name === "db") return db;
      if (name === "user") return { canEdit: async () => role !== "viewer", isOwner: async () => role === "owner", me: async () => ({ id: "u1", name: "Test Viewer" }), id: async () => "u1", can: async () => role !== "viewer" };
      if (name === "sample") return window.__sample || null;
      return null; } };
    if (true) window.__sample = Object.assign(async (messages, opts) => {
      window.__sampleLog = window.__sampleLog || [];
      const tools = (opts && opts.tools) || [], by = {}; tools.forEach(t => by[t.name] = t);
      window.__sampleLog.push({ tools: tools.map(t => t.name), system: messages[0].content });
      const run = async (name, input) => { const v = await by[name].execute(input, {}); const s = typeof v === "string" ? v : JSON.stringify(v); window.__sampleLog.push({ call: name, chars: s.length }); return JSON.parse(s.slice(0, 12000)); };
      const q = String(messages[messages.length - 1].content);
      const part = window.__T.lbGet().allPartners.find(p => String(p.name).length > 3).name;
      const r1 = await run("get_partner_revenue", { name: part }), r2 = await run("leads_summary", { period: "all" });
      const pe = (r1.periods || [{}])[0]; const text = "Revenue for " + (r1.partner || part) + " (" + pe.label + ", as of " + r1.as_of + "): " + pe.earned_usd + ". Leads: " + r2.leads + ".\nNext: a | b";
      if (opts && opts.onText) opts.onText({ text });
      return { text }; });
    window.__sample.limits = async () => ({ tools: { maxCount: 24 }, rounds: 6 });
  }, { role, leadsMode, partners: [] });
  await page.goto("file://" + path.resolve(pagePath));
  await page.waitForFunction(() => window.__T && window.__T.ST.userState !== "loading" && window.__T.ST.leadState !== "loading", null, { timeout: 20000 });
  await page.waitForTimeout(600);
  const T = (fn, arg) => page.evaluate(fn, arg);
  const tool = (name, input) => T(async ([n, i]) => { try { return await window.__T.floT[n](i || {}); } catch (e) { return { __error: String(e.message) }; } }, [name, input]);
  const out = {};

  /* gates */
  const snapOk = await T(() => !!window.__T.lbGet());
  const leadsOk = await T(() => window.__T.ST.leadState === "ready");
  out.snapOk = snapOk; out.leadsOk = leadsOk;
  if (!snapOk) {
    for (const n of ["revenue_overview", "revenue_by_partner", "get_partner_revenue"]) { const r = await tool(n, { name: "x" }); check(n, "gate: snapshot not loaded", r.available === false && /not available for your access/.test(r.note || ""), JSON.stringify(r)); }
  }
  if (!leadsOk) {
    for (const n of ["leads_summary", "search_leads"]) { const r = await tool(n, {}); check(n, "gate: leads not loaded", r.available === false && /not available for your access/.test(r.note || ""), JSON.stringify(r)); }
  }

  if (snapOk) {
    const periods = await T(() => window.__T.lbGet().periods.map(p => ({ key: p.key, label: p.label })));
    const setUI = async (key, partner) => { await T(([k, p]) => { window.__T.setSection("payouts", false); window.__T.renderLB(); const sel = document.getElementById("lb-period"); sel.value = k; sel.dispatchEvent(new Event("change", { bubbles: true }));
      const ps = document.getElementById("lb-partner"); ps.value = p || ""; ps.dispatchEvent(new Event("change", { bubbles: true })); }, [key, partner || ""]); };
    /* the Earned total in the Payouts filter row (build 12 moved the period and partner selection, and Earned, from Revenue to Payouts) */
    const earnedTile = () => T(() => { const e = document.querySelector("#pay-tot .e"); return e ? e.textContent : null; });
    const topRows = () => T(() => [...document.querySelectorAll("#lb-top-b tr[data-n]")].map(tr => ({ name: tr.querySelector("td").textContent.trim(), earned: tr.children[1].getAttribute("title") })));

    /* revenue_overview: one comparison per period against the Earned tile */
    const ov = await tool("revenue_overview", {});
    check("revenue_overview", "shape", ov.as_of && ov.source && Array.isArray(ov.periods) && ov.periods.length === periods.length, JSON.stringify(ov).slice(0, 200));
    out.ovLen = JSON.stringify(ov).length;
    for (const p of periods) { await setUI(p.key, ""); const tile = num(await earnedTile()); const row = ov.periods.find(x => x.period === p.key);
      check("revenue_overview", "period " + p.key + " equals Earned tile", row && Math.round(row.earned_usd) === Math.round(tile), `${row && row.earned_usd} vs ${tile}`); }
    const one = await tool("revenue_overview", { period: periods[periods.length - 1].label });
    check("revenue_overview", "single period by label", one.periods && one.periods.length === 1 && one.periods[0].period === periods[periods.length - 1].key, JSON.stringify(one).slice(0, 150));
    const bad = await tool("revenue_overview", { period: "nonsense" });
    check("revenue_overview", "unknown period errors", /Unknown period/.test(bad.__error || ""), JSON.stringify(bad).slice(0, 150));

    /* revenue_by_partner vs Top partners rows */
    let nTop = 0;
    for (const key of [periods[0].key, periods[periods.length - 1].key]) {
      await setUI(key, ""); const rows = await topRows(); const t = await tool("revenue_by_partner", { period: key, limit: 20 });
      out.byLen = JSON.stringify(t).length;
      for (let i = 0; i < Math.min(5, rows.length); i++) { nTop++;
        const dn = rows[i].name.replace(/\s*\(.*\)\s*$/, ""); const r = t.rows[i];
        check("revenue_by_partner", `${key} row ${i + 1} equals Top partners`, r && Math.abs(r.earned_usd - num(rows[i].earned)) < 0.011 && (r.partner === rows[i].name || rows[i].name.startsWith(r.partner)), `${r && r.partner}:${r && r.earned_usd} vs ${rows[i].name}:${rows[i].earned}`); }
    }
    out.nTop = nTop;
    const capT = await tool("revenue_by_partner", { period: periods[0].key, limit: 2 });
    check("revenue_by_partner", "limit and not_shown", capT.rows.length === Math.min(2, capT.partners_matching) && capT.not_shown === Math.max(0, capT.partners_matching - 2), JSON.stringify([capT.shown, capT.not_shown, capT.partners_matching]));
    const all = await tool("revenue_by_partner", { period: periods[0].key, limit: 20 });
    check("revenue_by_partner", "at most 20 rows", all.rows.length <= 20 && all.shown === all.rows.length, all.rows.length);
    const nm = all.rows[0] ? all.rows[0].partner.split(" ")[0] : "a";
    const filt = await tool("revenue_by_partner", { period: periods[0].key, name: nm });
    check("revenue_by_partner", "name filter", filt.partners_matching >= 1 && filt.rows.length >= 1 && filt.matching_earned_usd <= filt.total_earned_all_partners_usd + 0.001, JSON.stringify(filt).slice(0, 150));

    /* get_partner_revenue vs partner + period selectors */
    const top3 = all.rows.filter(r => r.earned_usd > 0).slice(0, 3);
    const pnames = await T(() => window.__T.lbGet().allPartners.map(p => ({ name: p.name, dn: p.display_name || p.name })));
    let nPart = 0;
    for (const tr of top3) {
      const pn = pnames.find(x => x.dn === tr.partner || x.name === tr.partner); if (!pn) continue;
      const g = await tool("get_partner_revenue", { name: pn.name }); out.partLen = Math.max(out.partLen || 0, JSON.stringify(g).length);
      for (const p of periods.slice(0, 4)) { await setUI(p.key, pn.name); const tile = num(await earnedTile()); const row = (g.periods || []).find(x => x.period === p.key); nPart++;
        check("get_partner_revenue", `${pn.name.slice(0, 1)}* ${p.key} equals Earned tile`, row && Math.round(row.earned_usd) === Math.round(tile), `${row && row.earned_usd} vs ${tile}`); }
      const q = (g.periods || []).find(x => x.period !== "alltime" && /^q\d$/.test(x.period));
      if (q && g.monthly_series) { const qm = await T(k => window.__T.lbGet().periods.find(p => p.key === k).months, q.period); const s = g.monthly_series.filter(m => qm.includes(m.month)).reduce((a, m) => a + m.earned_usd, 0);
        check("get_partner_revenue", `series sums to ${q.period}`, Math.abs(s - q.earned_usd) < 0.02, `${s} vs ${q.earned_usd}`); }
    }
    out.nPart = nPart;
    const regOnly = await T(() => { const L = window.__T.lbGet(), have = new Set(L.allPartners.map(p => String(p.name).toLowerCase())); const d = window.__T.ST.docs.find(x => !have.has(String(x.name).toLowerCase()) && !(x.aliases || []).some(a => have.has(String(a).toLowerCase()))); return d ? d.name : null; });
    if (regOnly) { const r = await tool("get_partner_revenue", { name: regOnly }); check("get_partner_revenue", "registry partner without revenue rows", r.found_in_registry === true && r.revenue_rows === 0 && /no rows in the revenue snapshot/.test(r.note), JSON.stringify(r).slice(0, 200)); }
    const none = await tool("get_partner_revenue", { name: "zzzz-no-such-partner" });
    check("get_partner_revenue", "unknown partner", none.found === false && /No partner matches/.test(none.note), JSON.stringify(none).slice(0, 150));
    const empty = await tool("get_partner_revenue", { name: "" });
    check("get_partner_revenue", "empty name errors", /Give a partner name/.test(empty.__error || ""), JSON.stringify(empty).slice(0, 100));
    /* state restored: the page's own selection is unchanged by tool calls */
    const before = await T(() => JSON.stringify([window.__T.LBS.period, window.__T.LBS.partner]));
    await tool("get_partner_revenue", { name: top3[0] ? top3[0].partner : "a" }); await tool("revenue_overview", {});
    const after = await T(() => JSON.stringify([window.__T.LBS.period, window.__T.LBS.partner]));
    check("revenue_overview", "tools leave the page's period and partner unchanged", before === after, before + " vs " + after);
    out.restored = before === after;
  }

  if (leadsOk) {
    await T(() => { window.__T.setSection("leads", false); window.__T.renderLeads(); });
    const sum = () => T(() => { const t = document.getElementById("l-sum").textContent; const m = /^([\d,]+) leads?: ([\d,]+) approved, ([\d,]+) pending, ([\d,]+) lost; ([\d,]+) missing/.exec(t); return m ? m.slice(1).map(x => +x.replace(/,/g, "")) : null; });
    const tiles = () => T(() => [...document.querySelectorAll("#lead-tiles .tile")].map(t => ({ l: t.querySelector(".label").textContent, v: t.querySelector(".v").textContent })));
    const M3 = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    const isoOf = t => { const m = /^(\w{3}) (\d+), (\d{4})$/.exec(t); return m ? m[3] + "-" + String(M3.indexOf(m[1]) + 1).padStart(2, "0") + "-" + String(+m[2]).padStart(2, "0") : ""; };
    const rowsDom = async () => (await T(() => [...document.querySelectorAll("#l-body tr[data-id]")].map(tr => ({ company: tr.children[0].querySelector("span").textContent.trim(), partner: tr.children[1].textContent.trim(), status: tr.children[2].textContent.trim().toLowerCase(), referred: tr.children[5].textContent.trim() })))).map(r => Object.assign(r, { referred: isoOf(r.referred) }));
    const ui = async f => T(f0 => { const $ = id => document.getElementById(id); window.__T.ST.lf = { status: "all", cf: "", q: "", missing: false }; window.__T.renderLeads();
      if (f0.status) $("l-status").querySelector(`[data-k="${f0.status}"]`).click();
      if (f0.cf != null) { $("l-cf").value = f0.cf; $("l-cf").dispatchEvent(new Event("change", { bubbles: true })); }
      if (f0.q) { $("l-q").value = f0.q; $("l-q").dispatchEvent(new Event("input", { bubbles: true })); }
      if (f0.missing) $("l-miss").click(); }, f);

    /* leads_summary: tiles, status segments, partner filters, dated periods counted from the table's Referred column */
    await ui({});
    const ls = await tool("leads_summary", { period: "all" });
    out.lsLen = JSON.stringify(ls).length;
    const tl = await tiles(), tv = l => tl.find(t => t.l === l).v;
    check("leads_summary", "open equals Open leads tile", ls.open === num(tv("Open leads")), `${ls.open} vs ${tv("Open leads")}`);
    check("leads_summary", "paying equals Paying tile", ls.paying === num(tv("Paying")), `${ls.paying} vs ${tv("Paying")}`);
    check("leads_summary", "collected equals Collected tile", Math.round(ls.collected_usd) === Math.round(num(tv("Collected"))), `${ls.collected_usd} vs ${tv("Collected")}`);
    check("leads_summary", "missing deals equals tile", ls.missing_deal === num(tv("Missing deals")), `${ls.missing_deal} vs ${tv("Missing deals")}`);
    const s0 = await sum();
    check("leads_summary", "status counts equal the table summary", s0 && ls.leads === s0[0] && (ls.by_status.approved || 0) === s0[1] && (ls.by_status.pending || 0) === s0[2] && (ls.by_status.lost || 0) === s0[3], JSON.stringify([ls.leads, ls.by_status, s0]));
    const partners = ls.by_partner.filter(p => p.partner !== "No partner").slice(0, 3);
    for (const p of partners) { await ui({ q: p.partner }); const s = await sum(); const t = await tool("leads_summary", { partner: p.partner });
      check("leads_summary", "partner filter equals table summary", s && t.leads === s[0] && (t.by_status.approved || 0) === s[1] && (t.by_status.pending || 0) === s[2] && (t.by_status.lost || 0) === s[3] && t.missing_deal === s[4], JSON.stringify([t.leads, t.by_status, s])); }
    await ui({});
    const all = await rowsDom();
    const today = await T(() => { const d = new Date(); return [d.getFullYear(), d.getMonth()]; });
    const iso = (y, m, d) => y + "-" + String(m + 1).padStart(2, "0") + "-" + String(d).padStart(2, "0");
    const qs = Math.floor(today[1] / 3) * 3, ytdFrom = iso(today[0], 0, 1), qFrom = iso(today[0], qs, 1), tdy = await T(() => window.__T.todayLocal());
    for (const [per, from, to] of [["this_quarter", qFrom, tdy], ["ytd", ytdFrom, tdy], ["this_month", iso(today[0], today[1], 1), tdy]]) {
      const t = await tool("leads_summary", { period: per }); const n = all.filter(r => r.referred && r.referred >= from && r.referred <= to).length;
      check("leads_summary", per + " equals rows counted from the Referred column", t.leads === n, `${t.leads} vs ${n} (${from} to ${to})`); }
    const dated = await tool("leads_summary", { from: "2000-01-01", to: "2000-01-02" });
    check("leads_summary", "empty range", dated.leads === 0 && Array.isArray(dated.by_partner), JSON.stringify(dated).slice(0, 150));

    /* search_leads against the table under the same filters */
    const cfs = await T(() => [...new Set(window.__T.ST.leads.map(l => l.cf_status).filter(Boolean))].sort());
    const combos = [{ status: "approved" }, { status: "lost" }, { status: "pending" }, { missing: true }, { cf: cfs[0] }, { q: partners[0] ? partners[0].partner : "shop" }, { status: "approved", cf: cfs[0] }];
    out.slLen = 0;
    for (const c of combos) { await ui(c); const s = await sum(); const dom = await rowsDom();
      const t = await tool("search_leads", { status: c.status, cf_status: c.cf, missing_deal: c.missing ? true : undefined, query: c.q, limit: 20 });
      out.slLen = Math.max(out.slLen, JSON.stringify(t).length);
      const same = dom.slice(0, Math.min(5, t.rows.length)).every((r, i) => t.rows[i] && (t.rows[i].company === r.company || t.rows[i].company === ""));
      check("search_leads", "total equals table " + JSON.stringify(c), s && t.total_matching === s[0], `${t.total_matching} vs ${s && s[0]}`);
      check("search_leads", "first rows in table order " + JSON.stringify(c), same, JSON.stringify([t.rows.slice(0, 3).map(r => r.company), dom.slice(0, 3).map(r => r.company)])); }
    const lim = await tool("search_leads", { limit: 3 });
    check("search_leads", "limit and not_shown", lim.rows.length === Math.min(3, lim.total_matching) && lim.not_shown === Math.max(0, lim.total_matching - 3), JSON.stringify([lim.shown, lim.not_shown, lim.total_matching]));
    const big = await tool("search_leads", { limit: 99 });
    check("search_leads", "at most 20 rows", big.rows.length <= 20, big.rows.length);
    const badp = await tool("search_leads", { period: "someday" });
    check("search_leads", "unknown period errors", /Unknown period/.test(badp.__error || ""), JSON.stringify(badp).slice(0, 120));
    const pii = await tool("search_leads", { partner: partners[0] ? partners[0].partner : "x", limit: 20 });
    out.piiBlank = pii.rows.filter(r => r.company === "").length; out.piiRows = pii.rows.length;
  }

  /* end to end: the scripted model asks for tools, then answers; the loop passes the new tools through */
  const hasSample = await T(() => !!window.__T.FLO.avail);
  out.hasSample = hasSample;
  if (hasSample && snapOk && leadsOk) {
    const names = await T(async () => { const T = window.__T; T.FLO.msgs = []; T.FLO.open = false; T.floSend("How much did the first partner bring in?", {}); for (let i = 0; i < 60 && T.FLO.busy; i++) await new Promise(r => setTimeout(r, 100));
      const m = T.FLO.msgs[T.FLO.msgs.length - 1]; return { tools: window.__sampleLog[0].tools, calls: window.__sampleLog.filter(x => x.call).map(x => x.call + ":" + x.chars), text: m.text, state: m.state, err: m.err || "", system: window.__sampleLog[0].system }; });
    out.e2e = { tools: names.tools, calls: names.calls, state: names.state };
    const newT = ["revenue_overview", "revenue_by_partner", "get_partner_revenue", "leads_summary", "search_leads"];
    check("e2e", "all five new tools offered with the old ones", newT.every(n => names.tools.includes(n)) && names.tools.length === 18, JSON.stringify(names.tools));
    check("e2e", "model called two new tools and answered", names.calls.length === 2 && names.state === "done" && /^Revenue for /.test(names.text) && !names.err, JSON.stringify(names));
    check("e2e", "system prompt names the new areas", /revenue_overview/.test(names.system) && /as-of date/.test(names.system) && /Never estimate, extrapolate/.test(names.system) && /I do not have that/.test(names.system), "prompt");
    check("e2e", "answer follow-ups parsed", await T(() => { const m = window.__T.FLO.msgs[window.__T.FLO.msgs.length - 1]; return Array.isArray(m.follow) && m.follow.length === 2; }), "follow");
    const nameKeep = await T(() => window.__T.floT.get_partner_revenue({ name: window.__T.ST.docs[0].name }) && true);
  }
  out.chargedLine = await T(() => { window.__T.floMount && 0; const e = document.querySelector(".flo-cost"); return e ? e.textContent : null; });
  /* payout and contact scan over every tool result, for the fixture's canaries */
  const dump = await T(async () => { const T = window.__T, o = []; const L = T.lbGet(); const names = T.ST.docs.slice(0, 6).map(d => d.name);
    for (const n of ["revenue_overview", "revenue_by_partner", "leads_summary", "search_leads"]) { try { o.push(JSON.stringify(await T.floT[n]({ limit: 20 }))); } catch (e) {} }
    for (const nm of names) { try { o.push(JSON.stringify(await T.floT.get_partner_revenue({ name: nm }))); } catch (e) {} }
    return o.join("\n"); });
  out.dump = process.env.FLO_DUMP === "1" ? dump : undefined;
  out.keys = [...new Set((dump.match(/"([a-z_0-9]+)":/g) || []).map(s => s.slice(1, -2)))].sort();
  out.hits = { email: /@/.test(dump), canary: /31337/.test(dump), linkedin: /linkedin\.com/i.test(dump), phone: /\+\d[\d\s().-]{7,}\d/.test(dump) };
  process.stdout.write(JSON.stringify({ checks, errors, out }));
  await browser.close();
})().catch(e => { process.stdout.write(JSON.stringify({ fatal: String(e && e.stack || e).slice(0, 1500), checks, errors: [] })); process.exit(0); });
