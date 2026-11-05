# PartnerOS code

Infrastructure for PartnerOS, Chargeflow's partner operating system: the page builder, the loaders that produce the PartnerOS database docs, and the snapshot refresher. This repo holds code and schemas only. Everything that is data (payout text and dates, private links, footnote text, alias maps, test store names, the guard term list) lives outside it, in the dev workspace.

## Layout

| File | What it does |
|---|---|
| `template.html`, `build.py` | The page. `build.py` inlines seed data into the template and writes `partneros.html`. |
| `slim.py` | The snapshot shape the page reads; shared by `build.py` and `refresh.py`. |
| `refresh.py` | Snapshot refresher: `make-config`, `print-queries`, and the build of `snapshot.json`, `trend.json` and the slim docs. Standard library only. |
| `refresh-run.md` | The in-session run book for the twice-monthly refresh (run by a Sonnet agent in a Claude Code session; headless `claude -p` cannot load ArtifactData). |
| `shared.py` | Registry loader, PII patterns, and `load_local()` for the local data file. |
| `enrich.py`, `step4_build.py`, `agreements_build.py`, `portal_agreements_build.py`, `outreach_build.py`, `events_sync.py`, `seed_db.py` | Loaders that write db docs and batch manifests. |
| `step4_schema.md`, `events_schema.md` | Doc shapes. |
| `flo-voice.json` | Default openers for the page's outreach guard. |
| `guard.py`, `.githooks/pre-commit` | Keeps data out of the repo (see below). |

## Run

```
python3 build.py [check]                  # page; output path via CF_PARTNEROS_OUT
python3 enrich.py [--db-dir DIR --out DIR2 [--versions FILE] [--history-only]]
python3 step4_build.py                    # output dir via CF_STEP4_OUT
python3 agreements_build.py [--db-dir DIR]
python3 refresh.py make-config [--dir DATA_DIR] --out config.json
python3 refresh.py --help                 # the refresh itself; see refresh-run.md
python3 guard.py --all                    # or: python3 guard.py FILE...
```

## Local data

- `docs/finance/partneros/local-data.json` in the dev workspace (override with `CF_PARTNEROS_LOCAL`). A missing file or key is a hard error that names the path. It is gitignored there and must never be copied into this repo.
- `docs/finance/partneros/contracts/overrides.json` for agreement folder corrections.
- Dashboard inputs under `docs/finance/payouts/2026-q3/dashboard` (registry, actuals, config).

## Guard

`guard.py` fails on emails, LinkedIn URLs, phone shapes, dollar figures, comma-grouped numbers, font files, Claude artifact or routine URLs, and any term from the local guard list. Enable the hook once per clone: `git config core.hooksPath .githooks`.

## Not in git

`fonts/*.woff2` (licensed; copy from the chargeflow-design kit into `fonts/`) and `_qa/`. The full build log is in the dev workspace at `docs/projects/partneros/build-log.md`.
