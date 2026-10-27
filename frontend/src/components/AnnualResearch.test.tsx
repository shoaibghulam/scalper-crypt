import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { AnnualResearch as AnnualResearchView } from './AnnualResearch';
import type { ComponentProps } from 'react';
const catalog = [{ id: 'the-minute-black', name: 'Minute Black', timeframe: '1m' }, { id: 'lab-01', name: 'EMA cross', timeframe: '1m' }];
const enabledState = { mode: 'testnet', strategy_monitor: { rows: catalog.map(c => ({ id: c.id, disabled: false })) } } as AppState;
function AnnualResearch(props: ComponentProps<typeof AnnualResearchView>) {
  return <AnnualResearchView state={enabledState} {...props} />;
}
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); localStorage.clear(); });
it('filters selectable backtests to the new 10m entry timeframe', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({ top20: true }) })));
  const ten = { id: 'ten-ema-pullback', name: '10m EMA Pullback', timeframe: '10m' };
  const state = { ...enabledState, strategy_monitor: { ...enabledState.strategy_monitor,
    rows: [...enabledState.strategy_monitor!.rows, { id: ten.id, disabled: false }] } } as AppState;
  await act(async () => {
    render(<AnnualResearch catalog={[...catalog, ten]} symbols={['BTCUSDT']} state={state} />);
  });
  fireEvent.change(screen.getByRole('combobox', { name: /Entry timeframe/ }), { target: { value: '10m' } });
  expect(screen.getByRole('checkbox', { name: /10m EMA Pullback/ })).toBeEnabled();
  expect(screen.queryByRole('checkbox', { name: /Minute Black/ })).not.toBeInTheDocument();
});
it('allows IVB backtesting without an execution-monitor row and excludes it from scanning', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({ top20: true }) })));
  const ivb = { id: 'ivb', name: 'IVB', timeframe: '5m', backtest_only: true };
  render(<AnnualResearch catalog={[...catalog, ivb]} symbols={['BTCUSDT']} />);
  const choice = screen.getByRole('checkbox', { name: /IVB.*Backtest only/ });
  expect(choice).toBeEnabled();
  fireEvent.click(choice);
  expect(choice).toBeChecked();
  const scan = screen.getByLabelText('Scan strategy');
  expect([...scan.querySelectorAll('option')].some(option => option.value === 'ivb')).toBe(false);
});
it('shows data-dependent PDF setups without allowing candle-only selection', async () => {
  const unavailable = [
    { id: 'advanced5-order-book', name: 'Order-book setup', reason: 'Historical L2 and trade events required.' },
    { id: 'advanced5-open-interest', name: 'Open-interest setup', reason: 'Historical open interest required.' },
    { id: 'advanced5-btc-eth-pairs', name: 'BTC-ETH pair setup', reason: 'Two-leg simulation required.' },
  ];
  vi.stubGlobal('fetch', vi.fn(async (url: string) => ({ ok: true, json: async () => url.endsWith('/capabilities')
    ? { top20: true, custom: true, unavailable_strategies: unavailable }
    : { coins: [], files: 0, bytes: 0 } })));
  render(<AnnualResearch catalog={catalog} symbols={['BTCUSDT']} />);
  await screen.findByText('Advanced PDF: 3 setups require additional data or simulation support');
  expect(screen.getByText('Historical open interest required.')).toBeInTheDocument();
  expect(screen.queryByRole('checkbox', { name: /Open-interest setup/ })).not.toBeInTheDocument();
});
it('submits a one-year comparison with selected strategies and shows separate exit counts', async () => {
  const job = { id: 'test', status: 'completed', phase: 'Completed', symbol: 'ETHUSDT', progress: 100, results: [{ strategy: 'lab-01', timeframe: '5m', run_id: 'saved', trades: 9, tp_hits: 5, sl_hits: 3, forced_exits: 1, net: 1.25, fees: .4 }] };
  const fetch = vi.fn(async (url: string) => ({ ok: true, json: async () => url.endsWith('/capabilities') ? { top20: true } : job }));
  vi.stubGlobal('fetch', fetch);
  render(<AnnualResearch catalog={catalog} symbols={['ETHUSDT']} />);
  fireEvent.click(screen.getByRole('button', { name: 'Select all strategies' }));
  fireEvent.change(screen.getByLabelText('Backtest coin'), { target: { value: 'ETHUSDT' } });
  fireEvent.change(screen.getByLabelText('Long-range capital (USDT)'), { target: { value: '20' } });
  fireEvent.change(screen.getByLabelText(/Risk per trade/), { target: { value: '0.5' } });
  await waitFor(() => expect(screen.getByRole('button', { name: 'Start historical backtest' })).toBeEnabled());
  fireEvent.submit(screen.getByRole('button', { name: 'Start historical backtest' }).closest('form')!);
  await screen.findByRole('cell', { name: '+1.25' });
  expect(screen.getByRole('cell', { name: '+1.65' })).toBeInTheDocument();
  expect(screen.getByRole('cell', { name: '5m' })).toHaveAttribute('title', 'Timeframe recorded for this result');
  const args = fetch.mock.calls as unknown as [string, RequestInit][];
  const request = JSON.parse(args.find(([url]) => url === '/api/research/jobs')![1].body as string);
  expect(request).toMatchObject({ coin_mode: 'top20', days: 365, capital: 20, risk_pct: 0.5, strategies: ['the-minute-black', 'lab-01'] });
  expect(screen.getByRole('columnheader', { name: 'TP hits' })).toBeInTheDocument();
  expect(screen.getByRole('columnheader', { name: 'SL hits' })).toBeInTheDocument();
});
it('never shows stale or unconfirmed coins as ready', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({ ready: true, scanned_at: Date.now()/1000, stats: { universe_size: 500 }, candidates: [
    { symbol: 'ETHUSDT', score: 90, setup_ready: true, expires_at: Date.now()/1000+30 },
    { symbol: 'BTCUSDT', score: 99, setup_ready: true, expires_at: Date.now()/1000-1 },
    { symbol: 'SOLUSDT', score: 95, setup_ready: false, expires_at: Date.now()/1000+30 },
  ] }) })));
  render(<AnnualResearch catalog={catalog} symbols={['ETHUSDT']} />);
  fireEvent.click(screen.getByRole('button', { name: 'Find confirmed setups' }));
  await screen.findByRole('button', { name: 'Backtest ETHUSDT' });
  expect(screen.queryByRole('button', { name: 'Backtest BTCUSDT' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Backtest SOLUSDT' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Backtest ETHUSDT' }));
  await waitFor(() => expect(screen.getByLabelText('Backtest coin')).toHaveValue('ETHUSDT'));
});

