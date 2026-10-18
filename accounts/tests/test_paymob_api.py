
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch
from django.test import override_settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.urls import reverse
from rest_framework.test import APITestCase

from accounts.models import CaptainProfile

User = get_user_model()


class CaptainPaymobTopUpAPITests(APITestCase):
    def setUp(self):
        self.captain_user = User.objects.create_user(
            username="paymob_api_captain",
            password="test-password",
            first_name="Test",
            last_name="Captain",
            email="captain@example.test",
            role=User.Role.CAPTAIN,
        )
        self.captain = CaptainProfile.objects.create(
            user=self.captain_user,
            phone_number="+201000000000",
        )
        self.customer = User.objects.create_user(
            username="paymob_api_customer",
            password="test-password",
            role=User.Role.CUSTOMER,
        )
        self.url = reverse("accounts:paymob_topup")

    @patch("accounts.api_views.create_paymob_topup")
    def test_captain_can_start_payment(self, create_topup):
        create_topup.return_value = (
            SimpleNamespace(reference="PAYMOB-TEST-123"),
            "https://accept.paymob.com/unifiedcheckout/?test=1",
        )
        self.client.force_authenticate(user=self.captain_user)

        response = self.client.post(
            self.url,
            {"amount": "50.00"},
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            response.data["topup_reference"],
            "PAYMOB-TEST-123",
        )
        self.assertEqual(
            response.data["checkout_url"],
            "https://accept.paymob.com/unifiedcheckout/?test=1",
        )
        create_topup.assert_called_once_with(
            captain=self.captain,
            amount=Decimal("50.00"),
        )

    @patch("accounts.api_views.create_paymob_topup")
    def test_customer_cannot_start_payment(self, create_topup):
        self.client.force_authenticate(user=self.customer)

        response = self.client.post(
            self.url,
            {"amount": "50.00"},
            format="json",
        )

        self.assertEqual(response.status_code, 403)
        create_topup.assert_not_called()

    @patch("accounts.api_views.create_paymob_topup")
    def test_unauthenticated_user_is_rejected(self, create_topup):
        response = self.client.post(
            self.url,
            {"amount": "50.00"},
            format="json",
        )

        self.assertIn(response.status_code, (401, 403))
        create_topup.assert_not_called()

    @patch("accounts.api_views.create_paymob_topup")
    def test_invalid_amounts_are_rejected(self, create_topup):
        self.client.force_authenticate(user=self.captain_user)

        for amount in (
            "abc",
            "NaN",
            "Infinity",
            "0.99",
            "100000000.00",
            True,
        ):
            with self.subTest(amount=amount):
                response = self.client.post(
                    self.url,
                    {"amount": amount},
                    format="json",
                )
                self.assertEqual(response.status_code, 400)

        create_topup.assert_not_called()

    @patch("accounts.api_views.create_paymob_topup")
    def test_missing_amount_is_rejected(self, create_topup):
        self.client.force_authenticate(user=self.captain_user)

        response = self.client.post(
            self.url,
            {},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        create_topup.assert_not_called()

    @patch("accounts.api_views.create_paymob_topup")
    def test_missing_captain_profile_is_rejected(self, create_topup):
        captain_without_profile = User.objects.create_user(
            username="captain_without_profile",
            password="test-password",
            role=User.Role.CAPTAIN,
        )
        self.client.force_authenticate(user=captain_without_profile)

        response = self.client.post(
            self.url,
            {"amount": "50.00"},
            format="json",
        )

        self.assertEqual(response.status_code, 404)
        create_topup.assert_not_called()

    @patch("accounts.api_views.create_paymob_topup")
    def test_provider_configuration_error_returns_503(
        self, create_topup
    ):
        create_topup.side_effect = ImproperlyConfigured(
            "Sensitive configuration detail"
        )
        self.client.force_authenticate(user=self.captain_user)

        response = self.client.post(
            self.url,
            {"amount": "50.00"},
            format="json",
        )

        self.assertEqual(response.status_code, 503)
        self.assertNotIn(
            "Sensitive configuration detail",
            str(response.data),
        )

    @patch("accounts.api_views.create_paymob_topup")
    def test_provider_validation_error_returns_400(
        self, create_topup
    ):
        create_topup.side_effect = ValidationError(
            "Unable to start payment right now."
        )
        self.client.force_authenticate(user=self.captain_user)

        response = self.client.post(
            self.url,
            {"amount": "50.00"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)




class PaymobReturnPageTests(APITestCase):
    @override_settings(
        ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"]
    )
    def test_return_page_loads_without_confirming_payment(self):
        response = self.client.get(
            reverse("accounts:paymob_return")
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "SNIPER GO")
        self.assertContains(response, "التحقق")