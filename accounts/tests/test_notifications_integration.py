import asyncio
from decimal import Decimal
from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from channels.testing import WebsocketCommunicator
from project.asgi import application

from accounts.models import (
    CaptainProfile,
    CaptainWallet,
    CaptainDocument,
    CustomerProfile,
    Notification,
    User,
)
from accounts.services.notifications import (
    create_notification,
    dispatch_notification_websocket,
    get_user_notifications,
    get_unread_notification_count,
)
from trips.models import CaptainOffer, CommissionConfig, Trip, TripReview
from trips.services.offers import create_captain_offer, accept_captain_offer
from trips.services.lifecycle import (
    mark_captain_arriving,
    mark_captain_arrived,
    start_trip,
    complete_trip,
    cancel_trip,
)
from trips.services.reviews import create_trip_review
from vehicles.models import Vehicle

User = get_user_model()


def _make_user(username, role, password="test-password"):
    return User.objects.create_user(
        username=username,
        password=password,
        role=role,
    )


def _make_customer(username="cust"):
    user = _make_user(username, User.Role.CUSTOMER)
    return CustomerProfile.objects.create(user=user), user


def _make_captain(username="capt"):
    user = _make_user(username, User.Role.CAPTAIN)
    captain = CaptainProfile.objects.create(
        user=user,
        approval_status=CaptainProfile.ApprovalStatus.APPROVED,
        operational_status=CaptainProfile.OperationalStatus.ONLINE,
    )
    CaptainWallet.objects.create(captain=captain, balance=Decimal("500.00"))

    # Approved documents (required by accept_captain_offer eligibility check)
    for doc_type in [
        CaptainDocument.DocumentType.NATIONAL_ID,
        CaptainDocument.DocumentType.DRIVING_LICENSE,
        CaptainDocument.DocumentType.VEHICLE_LICENSE,
        CaptainDocument.DocumentType.CRIMINAL_RECORD,
    ]:
        CaptainDocument.objects.create(
            captain=captain,
            document_type=doc_type,
            file=SimpleUploadedFile("doc.pdf", b"fake-content"),
            review_status=CaptainDocument.ReviewStatus.APPROVED,
            expires_at=None,
        )

    # Approved vehicle (required by accept_captain_offer)
    Vehicle.objects.create(
        captain=captain,
        vehicle_type=Vehicle.VehicleType.SCOOTER,
        brand="TestBrand",
        model_name="TestModel",
        manufacture_year=2024,
        plate_number=f"PLATE-{captain.pk}",
        chassis_number=f"CHASSIS-{captain.pk}",
        front_photo=SimpleUploadedFile("front.jpg", b"fake-content"),
        rear_photo=SimpleUploadedFile("rear.jpg", b"fake-content"),
        side_photo=SimpleUploadedFile("side.jpg", b"fake-content"),
        plate_photo=SimpleUploadedFile("plate.jpg", b"fake-content"),
        approval_status=Vehicle.ApprovalStatus.APPROVED,
    )

    # Active commission config (required by accept_captain_offer)
    CommissionConfig.objects.create(
        is_active=True,
        percentage=Decimal("10.00"),
    )

    return captain, user


def _make_trip(customer, captain=None, status=Trip.Status.REQUESTED):
    return Trip.objects.create(
        customer=customer,
        captain=captain,
        pickup_address="Pickup",
        pickup_latitude=Decimal("29.9670"),
        pickup_longitude=Decimal("32.5498"),
        destination_address="Destination",
        destination_latitude=Decimal("29.9800"),
        destination_longitude=Decimal("32.5600"),
        status=status,
        estimated_fare=Decimal("100.00"),
        agreed_fare=Decimal("100.00") if captain else None,
        commission_percentage=Decimal("10.00"),
    )


class _WebSocketHelper:
    @staticmethod
    async def connect(user):
        communicator = WebsocketCommunicator(
            application,
            "/ws/notifications/",
            headers=[(b"origin", b"https://snipergo-production.up.railway.app")],
        )
        communicator.scope["user"] = user
        connected, _ = await communicator.connect()
        assert connected, f"WebSocket connect failed for {user.username}"
        return communicator