it('blocks automatic batch submission until the backend supports top20', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({ top20: false }) })));
  render(<AnnualResearch catalog={catalog} symbols={['ETHUSDT']} />);
  expect(screen.getByRole('button', { name: 'Start historical backtest' })).toBeDisabled();
  await screen.findByText(/waiting for the backend update/);
  fireEvent.change(screen.getByLabelText('Coin selection'), { target: { value: 'single' } });
  expect(screen.getByRole('button', { name: 'Start historical backtest' })).toBeEnabled();
});

it('lists all coins independently of the selected value and searches without changing selection', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({ top20: true, coins: [], bytes: 0, files: 0 }) })));
  render(<AnnualResearch catalog={catalog} symbols={['BTCUSDT', 'ETHUSDT', 'SOLUSDT']} />);
  fireEvent.change(screen.getByLabelText('Coin selection'), { target: { value: 'single' } });
  const select = screen.getByLabelText('Backtest coin') as HTMLSelectElement;
  expect(Array.from(select.options).map(option => option.value)).toEqual(['BTCUSDT', 'ETHUSDT', 'SOLUSDT']);
  fireEvent.change(screen.getByLabelText('Search all coins'), { target: { value: 'ETH' } });
  expect(select).toHaveValue('BTCUSDT');
  expect(Array.from(select.options).map(option => option.value)).toEqual(['BTCUSDT', 'ETHUSDT']);
  fireEvent.change(select, { target: { value: 'ETHUSDT' } });
  expect(select).toHaveValue('ETHUSDT');
  await screen.findByText(/No saved long-range candles/);
});
import type { AppState } from '../lib/api';
it('disables across coin rows only after engine confirmation, and shows rejected requests', async () => {
  localStorage.setItem('scalper-research-job', 'saved-job');
  const result = { strategy: 'lab-01', run_id: 'saved', trades: 5, tp_hits: 1, sl_hits: 4, forced_exits: 0, net: -3, fees: .5 };
  const job = { id: 'saved-job', status: 'completed', phase: 'Completed', symbol: 'Top 20 coins', progress: 100,
    results: [{ ...result, symbol: 'BTCUSDT' }, { ...result, symbol: 'ETHUSDT' }] };
  let reject = true;
  const fetch = vi.fn(async (url: string, _options?: RequestInit) => {
    if (url.endsWith('/settings')) return { ok: !reject, json: async () => reject ? { detail: 'Engine unavailable' } : { accepted: true } };
    return { ok: true, json: async () => url.endsWith('/cache') ? { coins: [], files: 0, bytes: 0 }
      : url.endsWith('/capabilities') ? { top20: true } : job };
  });
  vi.stubGlobal('fetch', fetch);
  const state = { mode: 'testnet', strategy_monitor: { rows: [{ id: 'lab-01', disabled: false }] } } as AppState;
  const { rerender } = render(<AnnualResearch catalog={catalog} symbols={['BTCUSDT', 'ETHUSDT']} state={state} connected={false} />);
  const button = await screen.findByRole('button', { name: 'Disable lab-01 from BTCUSDT backtest' });
  expect(button).toBeDisabled();
  rerender(<AnnualResearch catalog={catalog} symbols={['BTCUSDT', 'ETHUSDT']} state={state} connected />);
  fireEvent.click(button);
  await screen.findByText('Engine unavailable');
  expect(button).toBeEnabled();
  reject = false;
  fireEvent.click(button);
  await waitFor(() => expect(button).toHaveTextContent('Waiting for engine'));
  expect(screen.getByRole('button', { name: 'Disable lab-01 from ETHUSDT backtest' })).toBeDisabled();
  const calls = fetch.mock.calls.filter(([url]) => url.endsWith('/settings'));
  expect(JSON.parse(calls[1][1]!.body as string)).toEqual({ strategy: 'lab-01', disabled: true });
  expect(button).not.toHaveTextContent(/^Disabled$/);
  const confirmed = { ...state, strategy_monitor: { ...state.strategy_monitor!, rows: [{ ...state.strategy_monitor!.rows[0], disabled: true }] } };
  rerender(<AnnualResearch catalog={catalog} symbols={['BTCUSDT', 'ETHUSDT']} state={confirmed} connected />);
  expect(button).toHaveTextContent('Disabled');
  expect(button).toBeDisabled();
});

