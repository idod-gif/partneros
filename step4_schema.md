# PartnerOS Step 4 doc shapes (Payouts, Leads, Settings)

Written by `step4_build.py`. Output: `docs/finance/partneros/step4/<collection>/<doc_id>.json` plus `batch-NN.json` manifests (50 `set` entries each, enrich.py format). Nothing is pushed to the db by the builder. All dates ISO; money in USD, 2 decimals; `generated_at` and `updated_at` are UTC `YYYY-MM-DDTHH:MM:SSZ`. No person fields anywhere (no contact names, emails, phones, LinkedIn, job titles, rep names).

## payouts/<id>
ids: `2025`, `2026-Q1`, `2026-Q2`, `2026-Q3`, `2026-Q4`.
```
{id, quarter, label,
 status: "paid"|"recorded"|"approved"|"in_review"|"estimate"|"open",   // approved = final figures, not paid yet (Q3 2026 from 2026-10-21)
 basis,            // string, e.g. "25% of Alerts gross margin and Automation revenue (paid invoices)"
 rate,             // percent number or null (null for 2025, Q1 and Q2: rates were mixed or unrecorded; 25 for Q3 and Q4)
 total_due, total_paid,   // number or null
 paid_on,          // ISO date or null
 payer,            // string or null
 partners_count, statements_count,  // int or null
 run_folder,       // repo-relative path or null
 sources: [string],
 notes,            // string
 history: [],      // page-written edit log, starts empty
 computed_total,   // ADDED: sum of registry history payout entries for the quarter (cross-check), number
 expected_total,   // ADDED: the figure the brief expects (same as total_due or total_paid)
 reconciles,       // ADDED: bool, computed_total == expected_total within 0.01
 due_dates,        // ADDED: {ilan_signoff, reem_pays_by} ISO dates, 2026-Q3 only, else null
 generated_at, updated_at}
```
Money semantics: `total_due` = what is owed for the quarter (null when not yet known, Q4); `total_paid` = what is marked paid. It is 0 for estimate, in_review and open, and null for Q1 and Q2 (recorded, but no paid date entered, so paid is unknown; Q2 was reportedly paid outside Partner.io, see notes).

## leads/<lead_id>   (doc id = Partner.io lead id as string)
```
{lead_id (int), partner, partner_id (int|null, via registry name and aliases), company, store_handle,
 status, bucket, cf_status, paying (bool), collected_usd, referred_at, lost_at,
 hubspot_company_id, hubspot_company_url, source,
 has_contact (bool), missing_deal (bool),
 notes: "", history: [], updated_at}
```
No person fields. `status` is the Partner.io lead status, `bucket` open|paying|lost, `cf_status` the Chargeflow-side status.

## settings/program
```
{commission:{rate:25, basis, decided_on, communicated, playbook_note},
 tiers:[{tier, count}],
 partner_types:[{id, name, count}],          // id = Partner.io partner type id (null if no id known)
 revenue_sync:{routine_id, url, schedule, mode, log_artifact},
 refresh_routine:{routine_id, url, schedule},
 links:{leadership_dashboard, partnerio_roadmap_artifact},
 generated_at, updated_at}
```

## settings/roadmap
```
{items:[{n, title, tier, status:"open"|"done"|"blocked", decision_pending (bool), note}],
 decisions_open:[string], source, generated_at}
```

## Q3 2026 final (2026-10-21)

When `docs/finance/payouts/2026-q3/dashboard/q3-actuals.json` exists, `step4_build.py` writes `payouts/2026-Q3` as status `paid` (total_due = total_paid = the final total, paid_on = the latest per-partner date, `shared.paid_on_quarter()`, partners_count = partners on the final sheet, statements_count null, sources = the final-sheet xlsx under inputs/), and `enrich.py` writes registry history entries for 2026-Q3 with source label `source_labels.q3_final` in local-data.json: status `paid` (per-partner `paid_on` from local-data.json `paid_on`) with the final payout for partners on the sheet, status `not_eligible` with payout 0 for every other partner on the 32-statement estimate. They replace the estimate entries (`source_labels.q3_estimate`) in place. `payouts_summary` gains `q3_final` and `q3_status`. Payouts can carry half cents where the final sheet does (3 decimals). `enrich.py --db-dir DIR --out DIR2 --versions FILE --history-only` builds update payloads for changed docs only, Q3 entries only.
