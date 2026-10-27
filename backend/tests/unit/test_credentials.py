"""Credential status.

Reported so the interface can tell the operator something true. Two rules: the
status never carries the key or the secret, and "configured" is never confused
with "working". A key that is present but rejected is a different problem from
no key at all, and saying so saves the operator from re-entering a key that was
already correct.
"""

from scalper.exchange.credentials import CredentialStatus, mask


class TestMasking:
    def test_a_key_is_shown_only_as_a_preview(self):
        key = "XLO8y1JvImUUf4G40Zuf3rIJhOycZ0yGeCv0GESZvFaWoj7sn0R4pT65NylVrvzU"
        preview = mask(key)
        assert key not in preview
        assert preview.startswith("XLO8y1")
        assert preview.endswith("rvzU")

    def test_a_short_key_is_not_revealed_by_the_preview(self):
        # Naive head-and-tail slicing would print a short secret in full.
        assert mask("abcd") == "..."

    def test_an_empty_key_masks_to_nothing_identifiable(self):
        assert mask("") == "..."


class TestStatus:
    def test_absent_credentials_are_not_reported_as_invalid(self):
        status = CredentialStatus.missing()
        assert status.configured is False
        assert status.valid is False
        assert "not configured" in status.message.lower()

    def test_present_but_rejected_is_distinguished_from_absent(self):
        status = CredentialStatus.rejected("key-preview", "testnet", "bad signature")
        assert status.configured is True
        assert status.valid is False
        assert "bad signature" in status.message

    def test_accepted_reports_the_balance(self):
        status = CredentialStatus.accepted(
            "key-preview", "testnet", balance="3307.88", can_trade=True
        )
        assert status.configured is True
        assert status.valid is True
        assert status.balance == "3307.88"
        assert status.can_trade is True

    def test_the_serialised_form_never_contains_a_secret_field(self):
        status = CredentialStatus.accepted("kp", "testnet", balance="1", can_trade=True)
        payload = status.describe()
        for banned in ("secret", "api_key", "apikey", "password"):
            assert not any(banned in key.lower() for key in payload)

    def test_the_serialised_form_carries_what_the_ui_needs(self):
        payload = CredentialStatus.missing().describe()
        for key in ("configured", "valid", "message", "environment"):
            assert key in payload
