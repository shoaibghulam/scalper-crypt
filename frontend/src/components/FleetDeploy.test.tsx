import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { FleetDeploy } from './FleetDeploy';
import type { AppState } from '../lib/api';

/**
 * One strategy, one amount, one leverage, applied to every coin at once.
 *
 * The dangerous part is not the deploy, it is the arithmetic: the amount is
 * PER COIN, because the engine caps a bot at five symbols and each bot posts
 * its own margin. Typing 100 and picking 500 coins commits 50,000, and an
 * operator who reads "100" as the total finds that out from a margin call.
 * Most of these tests are about that number being impossible to miss.
 */

const STRATEGIES = {
  default: 'ctk-1',
  strategies: [
    { id: 'ctk-1', name: 'CTK 1', summary: '', assumptions: [], timeframe: '1m' },
    { id: 'ctk-5', name: 'CTK 5', summary: '', assumptions: [], timeframe: '5m' },
    { id: 'lab-01', name: 'Lab 01', summary: '', assumptions: [], timeframe: '5m',
      backtest_only: true },
  ],
};
const COINS = { source: 'test', symbols: ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT'] };

function stub(overrides: Record<string, unknown> = {}) {
  const calls: { url: string; body?: unknown }[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, body: init?.body ? JSON.parse(init.body as string) : undefined });
    const match = Object.keys(overrides).find(key => url.includes(key));
    if (match) {
      const payload = overrides[match] as { __status?: number };
      const status = payload?.__status ?? 200;
      return { ok: status < 400, status, json: async () => payload, text: async () => 'refused' };
    }
    const payload = url.includes('/strategies') ? STRATEGIES
      : url.includes('/symbols') ? COINS : {};
    return { ok: true, status: 200, json: async () => payload, text: async () => '' };
  }));
  return calls;
}

const LIVE = {
  mode: 'testnet',
  trading_enabled: true,
  credentials: { valid: true, can_trade: true },
  max_leverage: 10,
  strategy_monitor: { rows: [] },
} as unknown as AppState;

afterEach(() => { vi.unstubAllGlobals(); localStorage.clear(); });

async function ready(state: AppState = LIVE) {
  const calls = stub();
  render(<FleetDeploy state={state} connected />);
  await screen.findByRole('option', { name: /CTK 1/ });
  return calls;
}

it('states the total commitment, not just the per-coin amount', async () => {
  await ready();
  fireEvent.change(screen.getByLabelText('Amount per coin'), { target: { value: '100' } });
  // 4 coins x 100 = 400 of margin, not 100. It is stated twice on purpose:
  // once as the headline figure and once in the line being acknowledged.
  const headline = await screen.findByText('Margin committed');
  expect(headline.parentElement).toHaveTextContent('400 USDT');
  expect(screen.getByLabelText(/I understand/).closest('label')).toHaveTextContent(
    /400 USDT of margin across 4 separate bots, not 100 in total/);
  expect(screen.getByText('Coins', { selector: 'dt' }).parentElement).toHaveTextContent('4 coins');
});

it('shows the notional the leverage actually controls', async () => {
  await ready();
  fireEvent.change(screen.getByLabelText('Amount per coin'), { target: { value: '100' } });
  fireEvent.change(screen.getByLabelText('Leverage'), { target: { value: '5' } });
  // 4 x 100 x 5 = 2000 of exposure.
  expect(await screen.findByText(/2,000|2000/)).toBeInTheDocument();
});

it('names the timeframe of the strategy being deployed', async () => {
  await ready();
  fireEvent.change(screen.getByLabelText('Strategy'), { target: { value: 'ctk-5' } });
  await waitFor(() =>
    expect(screen.getByText('Runs on').parentElement).toHaveTextContent('5m candles'));
  // ...and on the option itself, so it is visible before the pick is made.
  expect(screen.getByRole('option', { name: 'CTK 5 (5m)' })).toBeInTheDocument();
});

it('refuses to offer a backtest-only strategy for live deployment', async () => {
  await ready();
  const options = within(screen.getByLabelText('Strategy')).getAllByRole('option');
  expect(options.map(o => o.textContent)).not.toContain(expect.stringContaining('Lab 01'));
  expect(screen.queryByRole('option', { name: /Lab 01/ })).not.toBeInTheDocument();
});

it('will not deploy until the operator confirms the total', async () => {
  await ready();
  fireEvent.change(screen.getByLabelText('Amount per coin'), { target: { value: '100' } });
  expect(screen.getByRole('button', { name: /Deploy/ })).toBeDisabled();
  fireEvent.click(screen.getByLabelText(/I understand/));
  expect(screen.getByRole('button', { name: /Deploy/ })).toBeEnabled();
});

it('creates one bot per coin so each position is accounted separately', async () => {
  const calls = await ready();
  fireEvent.change(screen.getByLabelText('Amount per coin'), { target: { value: '25' } });
  fireEvent.click(screen.getByLabelText(/I understand/));
  fireEvent.click(screen.getByRole('button', { name: /Deploy/ }));
  await waitFor(() => expect(calls.filter(c => c.url.endsWith('/api/bots'))).toHaveLength(4));
  const bodies = calls.filter(c => c.url.endsWith('/api/bots')).map(c => c.body as
    { symbols: string[]; amount: string; strategy: string });
  expect(bodies.every(b => b.symbols.length === 1)).toBe(true);
  expect(bodies.map(b => b.symbols[0]).sort()).toEqual(COINS.symbols.slice().sort());
  expect(bodies.every(b => b.amount === '25' && b.strategy === 'ctk-1')).toBe(true);
});

it('reports which coins failed rather than claiming the fleet is running', async () => {
  // A partial deploy is the normal case: some symbols are unsupported, and
  // saying "done" would leave the operator believing in bots that do not exist.
  const calls: string[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith('/api/bots')) {
      const body = JSON.parse(init?.body as string) as { symbols: string[] };
      calls.push(body.symbols[0]);
      const ok = body.symbols[0] !== 'SOLUSDT';
      return { ok, status: ok ? 202 : 400, json: async () => ({}),
        text: async () => 'symbol not tradeable' };
    }
    const payload = url.includes('/strategies') ? STRATEGIES
      : url.includes('/symbols') ? COINS : {};
    return { ok: true, status: 200, json: async () => payload, text: async () => '' };
  }));
  render(<FleetDeploy state={LIVE} connected />);
  await screen.findByRole('option', { name: /CTK 1/ });
  fireEvent.change(screen.getByLabelText('Amount per coin'), { target: { value: '10' } });
  fireEvent.click(screen.getByLabelText(/I understand/));
  fireEvent.click(screen.getByRole('button', { name: /Deploy/ }));
  expect(await screen.findByText(/3 of 4/)).toBeInTheDocument();
  expect(screen.getByText(/SOLUSDT/)).toBeInTheDocument();
});

it('cannot arm anything while the engine or the account is not ready', async () => {
  await ready({ ...LIVE, trading_enabled: false } as AppState);
  fireEvent.change(screen.getByLabelText('Amount per coin'), { target: { value: '100' } });
  fireEvent.click(screen.getByLabelText(/I understand/));
  expect(screen.getByRole('button', { name: /Deploy/ })).toBeDisabled();
  expect(screen.getByRole('status')).toHaveTextContent(/Overview/);
});
