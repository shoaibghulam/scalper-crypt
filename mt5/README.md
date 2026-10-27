# ScalperDemo — first MT5 demo scalper

This is an experimental MQL5 Expert Advisor, not a proven profitable strategy.
It runs independently of the crypto dashboard. It does not use Binance keys,
change account leverage, or send trades through the Python engine.

## Strategy

- Default entry timeframe M1; M5 is also supported.
- Buy when EMA9 crosses above EMA21 on the last completed candle and RSI14
  is between 50 and 70. Sell on the reverse cross with RSI between 30 and 50.
- At most one entry attempt per new bar. The forming candle is not a signal.
- Stop distance is 1.5 × closed-bar ATR14, increased if needed to satisfy the
  broker's stop-distance rules and bid/ask spread. Target is 1.5 × stop distance.
- Close any EA-owned position after 15 minutes, on the next available tick.
- No averaging, grid, martingale, trailing stop or automatic reversal.

This is a straightforward baseline for forex testing. It does not reproduce
the crypto trader's inferred intrabar spike-reversal approach.

## Safeguards and units

- Only demo accounts or Strategy Tester are accepted; real and contest accounts
  are rejected. The account type is checked again on every tick.
- `EnableEntries=false` by default. Turning it off stops entries but the EA
  continues managing its own existing positions while attached.
- `RiskPercent=0.25`: estimated price loss at the stop as a percentage of account
  equity. Commission, swaps, gaps and slippage can increase the actual loss.
- Sizing uses the broker's `OrderCalcProfit`, rounds volume down, and skips if
  minimum lot exceeds the budget. Maximum volume defaults to 0.10 lots.
- Margin requirement must fit within 80% of available margin.
- `MaxSpreadPoints=20` is in broker POINTS, not pips. On a five-digit EURUSD
  quote, 20 points is 2 pips. Different symbols need different settings.
- Spread must also be no more than 20% of the planned stop distance.
- Daily entry cutoff: equity below 98% of broker-midnight balance. Includes all
  account activity. Do not deposit/withdraw during comparative tests; cashflows
  affect this measure. This blocks new entries; it does not liquidate the account.
- Any existing account position or pending order blocks entries. Run only one
  instance for this initial version; separate terminals have no shared lock.
- Stops and targets are submitted with the entry. An owned position observed
  without either protection is closed when trading permissions allow.
- Failed/unconfirmed entry responses block further entries for the attachment.
  Inspect Trade, History and Experts before reattaching. There is no blind retry.
- Broker-side SL/TP remain after detach. Time exits require MT5 and the EA to
  remain running and receive ticks. The EA does not close other/manual positions.

## Run a historical test first

1. Open the broker's MT5 and sign into your DEMO account locally.
2. In Navigator → Expert Advisors, right-click Refresh. Look for
   `TheScalper/ScalperDemo`. Installation copies the source and compiled EA only;
   it does not attach or activate it.
3. Press Ctrl+R. Select ScalperDemo, the broker's exact EURUSD symbol (which may
   have a suffix), M1, and a fixed historical date range.
4. Choose **Every tick based on real ticks** when available. Set the deposit,
   currency and leverage to match your intended demo account. Check broker
   commission, spread, swap and tick coverage in the report.
5. Inputs: set `EnableEntries=true`. Keep the initial parameters fixed rather
   than optimizing until the report turns profitable.
6. Start, then inspect Results/Graph/Journal. A small account may legitimately
   produce no orders because minimum lot exceeds the risk allowance.
7. Save the tester report. Reserve a later date range for validation.

## Forward demo test

Attach the EA to one demo EURUSD chart, enable Algo Trading and the EA's algorithmic
trading permission, then explicitly set EnableEntries=true. The chart status
explains waiting, spread, sizing and account-limit conditions. Use the account
History tab and Experts journal to review fills, SL/TP and costs. No credentials
need to be embedded in the EA or sent in chat.

MT5 native history/report export is available. Automatic synchronization into
our SQLite database/dashboard is not implemented in this first version.

## Verification

Compiled with the installed MetaEditor. See `compile.log` for the compiler result.
Source review covers closed-bar buffer order, demo-only checks, lot rounding,
ownership checks, protective orders and server return codes. No broker test,
historical performance run or automatic demo order has been executed by the
assistant; compile success is not evidence of profitability or execution quality.

Official references:
- https://www.mql5.com/en/docs/trading/ordercalcprofit
- https://www.mql5.com/en/docs/standardlibrary/tradeclasses/ctrade/ctradepositionopen
- https://www.metatrader5.com/en/terminal/help/algotrading/tick_generation
