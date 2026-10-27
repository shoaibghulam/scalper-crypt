import { render, screen, within } from '@testing-library/react';
import { expect, it } from 'vitest';
import { ExternalPositions } from './ExternalPositions';
import type { ExternalPosition } from '../lib/api';

/**
 * Positions opened by something other than this engine.
 *
 * A CLI bot or a hand-placed trade on the same Binance account shows up here
 * so the dashboard is a complete picture of what is open. It is read-only on
 * purpose: this engine has no record of why these were opened and no business
 * closing them, so it reports and offers no action.
 */

const ETH: ExternalPosition = {
  symbol: 'ETHUSDT', side: 'LONG', quantity: 0.093, entry_price: 2678.17,
  mark_price: 2685.82, unrealised: 0.71, liquidation_price: 1.08,
};
const XRP: ExternalPosition = {
  symbol: 'XRPUSDT', side: 'SHORT', quantity: 50, entry_price: 2.1,
  mark_price: 2.0, unrealised: 5, liquidation_price: null,
};

it('says nothing at all when the account has none', () => {
  // An empty panel on every normal dashboard would be noise.
  const { container } = render(<ExternalPositions positions={[]} />);
  expect(container).toBeEmptyDOMElement();
});

it('lists a position this engine did not open', () => {
  render(<ExternalPositions positions={[ETH]} />);
  const row = screen.getByRole('row', { name: /ETHUSDT/ });
  expect(within(row).getByText('LONG')).toBeInTheDocument();
  expect(within(row).getByText('0.093')).toBeInTheDocument();
  expect(within(row).getByText('2678.17')).toBeInTheDocument();
});

it('shows profit and loss with its direction', () => {
  render(<ExternalPositions positions={[ETH, XRP]} />);
  expect(within(screen.getByRole('row', { name: /ETHUSDT/ })).getByText('+0.71')).toBeInTheDocument();
  expect(within(screen.getByRole('row', { name: /XRPUSDT/ })).getByText('+5.00')).toBeInTheDocument();
});

it('marks a loss as a loss', () => {
  const losing = { ...ETH, unrealised: -12.4 };
  render(<ExternalPositions positions={[losing]} />);
  expect(screen.getByText('-12.40')).toBeInTheDocument();
});

it('says the venue gave no mark rather than printing a zero', () => {
  // A dash reads as "unknown". A 0.00 reads as "flat", which is a different
  // and wrong claim.
  const unpriced = { ...ETH, mark_price: null, unrealised: null };
  render(<ExternalPositions positions={[unpriced]} />);
  const row = screen.getByRole('row', { name: /ETHUSDT/ });
  expect(within(row).getAllByText('—').length).toBeGreaterThan(0);
  expect(within(row).queryByText('0.00')).not.toBeInTheDocument();
});

it('offers no way to act on them', () => {
  // Every button here would be this engine touching a position it does not
  // understand.
  render(<ExternalPositions positions={[ETH, XRP]} />);
  expect(screen.queryAllByRole('button')).toHaveLength(0);
});

it('names where they came from so they are not mistaken for the fleet', () => {
  render(<ExternalPositions positions={[ETH]} />);
  expect(screen.getByText(/not managed by this engine/i)).toBeInTheDocument();
});

it('shows a liquidation price only when the exchange supplied one', () => {
  render(<ExternalPositions positions={[ETH, XRP]} />);
  expect(within(screen.getByRole('row', { name: /ETHUSDT/ })).getByText('1.08')).toBeInTheDocument();
  const xrp = within(screen.getByRole('row', { name: /XRPUSDT/ }));
  expect(xrp.queryByText('1.08')).not.toBeInTheDocument();
});