it('hides disabled strategies and excludes previously selected ones from submission', async () => {
  const fetch = vi.fn(async (url: string) => ({ ok: true, json: async () => url.endsWith('/capabilities') ? { top20: true }
    : url.endsWith('/cache') ? { coins: [], files: 0, bytes: 0 }
    : { id: 'filtered-job', status: 'completed', phase: 'Completed', symbol: 'Top 20 coins', results: [] } }));
  vi.stubGlobal('fetch', fetch);
  const { rerender } = render(<AnnualResearch catalog={catalog} symbols={['BTCUSDT']} />);
  fireEvent.click(screen.getByRole('button', { name: 'Select all strategies' }));
  const restricted = { ...enabledState, strategy_monitor: { ...enabledState.strategy_monitor!, rows: enabledState.strategy_monitor!.rows.map(r => ({ ...r, disabled: r.id === 'lab-01' })) } };
  rerender(<AnnualResearch catalog={catalog} symbols={['BTCUSDT']} state={restricted} />);
  expect(screen.queryByRole('checkbox', { name: /EMA cross/ })).not.toBeInTheDocument();
  expect(screen.getByText(/1 selected \/ 1 enabled candle strategies/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Select all strategies' }));
  await waitFor(() => expect(screen.getByRole('button', { name: 'Start historical backtest' })).toBeEnabled());
  fireEvent.submit(screen.getByRole('button', { name: 'Start historical backtest' }).closest('form')!);
  await waitFor(() => expect(fetch.mock.calls.some(([url]) => url === '/api/research/jobs')).toBe(true));
  const calls = fetch.mock.calls as unknown as [string, RequestInit][];
  expect(JSON.parse(calls.find(([url]) => url === '/api/research/jobs')![1].body as string).strategies).toEqual(['the-minute-black']);
});

it('timeframe configuration filters selection and submitted strategy IDs', async () => {
  const fetch = vi.fn(async (url: string) => ({ ok: true, json: async () => url.endsWith('/capabilities') ? { top20: true }
    : url.endsWith('/cache') ? { coins: [], files: 0, bytes: 0 }
    : { id: 'timeframe-job', status: 'completed', phase: 'Completed', symbol: 'BTCUSDT', results: [] } }));
  vi.stubGlobal('fetch', fetch);
  const candidates = [...catalog, { id: 'lab-04', name: 'EMA slope breakout', timeframe: '5m (1h context)' }];
  const state = { ...enabledState, strategy_monitor: { ...enabledState.strategy_monitor!, rows: candidates.map(c => ({ ...enabledState.strategy_monitor!.rows[0], id: c.id, disabled: false })) } };
  render(<AnnualResearch catalog={candidates} symbols={['BTCUSDT']} state={state} />);
  fireEvent.click(screen.getByRole('button', { name: 'Select all strategies' }));
  fireEvent.change(screen.getByLabelText(/Entry timeframe/), { target: { value: '5m' } });
  expect(screen.queryByRole('checkbox', { name: /Minute Black/ })).not.toBeInTheDocument();
  expect(screen.getByRole('checkbox', { name: /EMA slope breakout/ })).toBeChecked();
  fireEvent.click(screen.getByRole('button', { name: 'Select all strategies' }));
  await waitFor(() => expect(screen.getByRole('button', { name: 'Start historical backtest' })).toBeEnabled());
  fireEvent.submit(screen.getByRole('button', { name: 'Start historical backtest' }).closest('form')!);
  await waitFor(() => expect(fetch.mock.calls.some(([url]) => url === '/api/research/jobs')).toBe(true));
  const calls = fetch.mock.calls as unknown as [string, RequestInit][];
  expect(JSON.parse(calls.find(([url]) => url === '/api/research/jobs')![1].body as string).strategies).toEqual(['lab-04']);
});

it('selects multiple coins across searches and submits exactly the selected coins', async () => {
  const fetch = vi.fn(async (url: string) => ({ ok: true, json: async () => url.endsWith('/capabilities') ? { top20: true, custom: true }
    : url.endsWith('/cache') ? { coins: [], files: 0, bytes: 0 }
    : { id: 'custom-job', status: 'completed', phase: 'Completed', symbol: '3 selected coins', results: [] } }));
  vi.stubGlobal('fetch', fetch);
  render(<AnnualResearch catalog={catalog} symbols={['BTCUSDT', 'ETHUSDT', 'SOLUSDT']} />);
  fireEvent.change(screen.getByLabelText('Coin selection'), { target: { value: 'custom' } });
  const start = screen.getByRole('button', { name: 'Start historical backtest' });
  expect(start).toBeDisabled();
  fireEvent.click(screen.getByRole('checkbox', { name: 'BTCUSDT' }));
  await waitFor(() => expect(start).toBeEnabled());
  expect(screen.getByText('1 coins selected')).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Search all coins'), { target: { value: 'ETH' } });
  fireEvent.click(screen.getByRole('checkbox', { name: 'ETHUSDT' }));
  expect(screen.getByText('2 coins selected')).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Search all coins'), { target: { value: '' } });
  expect(screen.getByRole('checkbox', { name: 'BTCUSDT' })).toBeChecked();
  fireEvent.click(screen.getByRole('checkbox', { name: 'SOLUSDT' }));
  expect(screen.getByText('3 coins selected')).toBeInTheDocument();
  fireEvent.submit(start.closest('form')!);
  await waitFor(() => expect(fetch.mock.calls.some(([url]) => url === '/api/research/jobs')).toBe(true));
  const calls = fetch.mock.calls as unknown as [string, RequestInit][];
  expect(JSON.parse(calls.find(([url]) => url === '/api/research/jobs')![1].body as string)).toMatchObject({ coin_mode: 'custom', symbols: ['BTCUSDT', 'ETHUSDT', 'SOLUSDT'] });
  await waitFor(() => expect(start).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: 'Clear coins' }));
  expect(start).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Search all coins'), { target: { value: 'ETH' } });
  fireEvent.click(screen.getByRole('button', { name: 'Select all matching coins' }));
  expect(screen.getByText('1 coins selected')).toBeInTheDocument();
  expect(screen.getByRole('checkbox', { name: 'ETHUSDT' })).toBeChecked();
});

