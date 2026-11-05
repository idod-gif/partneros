# Events module: data model (from tracker.html, verified 2026-10-18)

Source of truth: `docs/recurring/events/tracker.html` (legacy, untouched). Everything below is copied from that file. The db version is produced by `scripts/partneros/events_sync.py`.

Correction to the brief: SEED covers three events, not two: ase-2026, dotdev-2026 and shopify-field-2026. Shopify Field is a real third event with its own SEED contacts.

## Enums

### Statuses (`STATUSES`, in order; index = `STATUS_ORDER`)
| # | id | label |
|---|---|---|
| 0 | not-started | Not started |
| 1 | requested | Requested |
| 2 | replied | Replied |
| 3 | booked | Booked |
| 4 | met | Met |
| 5 | followed-up | Followed up |
| 6 | won | Won |
| 7 | dead | Dead |

`statusOf(c)` = `overlay.status` or (`c.requested` ? `requested` : `not-started`). "Never downgrade" = a bump or autolog only applies when its status index is higher than the current one. `dead` has the highest index, so it is never overwritten by a bump. Manual changes through the drawer dropdown can go down and are recorded in `statusLog`.

### Interaction types (`INT_TYPES` order) and `INT_META`
| type | bump (status the interaction raises to, if higher) | short | glyph | cls |
|---|---|---|---|---|
| LinkedIn DM | null | DM | ↗ | |
| Cold email | requested | Email | ✉ | |
| Meeting request | requested | Req | ◇ | |
| Reply | replied | Reply | ↩ | in |
| Meeting | booked | Meeting | ◆ | milestone |
| Met at booth | met | Met | ⚑ | milestone |
| Follow-up email | followed-up | F/up | ↻ | |
| Other | null | Note | · | |

Unknown types render as glyph `·`, bump null. Logging an interaction also clears `nextAction` and `nextDue` when either was set.

### Checklist keys per event (`SEED_EVENTS[].checklist`, one item each)
| event | key | label | bumpsTo | outreach |
|---|---|---|---|---|
| dotdev-2026 | li-interaction | LinkedIn interaction | null | true |
| ase-2026 | ase-meeting-request | Meeting request (ASE app) | requested | true |
| shopify-field-2026 | li-connect | LinkedIn connect | requested | true |

Per-event extra items can be added in the app (`state.customChecklist[eventId]`, same item shape). Ticking sets `checks[key]` to an ISO timestamp and, if `bumpsTo` outranks the current status, raises the status and stores `prevStatus[key]` (the status before the tick) so an untick can revert it. `reachedOut(c)` = the event's `outreach:true` item is ticked.

### STALE_RULES and thresholds
`STALE_RULES = {requested:5, replied:3}` (days with no touch before the contact shows in the Due view; only these two statuses auto-flag). `STALE_TOUCH_DAYS = 14` (last touch goes bold in the table).

### SEED_EVENTS fields
`{id, name, dates, checklist:[{id,label,bumpsTo,outreach}]}`. Values: dotdev-2026 "DotDev Toronto" "Jul 21-22"; ase-2026 "ASE NYC" "Jul 27-28"; shopify-field-2026 "Shopify Field Program" "Sep 2026 ongoing". The tracker has no venue or city field; the db doc carries `venue` and `city` as null for now (Ido can fill later; do not invent).

## Source shapes

### SEED contact (all events)
`{id, event, tier (A|B|C), company, name, title, priority (bool), angle, requested (bool), sheetNotes?}`. `sheetNotes` is absent on 205 contacts (all shopify-field). `id` = `slug(company)|slug(name)`; 17 ids have an empty company part (`|name`). No contact has an email, `linkedin`, `attending` or `type` field; those are derived (see below).

### Overlay (`localStorage["cf-event-tracker-v1"].overlays[contactId]`)
`{status?, statusLog?:[{ts,from,to}], checks?:{<key>:ISO}, prevStatus?:{<key>:status}, interactions?:[{ts,type,note}] (older autolog entries: {type,note,at}), nextAction?:string, nextDue?:"YYYY-MM-DD", myNotes?:string}`.
Storage root: `{overlays, customContacts:[contact + custom:true], customEvents:[event], customChecklist:{<eventId>:[item]}}`. The boot migration copies `at` to `ts` (keeps `at`).

