import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { SavedMarketData } from './SavedMarketData';
afterEach(() => vi.unstubAllGlobals());
it('shows retained cache for selected coin and supports refresh', async () => {
  const fetch = vi.fn(async () => ({ ok: true, json: async () => ({ coins: [{ symbol: 'BTCUSDT', files: 366, bytes: 70233173, first: 60000, last: 240000 }], files: 366, bytes: 70233173 }) }));
  vi.stubGlobal('fetch', fetch);
  render(<SavedMarketData symbol="BTCUSDT" />);
  await screen.findByText(/366 cached files/);
  expect(screen.getByText(/not guaranteed continuous coverage/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Refresh storage' }));
  expect(fetch).toHaveBeenCalledTimes(2);
});
it('does not present failed inventory requests as an empty cache', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false })));
  render(<SavedMarketData symbol="BTCUSDT" />);
  await screen.findByText(/saved files have not been deleted/);
  expect(screen.queryByText(/No saved long-range/)).not.toBeInTheDocument();
});
