from decimal import Decimal
from unittest.mock import Mock, patch

from django.test import TestCase, override_settings
from django.core.exceptions import ValidationError

from accounts.models import User, CaptainProfile, WalletTopUpRequest
from accounts.services.paymob import create_paymob_topup


@override_settings(
    PAYMOB_API_KEY="test-api-key",
    PAYMOB_SECRET_KEY="test-secret-key",
    PAYMOB_PUBLIC_KEY="test-public-key",
    PAYMOB_INTEGRATION_ID="5489313",
    PAYMOB_CURRENCY="EGP",
    PAYMOB_NOTIFICATION_URL="https://example.test/paymob/webhook/",
    PAYMOB_REDIRECTION_URL="https://example.test/paymob/return/",
)
class PaymobTopUpServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="paymob_captain",
            password="test-password",
            first_name="Test",
            last_name="Captain",
            email="captain@example.test",
            role=User.Role.CAPTAIN,
        )
        self.captain = CaptainProfile.objects.create(
            user=self.user,
            phone_number="+201000000000",
        )

    @patch("accounts.services.paymob.requests.post")
    def test_creates_pending_topup_and_returns_checkout_url(self, post):
        post.return_value = Mock(
            status_code=201,
            json=Mock(return_value={
                "intention_order_id": "order-123",
                "client_secret": "client-secret-test",
                "id": "intention-456",
            }),
        )
        post.return_value.raise_for_status.return_value = None

        topup, checkout_url = create_paymob_topup(
            captain=self.captain,
            amount=Decimal("200.00"),
        )

        self.assertEqual(topup.amount, Decimal("200.00"))
        self.assertEqual(topup.method, WalletTopUpRequest.Method.PAYMOB)
        self.assertEqual(topup.status, WalletTopUpRequest.Status.PENDING)
        self.assertEqual(topup.payment_order_id, "order-123")
        self.assertEqual(topup.payment_reference, "intention-456")
        self.assertIn("clientSecret=client-secret-test", checkout_url)
        self.assertTrue(
            checkout_url.startswith(
                "https://accept.paymob.com/unifiedcheckout/"
            )
        )
        self.assertIn("publicKey=test-public-key", checkout_url)
        post.assert_called_once()

        request_kwargs = post.call_args.kwargs
        self.assertEqual(
            request_kwargs["headers"]["Authorization"],
            "Token test-secret-key",
        )
        self.assertEqual(request_kwargs["timeout"], 20)

        payload = request_kwargs["json"]
        self.assertEqual(payload["amount"], 20000)
        self.assertEqual(payload["currency"], "EGP")
        self.assertEqual(payload["payment_methods"], [5489313])
        self.assertEqual(
            payload["notification_url"],
            "https://example.test/paymob/webhook/",
        )
        self.assertEqual(
            payload["redirection_url"],
            "https://example.test/paymob/return/",
        )
        self.assertEqual(
            payload["items"][0]["amount"],
            20000,
        )

    @patch("accounts.services.paymob.requests.post")
    def test_rejects_provider_response_missing_checkout_fields(self, post):
        post.return_value = Mock(
            status_code=201,
            json=Mock(return_value={"id": "intention-456"}),
        )
        post.return_value.raise_for_status.return_value = None

        with self.assertRaises(ValidationError):
            create_paymob_topup(
                captain=self.captain,
                amount=Decimal("200.00"),
            )

    @patch("accounts.services.paymob.requests.post")
    def test_handles_provider_connection_failure(self, post):
        import requests

        post.side_effect = requests.ConnectionError("simulated failure")

        with self.assertRaises(ValidationError):
            create_paymob_topup(
                captain=self.captain,
                amount=Decimal("200.00"),
            )

        self.assertFalse(
            WalletTopUpRequest.objects.filter(
                captain=self.captain,
                method=WalletTopUpRequest.Method.PAYMOB,
            ).exists(),
            "Failed provider request unexpectedly left a pending top-up.",
        )

    @patch("accounts.services.paymob.requests.post")
    def test_rejects_amount_below_minimum_without_provider_call(self, post):
        with self.assertRaises(ValidationError):
            create_paymob_topup(
                captain=self.captain,
                amount=Decimal("199.99"),
            )

        post.assert_not_called()