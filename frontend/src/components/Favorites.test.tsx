import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { Favorites } from './Favorites';
import type { AppState } from '../lib/api';

/**
 * Favourites are server state now, not browser state.
 *
 * They used to live in localStorage, so a cleared browser or a second machine
 * lost the shortlist. These tests drive the same behaviours through the API
 * the hook now calls, which is also what stops a regression to per-browser
 * storage passing quietly.
 */

vi.mock('./FavoriteTrade', () => ({
  FavoriteTrade: ({ pair }: { pair: { symbol: string; strategy: string } }) => (
    <div>Trade setup {pair.symbol} / {pair.strategy}</div>
  ),
}));
vi.mock('../hooks/useScan', () => ({
  useStrategies: () => ({
    data: {
      strategies: [
        { id: 'ema', name: 'EMA trend' },
        { id: 'ivb', name: 'IVB', backtest_only: true },
      ],
    },
    isError: false,
  }),
}));

type Pair = { symbol: string; strategy: string };

/** A stand-in for the favourites table, so the hook talks real HTTP shapes. */
function server(initial: Pair[] = []) {
  let rows = [...initial];
  const calls: { method: string; body?: Pair }[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (_url: string, init?: RequestInit) => {
      const method = init?.method ?? 'GET';
      const body = init?.body ? (JSON.parse(init.body as string) as Pair) : undefined;
      calls.push({ method, body });
      if (method === 'POST' && body) {
        if (!rows.some(r => r.symbol === body.symbol && r.strategy === body.strategy)) {
          rows.push(body);
        }
      }
      if (method === 'DELETE' && body) {
        rows = rows.filter(r => !(r.symbol === body.symbol && r.strategy === body.strategy));
      }
      return { ok: true, json: async () => rows.map(r => ({ ...r, created: 1790185884.81 })) };
    }),
  );
  return { calls, rows: () => rows };
}

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

const TWO: Pair[] = [
  { symbol: 'BTCUSDT', strategy: 'ema' },
  { symbol: 'ETHUSDT', strategy: 'ema' },
];

it('labels backtest-only favorites and prevents starting a trading setup', async () => {
  server([{ symbol: 'BTCUSDT', strategy: 'ivb' }]);
  render(<Favorites state={{ mode: 'testnet' } as AppState} connected />);
  expect(await screen.findByText('Backtest only')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Trade BTCUSDT / IVB' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Remove favorite BTCUSDT / IVB' })).toBeEnabled();
});

it('lists saved pairs independently of backtests and hands the exact pair to trade preparation', async () => {
  const api = server(TWO);
  render(<Favorites />);
  expect(await screen.findByRole('rowheader', { name: 'BTCUSDT' })).toBeInTheDocument();
  expect(screen.getByRole('rowheader', { name: 'ETHUSDT' })).toBeInTheDocument();
  expect(screen.queryByText(/Trade setup/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Trade ETHUSDT / EMA trend' }));
  expect(screen.getByText('Trade setup ETHUSDT / ema')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Remove favorite BTCUSDT / EMA trend' }));
  await waitFor(() =>
    expect(screen.queryByRole('rowheader', { name: 'BTCUSDT' })).not.toBeInTheDocument(),
  );
  // The removal reached the server, not just this browser.
  expect(api.calls.some(c => c.method === 'DELETE' && c.body?.symbol === 'BTCUSDT')).toBe(true);
  expect(api.rows()).toEqual([{ symbol: 'ETHUSDT', strategy: 'ema' }]);
});

it('blocks disabled and missing strategies while still allowing removal', async () => {
  server([
    { symbol: 'BTCUSDT', strategy: 'ema' },
    { symbol: 'SOLUSDT', strategy: 'missing' },
  ]);
  const state = { strategy_monitor: { rows: [{ id: 'ema', disabled: true }] } } as AppState;
  render(<Favorites state={state} />);
  expect(await screen.findByRole('button', { name: 'Trade BTCUSDT / EMA trend' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Trade SOLUSDT / missing' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Remove favorite BTCUSDT / EMA trend' })).toBeEnabled();
});

it('shows an empty state and picks up a change made elsewhere', async () => {
  const api = server();
  render(<Favorites />);
  expect(await screen.findByText(/No favorites yet/)).toBeInTheDocument();
  api.rows().push(...TWO);
  // Another tab saving a favourite raises this; the list refetches rather
  // than reading a local copy that would not exist on a second machine.
  fireEvent(window, new Event('scalper-favorites-changed'));
  expect(await screen.findByRole('rowheader', { name: 'BTCUSDT' })).toBeInTheDocument();
});

it('migrates anything this browser saved before favourites moved to the server', async () => {
  // The change must not cost an existing user their shortlist.
  localStorage.setItem(
    'scalper-research-favorites',
    JSON.stringify([JSON.stringify(['BTCUSDT', 'ema'])]),
  );
  const api = server();
  render(<Favorites />);
  await waitFor(() =>
    expect(api.calls.some(c => c.method === 'POST' && c.body?.symbol === 'BTCUSDT')).toBe(true),
  );
  expect(await screen.findByRole('rowheader', { name: 'BTCUSDT' })).toBeInTheDocument();
  // ...and only once, however many times the page is opened.
  expect(localStorage.getItem('scalper-research-favorites-migrated')).toBe('1');
});

it('keeps the list on screen when the API cannot be reached', async () => {
  // Showing an empty table would read as "your favourites are gone".
  server(TWO);
  render(<Favorites />);
  expect(await screen.findByRole('rowheader', { name: 'BTCUSDT' })).toBeInTheDocument();
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, json: async () => ({}) })));
  fireEvent(window, new Event('scalper-favorites-changed'));
  await waitFor(() => expect(screen.getByText(/Favorites are unavailable/)).toBeInTheDocument());
  expect(screen.getByRole('rowheader', { name: 'BTCUSDT' })).toBeInTheDocument();
});
