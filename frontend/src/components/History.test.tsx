import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { History } from "./History";
import type { ClosedTrade, History as HistoryData } from "../lib/api";

/**
 * What the history tab must not do.
 *
 * Three of these guard against a screen that reads more confidently than the
 * data supports: undefined metrics rendered as zero, a perfect profit factor
 * claimed from a handful of trades, and an empty table after a restart that
 * looks like a strategy which never traded.
 */

function aTrade(overrides: Partial<ClosedTrade> = {}): ClosedTrade {
  return {
    symbol: "SOLUSDT",
    bot: "bot-1",
    strategy: "vwap_band",
    side: "LONG",
    quantity: 2.36,
    entry: 105.61,
    exit_price: 106.57,
    leverage: 5,
    realised: 1.94,
    fees: 0.22,
    outcome: "tp",
    opened_at: 1758150000,
    closed_at: 1758150420,
    duration_seconds: 420,
    is_win: true,
    roe_pct: 8.7,
    ...overrides,
  };
}

function aHistory(overrides: Partial<HistoryData> = {}): HistoryData {
  return {
    trades: [aTrade()],
    summary: {
      trades: 1,
      win_rate: 100,
      profit_factor: null,
      expectancy: 1.94,
      net: 1.94,
      fees: 0.22,
      tp_hits: 1,
      sl_hits: 0,
      wins: 1,
      losses: 0,
    },
    persistent: false,
    ...overrides,
  };
}

describe("History", () => {
  it("renders an undefined profit factor as unknown rather than zero", () => {
    // Nothing has lost yet, so the ratio has no denominator. A zero here would
    // read as a strategy that loses everything.
    render(<History history={aHistory()} />);
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("warns that a small sample cannot be judged", () => {
    render(<History history={aHistory()} />);
    expect(screen.getByText(/too few to judge/)).toBeInTheDocument();
  });

  it("drops the sample warning once there are enough trades", () => {
    const history = aHistory();
    render(
      <History
        history={{ ...history, summary: { ...history.summary, trades: 120 } }}
      />,
    );
    expect(screen.queryByText(/too few to judge/)).not.toBeInTheDocument();
  });

  it("reports fees as a share of gross profit", () => {
    // 0.22 of a 2.16 gross is 10 percent. Stated because at this holding period
    // the fee is a large part of the outcome, not a rounding detail.
    render(<History history={aHistory()} />);
    expect(screen.getByText(/10% of gross profit/)).toBeInTheDocument();
  });

  it("says history is lost on restart while it lives in memory", () => {
    render(<History history={aHistory()} />);
    expect(screen.getByText(/cleared when the engine restarts/)).toBeInTheDocument();
  });

  it("shows both ends of the trade, not only the result", () => {
    render(<History history={aHistory()} />);
    expect(screen.getByText("105.61")).toBeInTheDocument();
    expect(screen.getByText(/106\.57/)).toBeInTheDocument();
  });

  it("labels an exit that matched neither level as other", () => {
    // Closed by hand, most often. Filing it under stops would corrupt the
    // exit counts in the summary above.
    render(<History history={aHistory({ trades: [aTrade({ outcome: null })] })} />);
    expect(screen.getByText("unclassified")).toBeInTheDocument();
  });

  it("distinguishes an empty history from an absent one", () => {
    const history = aHistory({ trades: [] });
    render(<History history={history} />);
    expect(screen.getByText("No closed trades yet")).toBeInTheDocument();
  });

  it("says it is waiting when the engine has not reported at all", () => {
    render(<History history={undefined} />);
    expect(screen.getByText(/Waiting for the engine/)).toBeInTheDocument();
  });
});


it("labels legacy estimated trades as unverified", () => {
  render(<History history={aHistory({ trades: [aTrade({ verified: false })] })} />);
  expect(screen.getByText(/Unverified estimate; excluded from totals/)).toBeInTheDocument();
});

it("combines local date and outcome filters, recalculates totals, and resets", () => {
  const timestamp = (date: string) => new Date(date).getTime() / 1000;
  render(<History history={aHistory({ trades: [
    aTrade({ symbol: 'WINUSDT', closed_at: timestamp('2026-09-19T23:59:59'), realised: 2 }),
    aTrade({ symbol: 'LOSSUSDT', closed_at: timestamp('2026-09-20T00:00:00'), realised: -1 }),
    aTrade({ symbol: 'ESTIMATEUSDT', closed_at: timestamp('2026-09-19T12:00:00'), verified: false, realised: 100 }),
  ] })} />);
  fireEvent.change(screen.getByLabelText('From date'), { target: { value: '2026-09-19' } });
  fireEvent.change(screen.getByLabelText('To date'), { target: { value: '2026-09-19' } });
  fireEvent.change(screen.getByLabelText('Result'), { target: { value: 'win' } });
  expect(screen.getByText('WINUSDT')).toBeInTheDocument();
  expect(screen.queryByText('LOSSUSDT')).not.toBeInTheDocument();
  expect(screen.queryByText('ESTIMATEUSDT')).not.toBeInTheDocument();
  expect(screen.getByText(/Showing 1 of 3/)).toBeInTheDocument();
  expect(screen.getByText(/1 trade.*too few/)).toBeInTheDocument();
  expect(screen.getByText('2026-09-19')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Clear filters' }));
  expect(screen.getByText('LOSSUSDT')).toBeInTheDocument();
});

it("combines search, strategy and side and explains invalid dates", () => {
  render(<History history={aHistory({ trades: [aTrade(), aTrade({ symbol: 'BTCUSDT', strategy: 'other', side: 'SHORT' })] })} />);
  fireEvent.change(screen.getByLabelText('Search symbol or bot'), { target: { value: 'btc' } });
  fireEvent.change(screen.getByLabelText('Strategy'), { target: { value: 'other' } });
  fireEvent.change(screen.getByLabelText('Side'), { target: { value: 'SHORT' } });
  expect(screen.getByText('BTCUSDT')).toBeInTheDocument();
  expect(screen.queryByText('SOLUSDT')).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('From date'), { target: { value: '2026-09-20' } });
  fireEvent.change(screen.getByLabelText('To date'), { target: { value: '2026-09-19' } });
  expect(screen.getByRole('alert')).toHaveTextContent('From date must be on or before To date');
  expect(screen.getByText('No trades match these filters.')).toBeInTheDocument();
});

it("shows unclassified exits explicitly instead of an unexplained zero pair", () => {
  const history = aHistory();
  render(<History history={{ ...history, summary: { ...history.summary, trades: 44, tp_hits: 0, sl_hits: 0 } }} />);
  expect(screen.getByText('44 closed')).toBeInTheDocument();
  expect(screen.getByText('0 target / 0 stop / 44 unclassified')).toBeInTheDocument();
  expect(screen.getByText(/exit order type not verified/)).toBeInTheDocument();
});
