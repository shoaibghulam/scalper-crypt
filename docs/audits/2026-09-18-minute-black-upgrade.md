# Minute Black upgrade verification

Status: implementation upgraded; **real-money release remains gated on supervised testnet acceptance and strategy validation**. The user confirmed keeping the existing Minute Black strategy. No exchange orders, deployment or running-engine restart were performed.

## Delivered

- Stronger Minute Black signal validation: EMA slope/real close, baseline volume, valid candle geometry, real structure invalidation, chase limit, 20-second submission expiry, buffered structural stop, no fallback bracket.
- Durable per-symbol candle deduplication and full entry intents. Recovery queries the original order, cancels active/partial entries, matches exposure, protects valid recovered positions and refuses ambiguous results. Flat position REST alone cannot prove a filled trade closed.
- Account preflight: permission, isolated margin, equity/available margin, fee-inclusive planned-risk budget, aggregate initial-margin limit, daily equity-loss latch, exchange maintenance-tier and market-quantity checks.
- Opt-in continuous operation, with stop controls and no re-entry before settlement.
- Confirmed PANIC and fresh-reconciliation resume through API/dashboard; unrelated positions remain outside PANIC's ownership scope.
- Account-key fingerprint checks, full retained trade history, bounded seven-day-window fill pagination.
- Credential-free causal pattern replay, including fees, adverse slippage, stop-first ambiguous bars and next-bar entries.
- Windows/Python 3.13 dependency snapshot and CI checks. Remote CI execution has not been performed.

## Verification

Final test results are filled in below after the last local run. Tests use temporary state and mocked exchange clients. Integration tests cover risk ? entry ? bracket ? persistence, timeout intent survival, stop/halt suppression, PANIC ownership and stale reconciliation refusal. Component tests cover PANIC confirmation, unavailable controls and visible failures. Replay tests check no entry before signal close, future-bar independence, ambiguous exits and malformed history.

The first coverage run exposed a gap (79.20%). Added behavioral tests of the engine's actual creation and recovery path; the coverage threshold was retained at 80%, not lowered or bypassed.

## Remaining release gates

1. Supervised Binance testnet acceptance with real API response shapes and account-specific permissions: open/close, partial fill, lost acknowledgement, restart, conditional protection, PANIC and resume. Local mocks do not prove this.
2. Representative historical/out-of-sample strategy replay and forward observation. No profitability claim is made, and the replay does not reproduce the entire live liquidity scanner, funding or exchange fills.
3. PAPER mode remains non-executing; a live paper broker is not implemented. The offline replay is a separate research tool.
4. Legacy unknown intents without a saved plan, orders outside exchange retention, non-USDT commission accounting, manual exposure changes and saturated history windows still require explicit reconciliation. They remain blocked rather than being guessed away.
5. Operation is single-account and loopback-only. Remote authentication, multi-user service deployment, cross-machine leases and user-data-stream recovery are outside this release. Position monitoring polls REST, while protection is exchange-held.
6. First startup with older unbound state needs careful account reconciliation; a later API-key change is refused against the saved fingerprint. Back up state and stop the old engine before upgrading. Never restore stale live-position records during rollback.

Read [rules, configuration and operating guide](../video%20docs/the-minute-black-upgrade.md) before launch. The source/test backup for this pass is `var/production-upgrade-backup-20260918/`; no runtime state or secrets were copied there.

## Final local results

Backend: **813 tests passed**, **80.85% coverage** (80% gate retained), Ruff passed, strict mypy passed across **56 source files**. Two dependency deprecation warnings remain.

Frontend: **81 tests passed** across seven files; production build and lint passed.

No authenticated exchange acceptance run, live orders, deployment, or remote CI run was performed.
