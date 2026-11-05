# PartnerOS dashboard refresh, local run (in-session)

Run by a Sonnet agent inside a Claude Code session started in `/Users/idod/dev`. Headless `claude -p` cannot load ArtifactData, so there is no scheduler: the run is started by hand (or from a recurring Next loop) on the 1st and 15th. Replaces the retired cloud routine; its prompt is kept in `docs/projects/partneros/build-log.md`.

Large tool results may be saved by the harness under a tool-results directory; read them in place (Read, offset and limit) or with read-only python3 or jq, never edit them.

MODE: WRITE. The routine writes only `snapshots/latest`, `snapshots/stores-<i>` (only when sharded), `snapshots/trend` and `runs/<TODAY>`, and the snapshots only if the step 4 sanity checks pass. PartnerOS has no `cache/partnerio` collection: write no lead cache. Set MODE to REPORT-ONLY at the top of the run for a dry run: do steps 0 to 4, write no doc, report what would be written.

Tools: Partner.io MCP (`get-leads`, `get-partners`, `get-partner-reps`), HubSpot MCP (`query_crm_data`, `tool_guidance`), ArtifactData, ToolSearch, Read, Write, and Bash with Python. Missing tool: stop and report. The repo is present (cwd `/Users/idod/dev`); `.env` is not needed and is never read.

Refreshes the snapshot PartnerOS's Dashboard reads; 1st and 15th. `TODAY` is the run date (YYYY-MM-DD, UTC). `WORK` is `docs/finance/partneros/refresh-<TODAY>/` (create it; it is gitignored and holds sensitive data, never copy its files elsewhere). Every file named below lives in `WORK`; `cd` into `WORK` for steps 2 and 3 and call the code by absolute path (`/Users/idod/dev/scripts/partneros/refresh.py`).

## Hard rules

1. Read-only against Partner.io and HubSpot. Never call any update, create or delete tool.
2. Never write any db doc other than the four above. No contact fields in any doc.
3. Never paste raw rows or SQL results into the report.
4. Never edit refresh.py, slim.py, local-data.json, config or SQL (except splitting id lists in step 2).
5. No Slack, no Gmail, no other writes anywhere.
6. If a step fails, stop and report the step and the error text verbatim (step 4 failures write a `no-writes` run doc instead).

## 0. Load state

Artifact URL: read key `links.partneros_artifact` from `docs/finance/partneros/local-data.json` (never copy the value into a report or a doc). Load the tool with ToolSearch `select:ArtifactData`. Every read and write uses that url.

In this local session ArtifactData accepts `out_dir`: pass `out_dir` = `WORK` on every read, so the doc lands as a file and never enters the conversation. Read the saved files with python3 or Read; do not paste their contents into the report.

