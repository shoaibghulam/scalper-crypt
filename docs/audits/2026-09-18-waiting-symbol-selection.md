# Waiting-symbol selection fix

The form required a live signal even when creating a continuous bot, although
continuous execution already supported waiting for later signals. This made
short-lived research signals unnecessarily hard to configure from the UI.

- Waiting symbols are now selectable from a fresh scan. One-shot submission
  still requires a ready signal. Continuous mode explicitly permits waiting.
- Continuous creation persists the mode before acknowledgement and reports a
  watching bot; it does not try to place an immediate order. Later candle cycles
  use the existing fresh-signal, account, execution and protection checks.
- Candle loading checks close timestamps rather than discarding the last row
  unconditionally. Testnet can omit a forming row, so the previous behavior
  incorrectly removed the newest closed candle.
- Expiry only changes a previously ready setup; it no longer masks unrelated
  waiting reasons in backend responses or the frontend.
- A transient account-refresh failure may recover after a complete subsequent
  clean refresh, with verified protection, no pending entries/closures, valid
  trading permissions and successful persistence. Explicit halts remain binding.

Verification: 969 backend tests, 83.48% coverage, 84 frontend tests, frontend
production build/lint, Ruff and Mypy passed. An existing timing-sensitive indicator
test failed once during the first full run; the subsequent full run passed.

The testnet application was restarted with existing bot state preserved.
No bot or exchange position was created to exercise the UI. Refresh the browser,
select a waiting symbol, enable Continuous mode and create the watching bot.