class NotificationIntegrationTests(TestCase):
    """End-to-end integration: business action -> notification persisted."""

    def setUp(self):
        self.customer, self.customer_user = _make_customer("int_customer")
        self.captain, self.captain_user = _make_captain("int_captain")
        # Pricing config required by create_captain_offer
        from trips.models import TripPricingConfig
        TripPricingConfig.objects.create(
            city="Suez",
            base_fare=Decimal("10.00"),
            price_per_km=Decimal("5.00"),
            minimum_fare=Decimal("15.00"),
            negotiation_percent=Decimal("20.00"),
            is_active=True,
        )

    # --- Offer creation ---

    def test_captain_offer_notifies_customer(self):
        trip = _make_trip(self.customer)
        create_captain_offer(
            trip_id=trip.pk, captain=self.captain, amount=Decimal("80.00")
        )

        notifications = get_user_notifications(user=self.customer_user)
        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0].type, "OFFER_RECEIVED")
        self.assertEqual(notifications[0].title, "عرض جديد")
        self.assertEqual(notifications[0].payload["trip_id"], trip.pk)
        self.assertEqual(notifications[0].payload["captain_id"], self.captain.pk)
        self.assertIsNotNone(notifications[0].payload["offer_id"])

    # --- Offer acceptance ---

    def test_customer_accept_offer_notifies_captain(self):
        trip = _make_trip(self.customer, captain=None, status=Trip.Status.REQUESTED)
        from django.utils import timezone
        from datetime import timedelta

        offer = CaptainOffer.objects.create(
            trip=trip,
            captain=self.captain,
            amount=Decimal("80.00"),
            status=CaptainOffer.Status.PENDING,
            expires_at=timezone.now() + timedelta(minutes=30),
        )
        accept_captain_offer(offer_id=offer.pk, customer=self.customer)

        notifications = get_user_notifications(user=self.captain_user)
        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0].type, "OFFER_ACCEPTED")
        self.assertEqual(notifications[0].title, "تم قبول عرضك")
        self.assertEqual(notifications[0].payload["trip_id"], trip.pk)

    def test_offer_acceptance_no_notification_to_customer(self):
        trip = _make_trip(self.customer)
        from django.utils import timezone
        from datetime import timedelta

        offer = CaptainOffer.objects.create(
            trip=trip,
            captain=self.captain,
            amount=Decimal("80.00"),
            status=CaptainOffer.Status.PENDING,
            expires_at=timezone.now() + timedelta(minutes=30),
        )
        accept_captain_offer(offer_id=offer.pk, customer=self.customer)

        notifications = get_user_notifications(user=self.customer_user)
        self.assertEqual(len(notifications), 0)

    # --- Trip lifecycle status changes ---

    def test_mark_arriving_notifies_customer(self):
        trip = _make_trip(
            self.customer, captain=self.captain, status=Trip.Status.ACCEPTED
        )
        mark_captain_arriving(trip_id=trip.pk, captain=self.captain)

        notifications = get_user_notifications(user=self.customer_user)
        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0].type, "TRIP_STATUS_CHANGED")
        self.assertEqual(
            notifications[0].payload["status"], Trip.Status.ARRIVING
        )

    def test_mark_arrived_notifies_customer(self):
        trip = _make_trip(
            self.customer, captain=self.captain, status=Trip.Status.ARRIVING
        )
        mark_captain_arrived(trip_id=trip.pk, captain=self.captain)

        notifications = get_user_notifications(user=self.customer_user)
        self.assertEqual(len(notifications), 1)
        self.assertEqual(
            notifications[0].payload["status"], Trip.Status.ARRIVED
        )

    def test_start_trip_notifies_customer(self):
        trip = _make_trip(
            self.customer, captain=self.captain, status=Trip.Status.ARRIVED
        )
        start_trip(trip_id=trip.pk, captain=self.captain)

        notifications = get_user_notifications(user=self.customer_user)
        self.assertEqual(len(notifications), 1)
        self.assertEqual(
            notifications[0].payload["status"], Trip.Status.IN_PROGRESS
        )

    def test_complete_trip_notifies_customer(self):
        trip = _make_trip(
            self.customer,
            captain=self.captain,
            status=Trip.Status.IN_PROGRESS,
        )
        trip.final_fare = Decimal("100.00")
        trip.save()
        complete_trip(trip_id=trip.pk, captain=self.captain)

        notifications = get_user_notifications(user=self.customer_user)
        self.assertEqual(len(notifications), 1)
        self.assertEqual(
            notifications[0].payload["status"], Trip.Status.COMPLETED
        )

    # --- Trip cancellation ---

    def test_cancel_trip_by_customer_notifies_captain(self):
        trip = _make_trip(
            self.customer, captain=self.captain, status=Trip.Status.ACCEPTED
        )
        cancel_trip(
            trip_id=trip.pk,
            cancelled_by=self.customer_user,
            reason="Changed my mind",
        )

        captain_notifications = get_user_notifications(user=self.captain_user)
        self.assertEqual(len(captain_notifications), 1)
        self.assertEqual(
            captain_notifications[0].payload["status"], Trip.Status.CANCELLED
        )

        customer_notifications = get_user_notifications(user=self.customer_user)
        self.assertEqual(len(customer_notifications), 0)

    def test_cancel_trip_by_captain_notifies_customer(self):
        trip = _make_trip(
            self.customer, captain=self.captain, status=Trip.Status.ACCEPTED
        )
        cancel_trip(
            trip_id=trip.pk,
            cancelled_by=self.captain_user,
            reason="Vehicle issue",
        )

        customer_notifications = get_user_notifications(user=self.customer_user)
        self.assertEqual(len(customer_notifications), 1)
        self.assertEqual(
            customer_notifications[0].payload["status"], Trip.Status.CANCELLED
        )

        captain_notifications = get_user_notifications(user=self.captain_user)
        self.assertEqual(len(captain_notifications), 0)

    # --- Review submission ---

    def test_review_submission_notifies_captain(self):
        trip = _make_trip(
            self.customer,
            captain=self.captain,
            status=Trip.Status.COMPLETED,
        )
        trip.final_fare = Decimal("100.00")
        trip.save()
        create_trip_review(
            trip=trip,
            customer=self.customer,
            rating=5,
            comment="رحلة ممتازة",
        )

        notifications = get_user_notifications(user=self.captain_user)
        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0].type, "REVIEW_RECEIVED")
        self.assertEqual(notifications[0].title, "تقييم جديد")
        self.assertEqual(notifications[0].payload["rating"], 5)

    def test_review_submission_no_notification_to_customer(self):
        trip = _make_trip(
            self.customer,
            captain=self.captain,
            status=Trip.Status.COMPLETED,
        )
        trip.final_fare = Decimal("100.00")
        trip.save()
        create_trip_review(
            trip=trip,
            customer=self.customer,
            rating=5,
            comment="رحلة ممتازة",
        )

        notifications = get_user_notifications(user=self.customer_user)
        self.assertEqual(len(notifications), 0)

    # --- Rollback safety ---

    def test_validation_error_rolls_back_notification(self):
        trip = _make_trip(
            self.customer,
            captain=self.captain,
            status=Trip.Status.COMPLETED,
        )
        trip.final_fare = Decimal("100.00")
        trip.save()

        create_trip_review(
            trip=trip,
            customer=self.customer,
            rating=5,
            comment="First review",
        )

        with self.assertRaises(Exception):
            create_trip_review(
                trip=trip,
                customer=self.customer,
                rating=3,
                comment="Duplicate review",
            )

        notifications = get_user_notifications(user=self.captain_user)
        self.assertEqual(len(notifications), 1)
        self.assertEqual(notifications[0].payload["rating"], 5)

    # --- Per-user isolation ---

    def test_notifications_do_not_leak_between_users(self):
        trip = _make_trip(self.customer)
        create_captain_offer(
            trip_id=trip.pk, captain=self.captain, amount=Decimal("80.00")
        )

        customer_notifications = get_user_notifications(
            user=self.customer_user
        )
        self.assertEqual(len(customer_notifications), 1)

        captain_notifications = get_user_notifications(
            user=self.captain_user
        )
        self.assertEqual(len(captain_notifications), 0)

    # --- WebSocket dispatch ---

    def test_dispatch_notification_websocket_delivers_to_group(self):
        """Verify dispatch_notification_websocket sends to the user's WebSocket group."""
        notification = create_notification(
            user=self.customer_user,
            type=Notification.Type.OFFER_RECEIVED,
            title="Test Dispatch",
            message="Test message",
        )

        async def run():
            comm = await _WebSocketHelper.connect(self.customer_user)
            await dispatch_notification_websocket(notification)
            response = await asyncio.wait_for(
                comm.receive_json_from(), timeout=2.0
            )
            self.assertEqual(response["notification"]["title"], "Test Dispatch")
            self.assertEqual(response["notification"]["type"], "OFFER_RECEIVED")
            await comm.disconnect()

        asyncio.run(run())