// Favourites are server state now, so the stub has to hold them like the
// database does: a GET that forgot what a POST just saved would make every
// toggle look as though it had failed.
function mockFavoriteResults(initial: { symbol: string; strategy: string }[] = []) {
  localStorage.setItem('scalper-research-job', 'favorite-job');
  const result = { strategy: 'lab-01', run_id: 'saved', trades: 5, tp_hits: 1, sl_hits: 4, forced_exits: 0, net: -3, fees: .5 };
  let saved: { symbol: string; strategy: string }[] = [...initial];
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith('/favorites')) {
      const method = init?.method ?? 'GET';
      const body = init?.body ? JSON.parse(init.body as string) as { symbol: string; strategy: string } : undefined;
      if (method === 'POST' && body && !saved.some(p => p.symbol === body.symbol && p.strategy === body.strategy)) saved.push(body);
      if (method === 'DELETE' && body) saved = saved.filter(p => !(p.symbol === body.symbol && p.strategy === body.strategy));
      return { ok: true, json: async () => saved.map(p => ({ ...p, created: 1 })) };
    }
    return { ok: true, json: async () => url.endsWith('/capabilities') ? { top20: true }
      : url.endsWith('/cache') ? { coins: [], files: 0, bytes: 0 }
      : { id: 'favorite-job', status: 'completed', phase: 'Completed', symbol: 'Selected coins', results: [{ ...result, symbol: 'BTCUSDT' }, { ...result, symbol: 'ETHUSDT' }] } };
  });
  vi.stubGlobal('fetch', fetch);
  // A setter, not the array: DELETE rebinds `saved`, so a handed-out
  // reference stops being the list the stub serves.
  return Object.assign(fetch, { forget: (symbol: string) => { saved = saved.filter(p => p.symbol !== symbol); } });
}