1. Config: `python3 /Users/idod/dev/scripts/partneros/refresh.py make-config --out WORK/config.json` (reads the local dashboard data dir and `local-data.json`; a missing local file or key is a hard error, stop and report the message).
2. Code: `refresh.py` and `slim.py` are run straight from `/Users/idod/dev/scripts/partneros/` (`--slim-out` finds `slim.py` next to `refresh.py`) (no db copy needed).
3. `get` `snapshots/latest` with `out_dir`; keep its `version` and `generated`. If it has `stores_shards: n` and no `stores`, `get` `snapshots/stores-0` to `stores-<n-1>` and join their `stores` in order (each shard's `generated` must match). Save the result as `prev.json`. Keep the version of every `snapshots/stores-<i>` that exists (try `get` on `stores-0` upward until one is missing). If `snapshots/latest` is missing: no baseline; step 4 says "first run, no baseline".
4. `get` `snapshots/trend` with `out_dir`; save its data as `trend-prev.json`; keep its `version`. If missing, skip `--trend-prev` and say so.
5. `list` `runs` (limit 200, `out_dir`); among docs with `routine: "dashboard-refresh"` take the latest `today` and keep its `leads_total`. None: skip the lead-count check in step 1 and say so.
6. `list` `partners` (limit 1000, `out_dir`, page with `cursor` until no `next_cursor`); join the docs' `data` objects (each with its `id`) into a JSON array in `registry.json` with python. If empty, omit `--registry` and say so.

If `snapshots/latest` is the only thing missing, continue as a first run. If `make-config` fails, stop and report.

## 1. Partner.io leads

Call `get-leads` with `per_page: 100`, pages 1, 2, 3 ... until `leads` is empty. Pages are large; read by jq, never dump them. Call `get-partners` once (all pages) and `get-partner-reps` once (all pages). Map partner names by: lead.partner_rep_id to rep.partner_id to partner name (lead.partner_id when present wins; else the rep name). Map each lead to:

`{"lead_id": id, "store": name or company, "partner": partner display name, "status": status, "hs_id": customer_id or null}`

Drop test leads: any name containing "test" (case-insensitive), plus every exact name in `WORK/config.json` field `test_leads`. Keep the count as `test_dropped`. Save `leads.json` as `{"pulled_at": "<TODAY>", "leads": [...]}`. Count by status and linked (hs_id not null).

Stop and report if the count differs from the response `total` (after the test drop) or from the previous `leads_total` by more than 10 percent.

## 2. HubSpot batches

```
python3 /Users/idod/dev/scripts/partneros/refresh.py print-queries --config config.json --leads leads.json --registry registry.json --today TODAY
```

It prints SQL under `-- MONTHLY BATCH i/n`, `-- LIFETIME BATCH i/n SUM`, `-- LIFETIME BATCH i/n MIN` and `-- COMPANY-DATES BATCH i/n`. Run each with `query_crm_data` (`sql`, `verbosityLevel: LOW`; call `tool_guidance` once first if asked). Target files:

- MONTHLY batch N: `monthly<N>.txt`.
- LIFETIME batch N: SUM and MIN results both in `lifetime<N>.txt` (any order).
- COMPANY-DATES batch N: `companydates<N>.txt` (keep `LIMIT 500`).

Each query_crm_data result is either inline in the tool result or saved by the harness to a tool-results file. Inline: save it verbatim with the Write tool to the named file, header line included. In a file: copy it with a read-only Python command into the named file. Never retype rows.

If a result says "Showing 500 of N" with N > 500, split that batch's id list in two and run both halves as separate files. Change nothing else in the SQL; dates use BETWEEN.

HISTORY batches print only when config `history.pull` is true (normally false). If printed, save to `history<N>.txt` and pass `--history history*.txt`.

## 3. Build

```
python3 /Users/idod/dev/scripts/partneros/refresh.py --config config.json --leads leads.json --monthly monthly*.txt --lifetime lifetime*.txt --company-dates companydates*.txt --registry registry.json --today TODAY --trend-prev trend-prev.json --out snapshot.json --slim-out slim.json
```

Outputs: `snapshot.json` (full, local only), `trend.json` next to it, `slim.json` (the `snapshots/latest` doc) and, only when the slim doc is over 240000 bytes, `slim-stores-<i>.json` shards (then `slim.json` has `stores_shards` and no `stores`). Exit 2 means truncation, a parse warning or no rows: stop and report stderr verbatim. Any other non-zero exit: same.

## 4. Sanity thresholds

Compute from `snapshot.json` and from `prev.json` the same way (stores under `stores`; each has `scope`, `by_month` (a missing month is 0), `lifetime`):

- `payout_stores` = stores with `scope` "payout".
- `q3_earned` = sum over payout stores of `by_month` for 2026-07, 2026-08, 2026-09.
- `current_month_earned` = same sum for the month of TODAY.
- `alltime_earned` = sum over all stores of `lifetime.total` (null counts 0).

All must hold:

1. `payout_stores` within 20 percent of previous.
2. Q3 (closed) within 2 percent of previous, compared like for like: over payout stores present in both (same `hs_id`, else `lead_id`). Report Q3 of new payout stores separately; it is not a failure.
3. `alltime_earned` not lower than previous by more than 1 percent.
4. No month before the previous snapshot's last full month differs by more than 5 percent (earned, payout stores, like for like as in 2).

No `prev.json`: skip the checks, `sanity` = "pass", `previous` = null, and say "first run, no baseline" in the report. If any check fails, write no snapshot doc: `sanity` = "fail: <check, previous, now>", write the run doc with status `no-writes` (5.3), and report "NO WRITES: <check, previous, now>".

## 5. Write

1. Sizes: every `slim*.json` under 240000 bytes (`wc -c`), else stop and report. Shards first: `set` each `snapshots/stores-<i>` with `file_path`, `if_version` when it existed. Then `set` `snapshots/latest` with `file_path` `slim.json` and `if_version` from step 0 (none if it did not exist). On a version conflict, re-read once; if it changed, stop and report.
2. `set` `snapshots/trend` with `file_path` `trend.json` and `if_version` from step 0. Under 120 KB or skip it; `months` contiguous from 2024-08 to the current month, `forecast.months` the next 5 (report any `forecast.warning`). If only this write fails, report it and continue.
3. `set` `runs/<TODAY>`:

```
{routine: "dashboard-refresh", ran_at, today, leads_total, leads_linked, test_dropped, monthly_batches, lifetime_batches,
 rows_monthly, rows_lifetime, snapshot_bytes, snapshot_shards,
 totals: {q3_earned, current_month_earned, alltime_earned, payout_stores},
 previous: {q3_earned, current_month_earned, alltime_earned, payout_stores} or null,
 sanity, status: "written" | "no-writes" | "error", error}
```

`ran_at` UTC ISO; `leads_*` after the test drop; `rows_*` parsed data rows; `snapshot_bytes` of `slim.json` plus shards; `snapshot_shards` 0 when not sharded; `error` null unless status is `error`. Write it even when step 4 fails.

Then `get` `snapshots/latest`; if `generated` is not TODAY, stop and report.

## 6. Report (final message)

Under 15 lines:
- Mode: WRITTEN or NO WRITES (reason).
- Counts: leads, linked, by status; batches and rows. Registry line from the build output.
- Snapshot bytes and shards; `snapshots/trend` bytes written or failed.
- Totals: Q3, current month, all time, payout stores; each old to new (or "first run, no baseline"), and Q3 of new payout stores.
- Sanity: the four checks, pass or fail with numbers.
- `runs/<TODAY>` written.