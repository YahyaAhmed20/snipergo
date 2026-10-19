
from decimal import Decimal
import json
from unittest.mock import patch

from django.urls import reverse
from django.test import TestCase, SimpleTestCase, override_settings

@override_settings(PAYMOB_HMAC_SECRET="local-test-secret")
class PaymobWebhookSecurityTests(TestCase):
    def test_rejects_invalid_hmac(self):
        response = self.client.post(
            reverse("accounts:paymob_transaction_webhook")
            + "?hmac=invalid-signature",
            data=json.dumps({"obj": {}}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)





    def test_rejects_signed_callback_with_unsupported_type(self):
        import hashlib
        import hmac

        from accounts.services.paymob import (
            HMAC_CALLBACK_FIELDS,
            _nested_value,
        )

        payload = {
            "type": "OTHER",
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
            },
        }

        message = "".join(
            _nested_value(payload, key)
            for key in HMAC_CALLBACK_FIELDS
        )
        signature = hmac.new(
            b"local-test-secret",
            message.encode("utf-8"),
            hashlib.sha512,
        ).hexdigest()

        response = self.client.post(
            reverse("accounts:paymob_transaction_webhook")
            + f"?hmac={signature}",
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)





    def test_successful_payment_callback_is_idempotent(self):
        import hashlib
        import hmac
        from decimal import Decimal

        from django.contrib.auth import get_user_model

        from accounts.models import (
            CaptainProfile,
            CaptainWallet,
            WalletTopUpRequest,
        )
        from accounts.services.paymob import (
            HMAC_CALLBACK_FIELDS,
            _nested_value,
        )
        from trips.models import FinancialTransaction

        user = get_user_model().objects.create_user(
            username="paymob_test_captain",
            password="local-test-password",
            first_name="Test",
            last_name="Captain",
            email="captain@example.test",
            role="CAPTAIN",
        )
        captain = CaptainProfile.objects.create(
            user=user,
            phone_number="+201000000000",
        )
        wallet = CaptainWallet.objects.create(
            captain=captain,
            balance=Decimal("0.00"),
        )
        topup = WalletTopUpRequest.objects.create(
            captain=captain,
            amount=Decimal("100.00"),
            method=WalletTopUpRequest.Method.PAYMOB,
            status=WalletTopUpRequest.Status.PENDING,
            reference="PAYMOB-TEST-IDEMPOTENT",
            payment_order_id="987654",
        )

        payload = {
            "type": "TRANSACTION",
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
            },
        }

        message = "".join(
            _nested_value(payload, key)
            for key in HMAC_CALLBACK_FIELDS
        )
        signature = hmac.new(
            b"local-test-secret",
            message.encode("utf-8"),
            hashlib.sha512,
        ).hexdigest()

        url = (
            reverse("accounts:paymob_transaction_webhook")
            + f"?hmac={signature}"
        )

        first_response = self.client.post(
            url,
            data=json.dumps(payload),
            content_type="application/json",
        )
        second_response = self.client.post(
            url,
            data=json.dumps(payload),
            content_type="application/json",
        )

        wallet.refresh_from_db()
        topup.refresh_from_db()

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(wallet.balance, Decimal("100.00"))
        self.assertEqual(
            topup.status,
            WalletTopUpRequest.Status.COMPLETED,
        )
        self.assertEqual(topup.payment_transaction_id, "123456")
        self.assertEqual(
            FinancialTransaction.objects.filter(
                reference=f"wallet-topup:{topup.reference}"
            ).count(),
            1,
        )




    @patch("accounts.paymob_views.credit_wallet")
    def test_wallet_credit_failure_does_not_complete_topup(self, mock_credit):
        import hashlib
        import hmac

        from django.contrib.auth import get_user_model
        from accounts.models import (
            CaptainProfile,
            CaptainWallet,
            WalletTopUpRequest,
        )
        from accounts.services.paymob import (
            HMAC_CALLBACK_FIELDS,
            _nested_value,
        )
        from trips.models import FinancialTransaction

        user = get_user_model().objects.create_user(
            username="paymob_credit_failure",
            password="local-test-password",
            role="CAPTAIN",
        )
        captain = CaptainProfile.objects.create(
            user=user,
            phone_number="+201000000010",
        )
        wallet = CaptainWallet.objects.create(
            captain=captain,
            balance=Decimal("0.00"),
        )
        topup = WalletTopUpRequest.objects.create(
            captain=captain,
            amount=Decimal("100.00"),
            method=WalletTopUpRequest.Method.PAYMOB,
            status=WalletTopUpRequest.Status.PENDING,
            reference="PAYMOB-CREDIT-FAILURE",
            payment_order_id="987699",
        )

        payload = {
            "type": "TRANSACTION",
            "obj": {
                "amount_cents": 10000,
                "created_at": "2026-01-01T12:00:00",
                "currency": "EGP",
                "error_occured": False,
                "has_parent_transaction": False,
                "id": 123499,
                "integration_id": 5489313,
                "is_3d_secure": True,
                "is_auth": False,
                "is_capture": False,
                "is_refunded": False,
                "is_standalone_payment": True,
                "is_voided": False,
                "order": {"id": 987699},
                "owner": 123,
                "pending": False,
                "source_data": {
                    "pan": "2346",
                    "sub_type": "MasterCard",
                    "type": "card",
                },
                "success": True,
            },
        }

        message = "".join(
            _nested_value(payload, key)
            for key in HMAC_CALLBACK_FIELDS
        )
        signature = hmac.new(
            b"local-test-secret",
            message.encode("utf-8"),
            hashlib.sha512,
        ).hexdigest()

        mock_credit.side_effect = RuntimeError("Simulated wallet failure")


        with self.assertRaises(RuntimeError):
            self.client.post(
                reverse("accounts:paymob_transaction_webhook")
                + f"?hmac={signature}",
                data=json.dumps(payload),
                content_type="application/json",
            )


        wallet.refresh_from_db()
        topup.refresh_from_db()


        self.assertEqual(wallet.balance, Decimal("0.00"))
        self.assertEqual(topup.status, WalletTopUpRequest.Status.PENDING)
        self.assertEqual(topup.payment_transaction_id, "")
        self.assertFalse(
            FinancialTransaction.objects.filter(
                reference=f"wallet-topup:{topup.reference}"
            ).exists()
        )

    def test_rejects_invalid_amount_cents_types(self):
        import hashlib
        import hmac
        import json

        from django.contrib.auth import get_user_model

        from accounts.models import (
            CaptainProfile,
            CaptainWallet,
            WalletTopUpRequest,
        )
        from accounts.services.paymob import (
            HMAC_CALLBACK_FIELDS,
            _nested_value,
        )

        user = get_user_model().objects.create_user(
            username="paymob_invalid_amount",
            password="local-test-password",
            first_name="Test",
            last_name="Captain",
            email="invalid-amount@example.test",
            role="CAPTAIN",
        )
        captain = CaptainProfile.objects.create(
            user=user,
            phone_number="+201000000002",
        )
        wallet = CaptainWallet.objects.create(
            captain=captain,
            balance=Decimal("0.00"),
        )
        topup = WalletTopUpRequest.objects.create(
            captain=captain,
            amount=Decimal("100.00"),
            method=WalletTopUpRequest.Method.PAYMOB,
            status=WalletTopUpRequest.Status.PENDING,
            reference="PAYMOB-INVALID-AMOUNT",
            payment_order_id="987655",
        )

        invalid_amounts = [True, 0, -1, "10000", 10000.5]

        for index, invalid_amount in enumerate(invalid_amounts):
            with self.subTest(amount=invalid_amount):
                payload = {
                    "type": "TRANSACTION",
                    "obj": {
                        "amount_cents": invalid_amount,
                        "created_at": "2026-01-01T12:00:00",
                        "currency": "EGP",
                        "error_occured": False,
                        "has_parent_transaction": False,
                        "id": 123457 + index,
                        "integration_id": 5489313,
                        "is_3d_secure": True,
                        "is_auth": False,
                        "is_capture": False,
                        "is_refunded": False,
                        "is_standalone_payment": True,
                        "is_voided": False,
                        "order": {"id": 987655},
                        "owner": 123,
                        "pending": False,
                        "source_data": {
                            "pan": "2346",
                            "sub_type": "MasterCard",
                            "type": "card",
                        },
                        "success": True,
                    },
                }

                message = "".join(
                    _nested_value(payload, key)
                    for key in HMAC_CALLBACK_FIELDS
                )
                signature = hmac.new(
                    b"local-test-secret",
                    message.encode("utf-8"),
                    hashlib.sha512,
                ).hexdigest()

                response = self.client.post(
                    reverse("accounts:paymob_transaction_webhook")
                    + f"?hmac={signature}",
                    data=json.dumps(payload),
                    content_type="application/json",
                )

                self.assertEqual(response.status_code, 400)

        wallet.refresh_from_db()
        topup.refresh_from_db()

        self.assertEqual(wallet.balance, Decimal("0.00"))
        self.assertEqual(topup.status, WalletTopUpRequest.Status.PENDING)



    def test_rejects_null_amount_cents(self):
        from accounts.services.paymob import verify_processed_callback_hmac

        payload = {
            "type": "TRANSACTION",
            "obj": {"amount_cents": None},
        }

        self.assertFalse(
            verify_processed_callback_hmac(
                payload,
                "unused-signature",
                "local-test-secret",
            )
        )




    def test_ineligible_payment_statuses_never_credit_wallet(self):
        import hashlib
        import hmac

        from django.contrib.auth import get_user_model

        from accounts.models import (
            CaptainProfile,
            CaptainWallet,
            WalletTopUpRequest,
        )
        from accounts.services.paymob import (
            HMAC_CALLBACK_FIELDS,
            _nested_value,
        )

        user = get_user_model().objects.create_user(
            username="paymob_ineligible_status",
            password="local-test-password",
            role="CAPTAIN",
        )
        captain = CaptainProfile.objects.create(
            user=user,
            phone_number="+201000000003",
        )
        wallet = CaptainWallet.objects.create(
            captain=captain,
            balance=Decimal("0.00"),
        )
        topup = WalletTopUpRequest.objects.create(
            captain=captain,
            amount=Decimal("100.00"),
            method=WalletTopUpRequest.Method.PAYMOB,
            status=WalletTopUpRequest.Status.PENDING,
            reference="PAYMOB-INELIGIBLE-STATUS",
            payment_order_id="987656",
        )

        invalid_statuses = [
            {"success": False},
            {"pending": True},
            {"error_occured": True},
            {"is_refunded": True},
            {"is_voided": True},
        ]

        for index, status_changes in enumerate(invalid_statuses):
            with self.subTest(status_changes=status_changes):
                payload = {
                    "type": "TRANSACTION",
                    "obj": {
                        "amount_cents": 10000,
                        "created_at": "2026-01-01T12:00:00",
                        "currency": "EGP",
                        "error_occured": False,
                        "has_parent_transaction": False,
                        "id": 123460 + index,
                        "integration_id": 5489313,
                        "is_3d_secure": True,
                        "is_auth": False,
                        "is_capture": False,
                        "is_refunded": False,
                        "is_standalone_payment": True,
                        "is_voided": False,
                        "order": {"id": 987656},
                        "owner": 123,
                        "pending": False,
                        "source_data": {
                            "pan": "2346",
                            "sub_type": "MasterCard",
                            "type": "card",
                        },
                        "success": True,
                    },
                }
                payload["obj"].update(status_changes)

                message = "".join(
                    _nested_value(payload, key)
                    for key in HMAC_CALLBACK_FIELDS
                )
                signature = hmac.new(
                    b"local-test-secret",
                    message.encode("utf-8"),
                    hashlib.sha512,
                ).hexdigest()

                response = self.client.post(
                    reverse("accounts:paymob_transaction_webhook")
                    + f"?hmac={signature}",
                    data=json.dumps(payload),
                    content_type="application/json",
                )

                self.assertEqual(response.status_code, 200)

        wallet.refresh_from_db()
        topup.refresh_from_db()

        self.assertEqual(wallet.balance, Decimal("0.00"))
        self.assertEqual(topup.status, WalletTopUpRequest.Status.PENDING)
        self.assertEqual(topup.payment_transaction_id, "")




    def test_rejects_payment_amount_mismatch_without_crediting_wallet(self):
        import hashlib
        import hmac

        from django.contrib.auth import get_user_model

        from accounts.models import (
            CaptainProfile,
            CaptainWallet,
            WalletTopUpRequest,
        )
        from accounts.services.paymob import (
            HMAC_CALLBACK_FIELDS,
            _nested_value,
        )

        user = get_user_model().objects.create_user(
            username="paymob_amount_mismatch",
            password="local-test-password",
            role="CAPTAIN",
        )
        captain = CaptainProfile.objects.create(
            user=user,
            phone_number="+201000000004",
        )
        wallet = CaptainWallet.objects.create(
            captain=captain,
            balance=Decimal("0.00"),
        )
        topup = WalletTopUpRequest.objects.create(
            captain=captain,
            amount=Decimal("100.00"),
            method=WalletTopUpRequest.Method.PAYMOB,
            status=WalletTopUpRequest.Status.PENDING,
            reference="PAYMOB-AMOUNT-MISMATCH",
            payment_order_id="987657",
        )

        payload = {
            "type": "TRANSACTION",
            "obj": {
                "amount_cents": 9999,
                "created_at": "2026-01-01T12:00:00",
                "currency": "EGP",
                "error_occured": False,
                "has_parent_transaction": False,
                "id": 123470,
                "integration_id": 5489313,
                "is_3d_secure": True,
                "is_auth": False,
                "is_capture": False,
                "is_refunded": False,
                "is_standalone_payment": True,
                "is_voided": False,
                "order": {"id": 987657},
                "owner": 123,
                "pending": False,
                "source_data": {
                    "pan": "2346",
                    "sub_type": "MasterCard",
                    "type": "card",
                },
                "success": True,
            },
        }

        message = "".join(
            _nested_value(payload, key)
            for key in HMAC_CALLBACK_FIELDS
        )
        signature = hmac.new(
            b"local-test-secret",
            message.encode("utf-8"),
            hashlib.sha512,
        ).hexdigest()

        response = self.client.post(
            reverse("accounts:paymob_transaction_webhook")
            + f"?hmac={signature}",
            data=json.dumps(payload),
            content_type="application/json",
        )

        wallet.refresh_from_db()
        topup.refresh_from_db()

        self.assertEqual(response.status_code, 400)
        self.assertEqual(wallet.balance, Decimal("0.00"))
        self.assertEqual(topup.status, WalletTopUpRequest.Status.PENDING)





    def test_rejects_currency_mismatch_without_crediting_wallet(self):
        import hashlib
        import hmac

        from django.contrib.auth import get_user_model

        from accounts.models import (
            CaptainProfile,
            CaptainWallet,
            WalletTopUpRequest,
        )
        from accounts.services.paymob import (
            HMAC_CALLBACK_FIELDS,
            _nested_value,
        )

        user = get_user_model().objects.create_user(
            username="paymob_currency_mismatch",
            password="local-test-password",
            role="CAPTAIN",
        )
        captain = CaptainProfile.objects.create(
            user=user,
            phone_number="+201000000005",
        )
        wallet = CaptainWallet.objects.create(
            captain=captain,
            balance=Decimal("0.00"),
        )
        topup = WalletTopUpRequest.objects.create(
            captain=captain,
            amount=Decimal("100.00"),
            method=WalletTopUpRequest.Method.PAYMOB,
            status=WalletTopUpRequest.Status.PENDING,
            reference="PAYMOB-CURRENCY-MISMATCH",
            payment_order_id="987658",
        )

        payload = {
            "type": "TRANSACTION",
            "obj": {
                "amount_cents": 10000,
                "created_at": "2026-01-01T12:00:00",
                "currency": "USD",
                "error_occured": False,
                "has_parent_transaction": False,
                "id": 123471,
                "integration_id": 5489313,
                "is_3d_secure": True,
                "is_auth": False,
                "is_capture": False,
                "is_refunded": False,
                "is_standalone_payment": True,
                "is_voided": False,
                "order": {"id": 987658},
                "owner": 123,
                "pending": False,
                "source_data": {
                    "pan": "2346",
                    "sub_type": "MasterCard",
                    "type": "card",
                },
                "success": True,
            },
        }

        message = "".join(
            _nested_value(payload, key)
            for key in HMAC_CALLBACK_FIELDS
        )
        signature = hmac.new(
            b"local-test-secret",
            message.encode("utf-8"),
            hashlib.sha512,
        ).hexdigest()

        response = self.client.post(
            reverse("accounts:paymob_transaction_webhook")
            + f"?hmac={signature}",
            data=json.dumps(payload),
            content_type="application/json",
        )

        wallet.refresh_from_db()
        topup.refresh_from_db()

        self.assertEqual(response.status_code, 400)
        self.assertEqual(wallet.balance, Decimal("0.00"))
        self.assertEqual(topup.status, WalletTopUpRequest.Status.PENDING)




    def test_rejects_integration_id_mismatch_without_crediting_wallet(self):
        import hashlib
        import hmac

        from django.contrib.auth import get_user_model

        from accounts.models import (
            CaptainProfile,
            CaptainWallet,
            WalletTopUpRequest,
        )
        from accounts.services.paymob import (
            HMAC_CALLBACK_FIELDS,
            _nested_value,
        )

        user = get_user_model().objects.create_user(
            username="paymob_integration_mismatch",
            password="local-test-password",
            role="CAPTAIN",
        )
        captain = CaptainProfile.objects.create(
            user=user,
            phone_number="+201000000006",
        )
        wallet = CaptainWallet.objects.create(
            captain=captain,
            balance=Decimal("0.00"),
        )
        topup = WalletTopUpRequest.objects.create(
            captain=captain,
            amount=Decimal("100.00"),
            method=WalletTopUpRequest.Method.PAYMOB,
            status=WalletTopUpRequest.Status.PENDING,
            reference="PAYMOB-INTEGRATION-MISMATCH",
            payment_order_id="987659",
        )

        payload = {
            "type": "TRANSACTION",
            "obj": {
                "amount_cents": 10000,
                "created_at": "2026-01-01T12:00:00",
                "currency": "EGP",
                "error_occured": False,
                "has_parent_transaction": False,
                "id": 123472,
                "integration_id": 9999999,
                "is_3d_secure": True,
                "is_auth": False,
                "is_capture": False,
                "is_refunded": False,
                "is_standalone_payment": True,
                "is_voided": False,
                "order": {"id": 987659},
                "owner": 123,
                "pending": False,
                "source_data": {
                    "pan": "2346",
                    "sub_type": "MasterCard",
                    "type": "card",
                },
                "success": True,
            },
        }

        message = "".join(
            _nested_value(payload, key)
            for key in HMAC_CALLBACK_FIELDS
        )
        signature = hmac.new(
            b"local-test-secret",
            message.encode("utf-8"),
            hashlib.sha512,
        ).hexdigest()

        response = self.client.post(
            reverse("accounts:paymob_transaction_webhook")
            + f"?hmac={signature}",
            data=json.dumps(payload),
            content_type="application/json",
        )

        wallet.refresh_from_db()
        topup.refresh_from_db()

        self.assertEqual(response.status_code, 400)
        self.assertEqual(wallet.balance, Decimal("0.00"))
        self.assertEqual(topup.status, WalletTopUpRequest.Status.PENDING)



    def test_rejects_transaction_id_already_used_by_another_topup(self):
        import hashlib
        import hmac

        from django.contrib.auth import get_user_model

        from accounts.models import (
            CaptainProfile,
            CaptainWallet,
            WalletTopUpRequest,
        )
        from accounts.services.paymob import (
            HMAC_CALLBACK_FIELDS,
            _nested_value,
        )

        user = get_user_model().objects.create_user(
            username="paymob_reused_transaction",
            password="local-test-password",
            role="CAPTAIN",
        )
        captain = CaptainProfile.objects.create(
            user=user,
            phone_number="+201000000007",
        )
        wallet = CaptainWallet.objects.create(
            captain=captain,
            balance=Decimal("0.00"),
        )

        WalletTopUpRequest.objects.create(
            captain=captain,
            amount=Decimal("100.00"),
            method=WalletTopUpRequest.Method.PAYMOB,
            status=WalletTopUpRequest.Status.COMPLETED,
            reference="PAYMOB-TRANSACTION-ALREADY-USED",
            payment_order_id="987660",
            payment_transaction_id="123473",
        )

        pending_topup = WalletTopUpRequest.objects.create(
            captain=captain,
            amount=Decimal("100.00"),
            method=WalletTopUpRequest.Method.PAYMOB,
            status=WalletTopUpRequest.Status.PENDING,
            reference="PAYMOB-TRANSACTION-REUSE-ATTEMPT",
            payment_order_id="987661",
        )

        payload = {
            "type": "TRANSACTION",
            "obj": {
                "amount_cents": 10000,
                "created_at": "2026-01-01T12:00:00",
                "currency": "EGP",
                "error_occured": False,
                "has_parent_transaction": False,
                "id": 123473,
                "integration_id": 5489313,
                "is_3d_secure": True,
                "is_auth": False,
                "is_capture": False,
                "is_refunded": False,
                "is_standalone_payment": True,
                "is_voided": False,
                "order": {"id": 987661},
                "owner": 123,
                "pending": False,
                "source_data": {
                    "pan": "2346",
                    "sub_type": "MasterCard",
                    "type": "card",
                },
                "success": True,
            },
        }

        message = "".join(
            _nested_value(payload, key)
            for key in HMAC_CALLBACK_FIELDS
        )
        signature = hmac.new(
            b"local-test-secret",
            message.encode("utf-8"),
            hashlib.sha512,
        ).hexdigest()

        response = self.client.post(
            reverse("accounts:paymob_transaction_webhook")
            + f"?hmac={signature}",
            data=json.dumps(payload),
            content_type="application/json",
        )

        wallet.refresh_from_db()
        pending_topup.refresh_from_db()

        self.assertEqual(response.status_code, 409)
        self.assertEqual(wallet.balance, Decimal("0.00"))
        self.assertEqual(
            pending_topup.status,
            WalletTopUpRequest.Status.PENDING,
        )
        self.assertEqual(pending_topup.payment_transaction_id, "")




class PaymobReturnPageTests(SimpleTestCase):
    def test_return_page_renders_arabic_message(self):
        response = self.client.get(
            reverse("accounts:paymob_return"),
            HTTP_HOST="localhost",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.charset, "utf-8")
        self.assertContains(response, "نتيجة الدفع")
        self.assertContains(response, "تمت العودة من صفحة الدفع")
        self.assertContains(
            response,
            "سيتم تحديث رصيد المحفظة بعد تأكيد العملية.",
        )