it('favorites coin/strategy pairs independently, persists them and filters current results', async () => {
  const fetch = mockFavoriteResults();
  const view = render(<AnnualResearch catalog={catalog} symbols={['BTCUSDT', 'ETHUSDT']} />);
  const btc = await screen.findByRole('button', { name: 'Favorite BTCUSDT / EMA cross' });
  expect(btc).toHaveAttribute('aria-pressed', 'false');
  fireEvent.click(btc);
  expect(btc).toHaveAttribute('aria-pressed', 'true');
  expect(screen.getByRole('button', { name: 'Favorite ETHUSDT / EMA cross' })).toHaveAttribute('aria-pressed', 'false');
  view.unmount();
  render(<AnnualResearch catalog={catalog} symbols={['BTCUSDT', 'ETHUSDT']} />);
  expect(await screen.findByRole('button', { name: 'Favorite BTCUSDT / EMA cross' })).toHaveAttribute('aria-pressed', 'true');
  fireEvent.click(screen.getByRole('button', { name: 'Favorites only (1)' }));
  expect(screen.queryByRole('button', { name: 'Favorite ETHUSDT / EMA cross' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Favorite BTCUSDT / EMA cross' }));
  expect(screen.getByText(/No favorite results in this backtest/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Favorites only (0)' }));
  expect(screen.getByRole('button', { name: 'Favorite ETHUSDT / EMA cross' })).toBeInTheDocument();
  // The shortlist lives on the server now, so that is where the removal has
  // to land - localStorage would pass here and lose the list on another machine.
  await waitFor(() => expect(fetch.mock.calls.some(([, init]) =>
    (init as RequestInit | undefined)?.method === 'DELETE')).toBe(true));
  expect(fetch.mock.calls.every(([url]) => !url.endsWith('/settings'))).toBe(true);
});

it.each(['broken json', '{}', '[null, 42]'])('handles invalid saved favorites: %s', async saved => {
  mockFavoriteResults();
  // A leftover from the old browser storage must not break the migration.
  localStorage.setItem('scalper-research-favorites', saved);
  render(<AnnualResearch catalog={catalog} symbols={['BTCUSDT']} />);
  const btc = await screen.findByRole('button', { name: 'Favorite BTCUSDT / EMA cross' });
  expect(btc).toHaveAttribute('aria-pressed', 'false');
  fireEvent.click(btc);
  expect(btc).toHaveAttribute('aria-pressed', 'true');
});

it('reports a failed favorite save without claiming the pair was saved', async () => {
  mockFavoriteResults();
  render(<AnnualResearch catalog={catalog} symbols={['BTCUSDT']} />);
  const btc = await screen.findByRole('button', { name: 'Favorite BTCUSDT / EMA cross' });
  // Only the favourites endpoint fails; the rest of the page must carry on.
  const working = globalThis.fetch as unknown as (u: string, i?: RequestInit) => Promise<unknown>;
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) =>
    url.endsWith('/favorites') && init?.method
      ? { ok: false, json: async () => ({ detail: 'Could not save favorites.' }) }
      : working(url, init)));
  fireEvent.click(btc);
  // The star must fall back: claiming a save that did not happen is how a
  // shortlist silently loses entries.
  await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Could not save favorites'));
  await waitFor(() => expect(btc).toHaveAttribute('aria-pressed', 'false'));
});


it('marks strategies with archived backtests as tested independently of favorites', async () => {
  localStorage.setItem('scalper-research-favorites', JSON.stringify([JSON.stringify(['BTCUSDT', 'the-minute-black'])]));
  vi.stubGlobal('fetch', vi.fn(async (url: string) => ({ ok: true, json: async () => url.endsWith('/tested-strategies') ? ['lab-01']
    : url.endsWith('/capabilities') ? { top20: true } : { coins: [], files: 0, bytes: 0 } })));
  const view = render(<AnnualResearch catalog={catalog} symbols={['BTCUSDT']} />);
  expect(await screen.findByRole('checkbox', { name: /EMA cross.*Tested/ })).not.toBeChecked();
  expect(screen.getByRole('checkbox', { name: /Minute Black/ })).not.toHaveAccessibleName(/Tested/);
  view.unmount();
  render(<AnnualResearch catalog={catalog} symbols={['BTCUSDT']} />);
  expect(await screen.findByRole('checkbox', { name: /EMA cross.*Tested/ })).toBeInTheDocument();
});

it('shows unavailable history instead of claiming every strategy is untested', async () => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => ({ ok: !url.endsWith('/tested-strategies'), json: async () => url.endsWith('/tested-strategies') ? { detail: 'Unavailable' }
    : url.endsWith('/capabilities') ? { top20: true } : { coins: [], files: 0, bytes: 0 } })));
  render(<AnnualResearch catalog={catalog} symbols={['BTCUSDT']} />);
  expect(await screen.findByText(/Testing history is unavailable/)).toBeInTheDocument();
  expect(screen.getByRole('checkbox', { name: /EMA cross/ })).not.toHaveAccessibleName(/Tested/);
});


