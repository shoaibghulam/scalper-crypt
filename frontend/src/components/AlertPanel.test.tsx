import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { AlertPanel } from './AlertPanel';
import type { Alert, LastAction } from '../lib/api';

/**
 * The orphan alert has two doors, and only one of them opens.
 *
 * Plain adopt READS protective orders back from the exchange and refuses any
 * position that has none. A second bot that opens positions without stops
 * therefore makes that button permanently impossible - it was still the
 * primary one, so it got clicked, refused, and reported as broken. A control
 * that cannot succeed should not be the one being offered.
 */

const ORPHANS: Alert[] = [{
  id: 'orphans',
  severity: 'critical',
  message: 'The exchange holds positions this engine has no record of: BTCUSDT, ETHUSDT.',
  // Carried separately from the message: the panel offers the action on
  // these, and renders no buttons at all without them.
  symbols: ['BTCUSDT', 'ETHUSDT'],
}];

const REFUSED = {
  kind: 'adopt',
  ok: false,
  detail: 'BTCUSDT: the exchange holds no stop for it, so adopting would claim '
    + 'protection that does not exist; ETHUSDT: the exchange holds no stop for it, '
    + 'so adopting would claim protection that does not exist',
} as LastAction;

function show(lastAction?: LastAction) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <AlertPanel alerts={ORPHANS} lastAction={lastAction} />
    </QueryClientProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

const attachButton = () => screen.getByRole('button', { name: /attach a stop/i });
const plainButton = () => screen.getByRole('button', { name: /^Adopt \d+ position/ });

it('offers attaching a stop as the primary action', () => {
  // It is the one that works on an unprotected position, which is the case
  // that actually reaches this screen.
  show();
  expect(attachButton()).toHaveClass('btn-primary');
  expect(plainButton()).not.toHaveClass('btn-primary');
});

it('sends protect true from the primary button', async () => {
  const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) =>
    ({ ok: true, text: async () => '', json: async () => ({}) }));
  vi.stubGlobal('fetch', fetchMock);
  show();
  fireEvent.click(attachButton());
  await waitFor(() => expect(fetchMock).toHaveBeenCalled());
  const body = JSON.parse(fetchMock.mock.calls[0][1]?.body as string);
  expect(body.protect).toBe(true);
});

it('sends protect false from the plain button', async () => {
  const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) =>
    ({ ok: true, text: async () => '', json: async () => ({}) }));
  vi.stubGlobal('fetch', fetchMock);
  show();
  fireEvent.click(plainButton());
  await waitFor(() => expect(fetchMock).toHaveBeenCalled());
  const body = JSON.parse(fetchMock.mock.calls[0][1]?.body as string);
  expect(body.protect).toBe(false);
});

it('stops offering the plain adopt once the exchange has said there are no stops', () => {
  // Clicking it again cannot do anything different. Leaving it live is what
  // made it read as broken.
  show(REFUSED);
  expect(plainButton()).toBeDisabled();
});

it('names the button that will actually work after a refusal', () => {
  show(REFUSED);
  expect(screen.getByText(/use .?adopt and attach a stop/i)).toBeInTheDocument();
});

it('explains that the refusal is about missing stops, not a failure', () => {
  show(REFUSED);
  // Scoped to the guidance note: the engine's own refusal text also mentions
  // stops, and matching either would not prove the note says anything.
  const note = document.querySelector('.alert-note');
  expect(note?.textContent).toMatch(/holds no stop for these positions/i);
});

it('keeps the plain adopt available when nothing has been refused yet', () => {
  // Positions that DO carry a stop should still be adoptable without
  // placing a new order.
  show();
  expect(plainButton()).toBeEnabled();
});

it('re-enables the plain adopt after a successful adopt', () => {
  const ok = { kind: 'adopt', ok: true, detail: 'adopted ETHUSDT' } as LastAction;
  show(ok);
  expect(plainButton()).toBeEnabled();
});
