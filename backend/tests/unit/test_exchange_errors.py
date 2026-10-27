"""Exchange error classification.

Every Binance error code needs a decided response, and the wrong response is
often worse than the error. Retrying a margin failure burns rate limit against
a condition that has not changed. Retrying a reduce-only rejection can place an
order against a position that is already flat. Treating an IP ban as a
transient blip leaves positions unmanaged for up to three days.

So each code maps to one of four actions, and the mapping is data, not
scattered conditionals.
"""

import pytest

from scalper.exchange.errors import (
    Action,
    BinanceError,
    classify,
    is_benign,
)


class TestBenignCodes:
    def test_margin_type_already_set_is_success(self):
        # -4046 means the account is already in the state we asked for.
        # Treating it as a failure would abort a perfectly good startup.
        assert is_benign(-4046)

    def test_a_real_error_is_not_benign(self):
        assert not is_benign(-2019)


class TestActions:
    @pytest.mark.parametrize(
        "code,action",
        [
            (-1021, Action.RETRY),   # timestamp outside recvWindow, resync then retry
            (-2019, Action.SKIP),    # margin insufficient, condition will not change
            (-2022, Action.VERIFY),  # reduceOnly rejected, probably already flat
            (-4164, Action.SKIP),    # below min notional
            (-4028, Action.SKIP),    # invalid leverage for this notional tier
            (-4047, Action.SKIP),    # margin type change blocked by open orders
            (-4048, Action.SKIP),    # margin type change blocked by a position
            (-1022, Action.HALT),    # bad signature, a configuration fault
            (-4120, Action.HALT),    # conditional order sent to the wrong endpoint
        ],
    )
    def test_code_maps_to_action(self, code, action):
        assert classify(code, "").action is action

    def test_unknown_codes_are_skipped_not_retried(self):
        # Defaulting to retry on an unrecognised failure is how a bot hammers
        # the exchange into an IP ban.
        assert classify(-99999, "something new").action is Action.SKIP


class TestSafetyCritical:
    def test_wrong_endpoint_is_fatal_because_it_means_no_stop_loss(self):
        # -4120 is returned when a STOP_MARKET is sent to /fapi/v1/order. If
        # that happens the position has no protective order, so continuing is
        # not an option.
        error = classify(-4120, "Order type not supported for this endpoint")
        assert error.action is Action.HALT
        assert "algoOrder" in str(error)

    def test_ip_ban_halts_the_engine(self):
        error = BinanceError.from_http(418, "banned until 1700000000000")
        assert error.action is Action.HALT

    def test_rate_limit_is_retryable(self):
        assert BinanceError.from_http(429, "Too many requests").action is Action.RETRY

    def test_server_error_is_retryable_but_ambiguous(self):
        # A 5xx may mean the order was accepted. The caller must query by
        # client order id rather than resend.
        error = BinanceError.from_http(503, "Service Unavailable")
        assert error.action is Action.VERIFY


class TestMessages:
    def test_error_text_includes_the_code(self):
        assert "-2019" in str(classify(-2019, "Margin is insufficient"))

    def test_error_text_includes_the_exchange_message(self):
        assert "Margin is insufficient" in str(classify(-2019, "Margin is insufficient"))

    def test_error_carries_guidance_for_the_operator(self):
        assert classify(-2019, "Margin is insufficient").guidance


class TestBenignSetupErrors:
    """Errors that mean "nothing to do", not "the trade failed".

    A live bot died on -4067 before placing a single order. The engine sets
    isolated margin before every entry, and Binance refuses that call once the
    symbol already has open orders on it - which is the normal state when a
    second bot trades a symbol another bot is already holding. The mode was
    already what we wanted; the call was simply redundant.

    Treating a redundant setup call as fatal meant one bot could not trade any
    symbol another bot had touched.
    """

    def test_an_unchanged_margin_type_is_benign(self):
        assert is_benign(-4046)

    def test_position_mode_locked_by_orders_is_not_success(self):
        # -4067. The position side cannot be changed while orders rest on the
        # symbol, and for our purposes it does not need to be.
        assert not is_benign(-4067)

    def test_an_unchanged_position_side_is_benign(self):
        # -4059, the same situation reported under a different code.
        assert is_benign(-4059)

    def test_the_exemption_stays_narrow(self):
        # Insufficient margin must never be swallowed as housekeeping.
        assert not is_benign(-2019)
        # Nor must a rejected leverage, which means the trade is mis-sized.
        assert not is_benign(-4028)
