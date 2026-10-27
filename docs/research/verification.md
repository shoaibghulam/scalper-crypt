# Verification — 2026-09-20

## Windows progress-save fix

Progress snapshots now use unique temporary files and bounded retries for Windows
access/sharing errors during atomic replacement. Report downloads read a snapshot
into memory and release the file before sending it to the browser. A real Windows
handle-lock test, transient/permanent-error tests, concurrent-writer tests and
download-snapshot tests pass, along with the pipeline tests (19 total). Reads also
retry Windows CRT access-denied errors during replacement.
Permanent permission errors still surface; the last committed report is retained.
BTC from failed-save experiment `dd4b3723e6ca409c941b4b67a62fb534` was rerun with
the original settings and frozen dates in `a637b00965704af1b14bdd9c0bf3479d`.
Recovery completed without errors; the original ETH/XRP results were preserved.

## Initial research release

- Backend regression: **1,497 passed**, two pre-existing dependency deprecation warnings.
- Frontend regression: **173 passed** across 18 test files.
- Backend scoped Ruff checks, frontend lint and production build: passed.
- Tests include a changed-holdout experiment that must preserve the same selection
  audit, finalist IDs and portfolio proposal; compiled/Python replay equivalence;
  known funding/P&L examples; forward-only paper replay and operator stop; worker
  lock exclusion; API validation; and rejected/live artifact guards.
- Public Binance/API smoke experiment: `0e3e7601d07245f881a9455762a37f9b`.
  BTC, ETH and XRP each completed seven days of 5m research ending September 19
  UTC, including funding, evolution, Optuna, walk-forward, robustness and final
  triple-cost testing. 31, 31 and 32 evaluations respectively; no data/job errors.
  All finalists were rejected by at least one gate. Losing results are retained.
- Export endpoint, frontend dev server and frontend-to-backend proxy returned 200.
- Benchmark: **10,000 evaluations on 2,016 bars in 4.253 seconds**, approximately
  2,351/s with warmed Numba compilation, including random generation, features
  and metrics. This excludes downloads, JSON archives and full robustness work.
  It does not establish performance on million-candidate or year-long workloads.
- No connected browser was available for visual inspection. UI interactions were
  verified through React component tests; visual browser QA remains unverified.
- Forward paper behavior was tested with deterministic future-candle fixtures.
  No strategy was manually promoted past failed gates for a real-time paper run.
  No live orders or live trading-engine activation occurred.
