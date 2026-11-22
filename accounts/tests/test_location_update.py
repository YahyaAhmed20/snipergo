from decimal import Decimal
from tempfile import TemporaryDirectory

from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import CaptainProfile, CaptainWallet, User


@override_settings(MEDIA_ROOT=TemporaryDirectory().name)
class CaptainLocationUpdateAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()

        self.captain_user = User.objects.create_user(
            username="loc_captain",
            password="TestPassword-123!",
            role=User.Role.CAPTAIN,
        )
        self.captain = CaptainProfile.objects.create(
            user=self.captain_user,
        )
        CaptainWallet.objects.create(
            captain=self.captain,
            balance=Decimal("100.00"),
        )

        self.customer_user = User.objects.create_user(
            username="loc_customer",
            password="TestPassword-123!",
            role=User.Role.CUSTOMER,
        )

    def test_valid_captain_location_update(self):
        self.client.force_authenticate(user=self.captain_user)

        response = self.client.post(
            reverse("accounts:captain_location_update"),
            {"latitude": "29.9668", "longitude": "32.5498"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["latitude"], "29.9668")
        self.assertEqual(response.data["longitude"], "32.5498")
        self.assertIsNotNone(response.data["location_updated_at"])

        self.captain.refresh_from_db()
        self.assertEqual(self.captain.current_latitude, Decimal("29.9668"))
        self.assertEqual(self.captain.current_longitude, Decimal("32.5498"))
        self.assertIsNotNone(self.captain.location_updated_at)

    def test_invalid_latitude_rejected(self):
        self.client.force_authenticate(user=self.captain_user)

        response = self.client.post(
            reverse("accounts:captain_location_update"),
            {"latitude": "91", "longitude": "32.5498"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)

    def test_invalid_longitude_rejected(self):
        self.client.force_authenticate(user=self.captain_user)

        response = self.client.post(
            reverse("accounts:captain_location_update"),
            {"latitude": "29.9668", "longitude": "181"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)

    def test_unauthenticated_request_rejected(self):
        response = self.client.post(
            reverse("accounts:captain_location_update"),
            {"latitude": "29.9668", "longitude": "32.5498"},
            format="json",
        )

        self.assertEqual(response.status_code, 403)

    def test_non_captain_request_rejected(self):
        self.client.force_authenticate(user=self.customer_user)

        response = self.client.post(
            reverse("accounts:captain_location_update"),
            {"latitude": "29.9668", "longitude": "32.5498"},
            format="json",
        )

        self.assertEqual(response.status_code, 403)