it('protects every coin row of a favorited strategy until its last favorite is removed', async () => {
  // A favorite from an older backtest also protects the current results.
  const seed = [{ symbol: 'SOLUSDT', strategy: 'lab-01' }];
  const fetch = mockFavoriteResults(seed);
  const removeSeed = () => fetch.forget('SOLUSDT');
  render(<AnnualResearch catalog={catalog} symbols={['BTCUSDT', 'ETHUSDT']} connected />);
  const btcStar = await screen.findByRole('button', { name: 'Favorite BTCUSDT / EMA cross' });
  expect(screen.queryByRole('button', { name: 'Disable lab-01 from BTCUSDT backtest' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Disable lab-01 from ETHUSDT backtest' })).not.toBeInTheDocument();
  fireEvent.click(btcStar);
  // Wait for the star itself, not for an absence that was already true -
  // otherwise the second click can read state the first has not produced.
  await waitFor(() => expect(btcStar).toHaveAttribute('aria-pressed', 'true'));
  expect(screen.queryByRole('button', { name: 'Disable lab-01 from ETHUSDT backtest' })).not.toBeInTheDocument();
  fireEvent.click(btcStar);
  await waitFor(() => expect(btcStar).toHaveAttribute('aria-pressed', 'false'));
  // The older SOLUSDT favourite still protects lab-01: "until its LAST
  // favorite is removed" is the whole point of the rule.
  expect(screen.queryByRole('button', { name: 'Disable lab-01 from BTCUSDT backtest' })).not.toBeInTheDocument();
  removeSeed();
  fireEvent(window, new Event('scalper-favorites-changed'));
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'Disable lab-01 from BTCUSDT backtest' })).toBeEnabled());
  expect(screen.getByRole('button', { name: 'Disable lab-01 from ETHUSDT backtest' })).toBeEnabled();
  expect(fetch.mock.calls.some(([url]) => (url as string).endsWith('/settings'))).toBe(false);
});
