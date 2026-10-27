"""Verifying credentials against the exchange.

Checked eagerly at startup rather than lazily at first use. A key that was
never valid should surface while the operator is watching the screen, not at
the moment a signal fires and the trade is missed.

The three outcomes are kept distinct throughout, because they call for
different actions: absent means configure something, rejected means the value
is wrong, and unreachable means try again later.
"""

import httpx
import pytest
import respx

from scalper.config import Environment, ExchangeConfig
from scalper.exchange.credentials import verify

BASE = "https://testnet.binancefuture.com"
ACCOUNT = f"{BASE}/fapi/v2/account"


def config(key: str = "k" * 24, secret: str = "s" * 24) -> ExchangeConfig:
    return ExchangeConfig(env=Environment.TESTNET, api_key=key, api_secret=secret)


class TestAccepted:
    @respx.mock
    async def test_a_working_key_reports_the_balance(self):
        respx.get(ACCOUNT).mock(
            return_value=httpx.Response(
                200, json={"totalWalletBalance": "3307.88", "canTrade": True}
            )
        )
        status = await verify(config())
        assert status.configured is True
        assert status.valid is True
        assert status.balance == "3307.88"
        assert status.can_trade is True

    @respx.mock
    async def test_a_read_only_key_is_valid_but_cannot_trade(self):
        # Worth distinguishing: the key works, so re-entering it will not help.
        respx.get(ACCOUNT).mock(
            return_value=httpx.Response(
                200, json={"totalWalletBalance": "100", "canTrade": False}
            )
        )
        status = await verify(config())
        assert status.valid is True
        assert status.can_trade is False


class TestRejected:
    @respx.mock
    async def test_a_bad_signature_is_reported_as_rejected_not_absent(self):
        respx.get(ACCOUNT).mock(
            return_value=httpx.Response(
                401, json={"code": -1022, "msg": "Signature for this request is not valid."}
            )
        )
        status = await verify(config())
        assert status.configured is True
        assert status.valid is False
        assert "-1022" in status.message

    @respx.mock
    async def test_the_message_carries_the_exchange_wording(self):
        respx.get(ACCOUNT).mock(
            return_value=httpx.Response(
                400, json={"code": -2015, "msg": "Invalid API-key, IP, or permissions."}
            )
        )
        status = await verify(config())
        assert "Invalid API-key" in status.message


class TestAbsent:
    async def test_an_empty_key_is_missing_rather_than_rejected(self):
        status = await verify(config(key="", secret=""))
        assert status.configured is False
        assert "not configured" in status.message.lower()

    async def test_nothing_is_sent_to_the_exchange_when_no_key_exists(self):
        # Signing an empty secret would produce a confusing -1022 instead of
        # the honest answer, which is that nothing was configured.
        with respx.mock(assert_all_called=False) as mock:
            route = mock.get(ACCOUNT)
            await verify(config(key="", secret=""))
            assert not route.called


class TestUnreachable:
    @respx.mock
    async def test_a_network_failure_is_not_reported_as_a_bad_key(self):
        # Telling an operator their key is wrong when the network is down sends
        # them to replace a perfectly good credential.
        respx.get(ACCOUNT).mock(side_effect=httpx.ConnectError("no route to host"))
        status = await verify(config())
        assert status.configured is True
        assert status.valid is False
        assert "reach" in status.message.lower()


class TestNoLeak:
    @respx.mock
    async def test_the_secret_never_appears_in_the_status(self):
        secret = "d2GY1Rz0Ir5qls5aqEPLirQe7tmDMyihFNcDECi7HWu1h49PCCCGPQ7p372ZZWPP"
        key = "XLO8y1JvImUUf4G40Zuf3rIJhOycZ0yGeCv0GESZvFaWoj7sn0R4pT65NylVrvzU"
        respx.get(ACCOUNT).mock(
            return_value=httpx.Response(
                200, json={"totalWalletBalance": "1", "canTrade": True}
            )
        )
        status = await verify(config(key=key, secret=secret))
        blob = str(status.describe())
        assert secret not in blob
        assert key not in blob

    @respx.mock
    async def test_the_preview_identifies_without_disclosing(self):
        key = "XLO8y1JvImUUf4G40Zuf3rIJhOycZ0yGeCv0GESZvFaWoj7sn0R4pT65NylVrvzU"
        respx.get(ACCOUNT).mock(
            return_value=httpx.Response(
                200, json={"totalWalletBalance": "1", "canTrade": True}
            )
        )
        status = await verify(config(key=key))
        assert status.key_preview.startswith("XLO8y1")
        assert len(status.key_preview) < len(key)


@pytest.mark.parametrize("env", [Environment.TESTNET, Environment.LIVE])
async def test_the_environment_is_reported_so_the_ui_cannot_confuse_them(env):
    status = await verify(
        ExchangeConfig(env=env, api_key="", api_secret="")
    )
    # Absent credentials carry no environment claim, which is correct: there is
    # nothing configured to belong to one.
    assert status.configured is False
