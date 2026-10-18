
import hashlib
import hmac

from django.test import SimpleTestCase

from accounts.services.paymob import (
    HMAC_CALLBACK_FIELDS,
    _nested_value,
    verify_processed_callback_hmac,
)


class PaymobHMACTest(SimpleTestCase):
    def setUp(self):
        self.secret = "local-test-secret"
        self.payload = {
            "obj": {
                "amount_cents": 10000,
                "created_at": "2026-01-01T12:00:00",
                "currency": "EGP",
                "error_occured": False,
                "has_parent_transaction": False,
                "id": 123456,
                "integration_id": 5489313,
                "is_3d_secure": True,
                "is_auth": False,
                "is_capture": False,
                "is_refunded": False,
                "is_standalone_payment": True,
                "is_voided": False,
                "order": {"id": 987654},
                "owner": 123,
                "pending": False,
                "source_data": {
                    "pan": "2346",
                    "sub_type": "MasterCard",
                    "type": "card",
                },
                "success": True,
            }
        }

    def make_signature(self):
        message = "".join(
            _nested_value(self.payload, key)
            for key in HMAC_CALLBACK_FIELDS
        )
        return hmac.new(
            self.secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha512,
        ).hexdigest()

    def test_accepts_valid_signature(self):
        signature = self.make_signature()

        self.assertTrue(
            verify_processed_callback_hmac(
                self.payload,
                signature,
                self.secret,
            )
        )

    def test_rejects_invalid_signature(self):
        self.assertFalse(
            verify_processed_callback_hmac(
                self.payload,
                "0" * 128,
                self.secret,
            )
        )

    def test_rejects_missing_hmac_secret(self):
        self.assertFalse(
            verify_processed_callback_hmac(
                self.payload,
                self.make_signature(),
                "",
            )
        )



class PaymobOfficialHMACFormatTest(SimpleTestCase):
    def test_transaction_fields_match_documented_order(self):
        payload = {
            "obj": {
                "amount_cents": 100,
                "created_at": "2020-03-25T18:39:44.719228",
                "currency": "EGP",
                "error_occured": False,
                "has_parent_transaction": False,
                "id": 2556706,
                "integration_id": 6741,
                "is_3d_secure": True,
                "is_auth": False,
                "is_capture": False,
                "is_refunded": False,
                "is_standalone_payment": True,
                "is_voided": False,
                "order": {"id": 4778239},
                "owner": 4705,
                "pending": False,
                "source_data": {
                    "pan": "2346",
                    "sub_type": "MasterCard",
                    "type": "card",
                },
                "success": True,
            }
        }

        actual = "".join(
            _nested_value(payload, key)
            for key in HMAC_CALLBACK_FIELDS
        )

        expected = (
            "1002020-03-25T18:39:44.719228EGP"
            "falsefalse25567066741truefalsefalse"
            "falsetruefalse47782394705false"
            "2346MasterCardcardtrue"
        )

        self.assertEqual(actual, expected)
