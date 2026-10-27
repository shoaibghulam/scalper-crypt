from unittest.mock import AsyncMock

import pytest

from scalper.engine.settlement import exit_outcome


@pytest.mark.parametrize('kind,expected', [('TAKE_PROFIT_MARKET', 'tp'), ('STOP_MARKET', 'sl'), ('MARKET', None)])
async def test_exit_type_requires_matched_order_evidence(kind, expected):
    client = AsyncMock()
    client.query_order_id.return_value = {'symbol': 'SOLUSDT', 'orderId': 42, 'status': 'FILLED', 'origType': kind}
    assert await exit_outcome(client, 'SOLUSDT', [{'orderId': 42}, {'orderId': 42}]) == expected
    client.query_order_id.assert_awaited_once_with('SOLUSDT', 42)


async def test_missing_or_mismatched_evidence_is_not_guessed():
    client = AsyncMock()
    client.query_order_id.side_effect = TimeoutError()
    assert await exit_outcome(client, 'SOLUSDT', [{'orderId': 42}]) is None
    client.query_order_id.side_effect = None
    client.query_order_id.return_value = {'symbol': 'BTCUSDT', 'orderId': 42, 'status': 'FILLED', 'type': 'STOP_MARKET'}
    assert await exit_outcome(client, 'SOLUSDT', [{'orderId': 42}]) is None


async def test_mixed_exit_orders_are_unclassified():
    client = AsyncMock()
    async def order(symbol, order_id):
        return {'symbol': symbol, 'orderId': order_id, 'status': 'FILLED',
                'type': 'STOP_MARKET' if order_id == 1 else 'TAKE_PROFIT_MARKET'}
    client.query_order_id.side_effect = order
    assert await exit_outcome(client, 'SOLUSDT', [{'orderId': 1}, {'orderId': 2}]) is None
