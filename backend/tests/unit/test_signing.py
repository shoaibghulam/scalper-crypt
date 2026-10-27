"""Request signing.

Binance signs the exact query string that is sent, in the order it is sent.
Rebuilding or reordering parameters between signing and sending produces a
-1022 signature error that looks like a credentials problem and is not.

The canonical vector below is from the Binance documentation and is the only
way to be certain the implementation is correct rather than merely consistent
with itself.
"""

from decimal import Decimal

import pytest

from scalper.exchange.signing import build_query, sign, signed_params, to_param

DOC_SECRET = "NhqPtmdSJYdKjVHjA7PZj4Mge3R5YNiP1e3UZjInClVN65XAbvqqM6A7H5fATj0j"
DOC_QUERY = (
    "symbol=LTCBTC&side=BUY&type=LIMIT&timeInForce=GTC&quantity=1&price=0.1"
    "&recvWindow=5000&timestamp=1499827319559"
)
DOC_SIGNATURE = "c8db56825ae71d6d79447849e617115f4a920fa2acdcab2b053c4b2838bd6b71"


class TestCanonicalVector:
    def test_matches_the_documented_signature(self):
        assert sign(DOC_QUERY, DOC_SECRET) == DOC_SIGNATURE

    def test_signature_is_lowercase_hex(self):
        signature = sign(DOC_QUERY, DOC_SECRET)
        assert signature == signature.lower()
        assert len(signature) == 64

    def test_a_changed_parameter_changes_the_signature(self):
        tampered = DOC_QUERY.replace("BUY", "SELL")
        assert sign(tampered, DOC_SECRET) != DOC_SIGNATURE


class TestParameterEncoding:
    def test_decimal_never_becomes_scientific_notation(self):
        assert to_param(Decimal("1E-8")) == "0.00000001"

    def test_decimal_keeps_its_exact_value(self):
        assert to_param(Decimal("0.123")) == "0.123"

    def test_float_is_refused(self):
        # Binary floats silently lose precision on prices and sizes. Every
        # numeric value on the wire must originate as a Decimal.
        with pytest.raises(TypeError, match="Decimal"):
            to_param(0.1)

    def test_booleans_use_the_lowercase_json_form(self):
        assert to_param(True) == "true"
        assert to_param(False) == "false"

    def test_integers_pass_through(self):
        assert to_param(10) == "10"

    def test_strings_pass_through(self):
        assert to_param("BTCUSDT") == "BTCUSDT"


class TestQueryBuilding:
    def test_insertion_order_is_preserved(self):
        query = build_query({"symbol": "BTCUSDT", "side": "BUY", "type": "MARKET"})
        assert query == "symbol=BTCUSDT&side=BUY&type=MARKET"

    def test_none_values_are_omitted(self):
        query = build_query({"symbol": "BTCUSDT", "price": None, "side": "BUY"})
        assert query == "symbol=BTCUSDT&side=BUY"

    def test_empty_mapping_yields_empty_string(self):
        assert build_query({}) == ""

    def test_values_are_url_encoded(self):
        query = build_query({"newClientOrderId": "bot-1/seq-2"})
        assert "/" not in query

    def test_decimal_values_are_formatted_not_repr(self):
        query = build_query({"quantity": Decimal("0.00100")})
        assert query == "quantity=0.00100"


class TestSignedParams:
    def test_signature_is_appended_last(self):
        query = signed_params(
            {"symbol": "BTCUSDT"},
            secret=DOC_SECRET,
            timestamp_ms=1499827319559,
            recv_window_ms=5000,
        )
        assert query.split("&")[-1].startswith("signature=")

    def test_timestamp_and_recv_window_are_included(self):
        query = signed_params(
            {"symbol": "BTCUSDT"},
            secret=DOC_SECRET,
            timestamp_ms=1499827319559,
            recv_window_ms=5000,
        )
        assert "timestamp=1499827319559" in query
        assert "recvWindow=5000" in query

    def test_the_signature_covers_everything_before_it(self):
        query = signed_params(
            {"symbol": "BTCUSDT"},
            secret=DOC_SECRET,
            timestamp_ms=1499827319559,
            recv_window_ms=5000,
        )
        body, _, signature = query.rpartition("&signature=")
        assert sign(body, DOC_SECRET) == signature

    def test_recv_window_is_capped_at_the_exchange_maximum(self):
        # Binance rejects anything above 60000. Inflating recvWindow is also
        # the wrong answer to clock drift; fix the clock instead.
        with pytest.raises(ValueError, match="recvWindow"):
            signed_params(
                {"symbol": "BTCUSDT"},
                secret=DOC_SECRET,
                timestamp_ms=1499827319559,
                recv_window_ms=120000,
            )

    def test_secret_never_appears_in_the_query(self):
        query = signed_params(
            {"symbol": "BTCUSDT"},
            secret=DOC_SECRET,
            timestamp_ms=1499827319559,
            recv_window_ms=5000,
        )
        assert DOC_SECRET not in query
