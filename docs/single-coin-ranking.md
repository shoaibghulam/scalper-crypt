# Single Coin strategy ranking

New Single Coin searches select the highest number of verified winning trades among enabled
strategies matching the chosen 1m or 5m entry interval. Eligibility requires at
least one verified closed trade, positive cumulative net P&L after fees, and no
latest-20 loss-rule flag. Event-level order-flow strategies are excluded.
Ties use win rate, trade count, then strategy ID. Profit amount does not affect ranking; positive net profit remains an eligibility check. No qualifying strategy means
no search starts; there is no fallback to an unqualified strategy.

This testnet experiment includes early winners rather than waiting for 20 trades.
Selections with fewer than 20 verified trades are labeled as provisional early
leaders. The trade count and net profit remain visible beside the win rate.

The backend recomputes ranking when Start is processed. The selected strategy and
its evidence are persisted with the search. Existing searches and positions are
not silently reassigned. A fresh confirmed signal, liquidity/spread checks,
account risk limits and budget sizing remain required. One successful entry ends
the search. Ranking summarizes historical testnet results; it is not a forecast.
