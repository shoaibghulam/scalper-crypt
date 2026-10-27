import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { FavoriteTrade } from './FavoriteTrade';
import { createBot, type AppState } from '../lib/api';

vi.mock('../lib/api', () => ({ createBot: vi.fn() }));
vi.mock('./BotFleet', () => ({ BotFleet: () => <div>Bot controls</div> }));
beforeEach(() => { vi.resetAllMocks(); });
const state = { mode: 'testnet', trading_enabled: true, max_leverage: 10, credentials: { valid: true, can_trade: true }, bots: [] } as unknown as AppState;
function mount(connected = true, current = state) {
  render(<QueryClientProvider client={new QueryClient()}><FavoriteTrade pair={{ symbol: 'XRPUSDT', strategy: 'advanced5-macd' }} name="MACD" state={current} connected={connected} available onClose={vi.fn()} /></QueryClientProvider>);
}
it('starts watching exactly the favorite pair with the entered budget and leverage', async () => {
  vi.mocked(createBot).mockResolvedValue(undefined);
  mount();
  expect(createBot).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText('Amount (USDT)'), { target: { value: '25' } });
  fireEvent.change(screen.getByLabelText('Leverage'), { target: { value: '5' } });
  fireEvent.click(screen.getByRole('button', { name: 'Start searching for a trade' }));
  await waitFor(() => expect(createBot).toHaveBeenCalledExactlyOnceWith({ symbols: ['XRPUSDT'], strategy: 'advanced5-macd', amount: '25', leverage: 5, continuous: true, margin_mode: 'ISOLATED' }));
  expect(await screen.findByText(/Request sent/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Start searching for a trade' })).toBeDisabled();
});
it('blocks stale connections and invalid leverage', () => {
  mount(false);
  expect(screen.getByRole('button', { name: 'Start searching for a trade' })).toBeDisabled();
  expect(createBot).not.toHaveBeenCalled();
});
it('enforces the account leverage cap and positive budget', () => {
  mount();
  fireEvent.change(screen.getByLabelText('Leverage'), { target: { value: '11' } });
  expect(screen.getByRole('button', { name: 'Start searching for a trade' })).toBeDisabled();
  fireEvent.change(screen.getByLabelText('Leverage'), { target: { value: '3' } });
  fireEvent.change(screen.getByLabelText('Amount (USDT)'), { target: { value: '0' } });
  expect(screen.getByRole('button', { name: 'Start searching for a trade' })).toBeDisabled();
});
it('shows rejected requests and allows retry', async () => {
  vi.mocked(createBot).mockRejectedValue(new Error('Engine unavailable'));
  mount();
  fireEvent.click(screen.getByRole('button', { name: 'Start searching for a trade' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('Engine unavailable');
  expect(screen.getByRole('button', { name: 'Start searching for a trade' })).toBeEnabled();
});
it('blocks a duplicate active pair and displays its bot controls', () => {
  mount(true, { ...state, bots: [{ strategy: 'advanced5-macd', symbols: ['XRPUSDT'], state: 'running' }] } as AppState);
  expect(screen.getByRole('button', { name: 'Start searching for a trade' })).toBeDisabled();
  expect(screen.getByText('Bot controls')).toBeInTheDocument();
});