### Export payload v1 (Export JSON button, also the auto-save file)
`{app:"cf-event-tracker", version:1, exportedAt:ISO, state:{overlays, customContacts, customEvents, customChecklist}}`. Import also accepts the bare `state`. Expected path: `docs/finance/partneros/events/tracker-export.json`.

### Autolog blocks (appended `<script>`s; they write localStorage on every load)
1. Unnamed patch script (before `cf-ase-autolog`), array `IDS` of 27 dotdev ids: status `requested` when absent or not-started. No interaction, no check.
2. `<script id="cf-ase-autolog">`, four arrays at sentinels `/*IDS*/ /*CIDS*/ /*BIDS*/ /*PIDS*/` (the last items before each sentinel are the newest):
   - `IDS` (checks): set `checks["ase-meeting-request"]` = load time if unset; status `requested` if absent or not-started.
   - `CIDS` (connect): add interaction `{type:"Other", note:"Swapcard connect + Calendly invite", at:<load time>}` unless an interaction with that exact note exists; status `requested` if absent or not-started.
   - `BIDS` (booked): status `booked` when absent or one of not-started, requested, replied.
   - `PIDS` (partner invite): add interaction `{type:"Other", note:"Partner program invite (partners.chargeflow.io)", at:<load time>}` unless that note exists. No status change.
3. `<script id="cf-manual-dm-autolog">`: array `EV` (sentinel `/*MDIDS*/`) of `{id, status, type, ts, note}`; status raised only if higher; interaction `{ts,type,note}` added unless one with the same (ts,type) exists. 9 entries, all shopify-field, type "LinkedIn DM", ts 2026-09-17T12:00:00Z.
Autolog timestamps that say "load time" are not recoverable; the sync stamps them with the run's `imported_at` (see Limits in README).

## db docs

Doc ids: `doc_id` = the tracker id with `|` replaced by `__` and every character outside `[a-z0-9_-]` replaced by `_` (collision-checked; the original id stays in the doc as `id`).

### `events/<eventId>`
`{id, name, dates, venue:null, city:null, checklist:[key,...], checklist_items:[{id,label,bumpsTo,outreach}], counts:{total, by_tier:{A,B,C}, by_status:{<all 8 statuses>:n}, priority, attending_confirmed}, generated_at}`. Three docs: dotdev-2026, ase-2026, shopify-field-2026 (plus any export `customEvents`).

### `contacts/<docId>`
`{id, event, shard, name, company, title, tier, priority, type, angle, sheetNotes, linkedin, attending, requested, status, statusLog:[{ts,from,to}], checks:{key:ISO}, prevStatus:{key:status}, interactions:[{ts,type,note}], nextAction, nextDue, myNotes, custom, imported_at, updated_at, history:[]}`
- `shard` = `<event>__<tier>`. Shards: ase-2026__A/B/C, dotdev-2026__A/B/C, shopify-field-2026__A/B (and `__C` only if the export adds C). Each must stay under 950 docs; the sync fails otherwise.
- `type`: ASE only, from the leading bracket of `sheetNotes` (`[agency|media|network|super-affiliate · tier C · ... conf]`); null elsewhere.
- `attending`: from `[Attending: X]` in `sheetNotes`, lowercased first word group: `confirmed`, `likely`, `unconfirmed`, `no signal`; ASE contacts with `Attending: CONFIRMED - ...` map to `confirmed`; otherwise null.
- `linkedin`: first LinkedIn profile URL in `sheetNotes` (the "Profile:" URL), else null. `sheetNotes` itself is kept whole.
- `status` = effective status as the tracker would show it (overlay or autolog, falling back to `requested`/`not-started`).
- `history` starts as `[]`; the page appends `{at, by:"page", change:{field:{from,to}}, note?}` entries on edits (the Partners and candidates convention).
- `custom` true for contacts from the export's `customContacts`.
- Dropped from overlays: nothing renderable. `at` is converted to `ts` (the `at` key is not kept).

### Sharding rule
Collection `contacts` is queried by `shard` (`<event>__<tier>`), so a page loads one event tier at a time. No shard may exceed 950 docs (artifact db page limit headroom).

### Not in SEED (only reachable through the export)
`customContacts`, `customEvents`, `customChecklist` and every in-app overlay live only in the browser's localStorage. Without the export, 17 autolog ids (for example `downpay|diana-birsan`, `loyaltylion|charlie-casey`, the round-2 DotDev leads added by the boot migration) have no contact record; the sync lists the count and skips them. The export's `customContacts` become contact docs with `custom:true`.
