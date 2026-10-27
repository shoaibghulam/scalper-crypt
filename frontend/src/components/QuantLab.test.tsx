import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { QuantLab } from './QuantLab';

afterEach(() => vi.unstubAllGlobals());
function mount(saved: unknown[] = []) {
  const fetcher = vi.fn(async (url: string, options?: RequestInit) => {
    if (url.endsWith('/symbols')) return { ok: true, json: async () => ({ symbols: ['BTCUSDT', 'ETHUSDT', 'XRPUSDT', 'SOLUSDT'] }) };
    if (options?.method === 'POST') return { ok: true, json: async () => ({ id: 'a'.repeat(32), kind: 'research', status: 'running', phase: 'Loading candles', request: JSON.parse(String(options.body)) }) };
    if (url.endsWith('/jobs')) return { ok: true, json: async () => saved };
    return { ok: true, json: async () => saved[0] };
  });
  vi.stubGlobal('fetch', fetcher);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><QuantLab /></QueryClientProvider>);
  return fetcher;
}

it('submits chosen coins, timeframe, costs and risk ceilings', async () => {
  const fetcher = mount();
  await screen.findByLabelText('SOLUSDT');
  fireEvent.click(screen.getByLabelText('SOLUSDT'));
  fireEvent.change(screen.getByLabelText('Entry timeframe'), { target: { value: '15m' } });
  fireEvent.change(screen.getByLabelText('Fee per side (bps)'), { target: { value: '6' } });
  fireEvent.click(screen.getByRole('button', { name: 'Start research' }));
  await waitFor(() => expect(fetcher.mock.calls.some(([, options]) => options?.method === 'POST')).toBe(true));
  const request = JSON.parse(String(fetcher.mock.calls.find(([, options]) => options?.method === 'POST')?.[1]?.body));
  expect(request.symbols).toEqual(['BTCUSDT', 'ETHUSDT', 'XRPUSDT', 'SOLUSDT']);
  expect(request.timeframe).toBe('15m');
  expect(request.search.costs).toEqual({ fee: .0006, slippage: .0002, spread: .0002, require_funding: true });
  expect(request.search.safety.max_daily_loss).toBe(.05);
  expect(request.search.safety.max_leverage).toBe(1);
});

it('requires a coin and explains CPU usage', async () => {
  mount();
  fireEvent.click(screen.getByRole('button', { name: 'Clear coins', hidden: true }));
  expect(screen.getByRole('button', { name: 'Start research' })).toBeDisabled();
  expect(screen.getByText(/GPU is not used/)).toBeInTheDocument();
});

it('shows rejected results and never offers paper approval for them', async () => {
  const metrics = { net_profit: -5, total_return: -.005, trades: 4, sharpe: -1, max_drawdown: .02, profit_factor: .6, win_rate: .2, fees_paid: 3, funding_paid: 1, final_equity: 995, halted: false };
  const period = { metrics, equity: [[0, 1000], [1, 995]] };
  mount([{ id: 'b'.repeat(32), kind: 'research', created_at: 1, status: 'completed', phase: 'Done', request: { symbols: ['BTCUSDT'], timeframe: '5m' }, results: [{ symbol: 'BTCUSDT', timeframe: '5m', total_tested: 8, periods: {}, portfolio: { selected_before_holdout: [] }, strategies: [{ strategy_id: 'one', status: 'rejected', definition: { direction: 'long' }, reasons: ['unseen test: net loss after costs'], training: period, validation: period, test: period, robustness: { score: .1, stability_fraction: 0 }, ml_note: 'Disabled' }] }] }]);
  await screen.findByRole('option', { name: /completed/ });
  fireEvent.change(screen.getByLabelText('Saved experiments and paper sessions'), { target: { value: 'b'.repeat(32) } });
  await screen.findByText('Did not pass');
  expect(screen.getByText('unseen test: net loss after costs')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Approve and start paper session' })).not.toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'Download all evaluated candidates, including rejected rules' })).toBeInTheDocument();
});
