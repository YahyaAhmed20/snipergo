from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from rest_framework.test import APIClient

from django.core.exceptions import ValidationError
from django.db import transaction
from django.test import TestCase
from django.utils import timezone

from accounts.models import (
    CaptainDocument,
    CaptainProfile,
    CaptainWallet,
    CustomerProfile,
    User,
)
from accounts.services.reviews import (
    approve_captain,
    approve_document,
    approve_vehicle,
    set_captain_online_status,
)
from trips.models import CaptainOffer, FinancialTransaction, Trip, TripPricingConfig
from trips.services.offers import (
    accept_captain_offer,
    create_captain_offer,
)

from trips.models import TripFinancialRecord
from accounts.models import CaptainWallet
from vehicles.models import Vehicle
from trips.services.lifecycle import (
    accept_trip,
    mark_captain_arriving,
    mark_captain_arrived,
    start_trip,
    complete_trip,
    cancel_trip,
)


class AcceptTripTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from decimal import Decimal
        from trips.models import CommissionConfig

        CommissionConfig.objects.create(
            name="default",
            percentage=Decimal("15.00"),
            is_active=True,
        )

    def setUp(self):
        self.reviewer = User.objects.create_superuser(
            username="trip_admin",
            email="trip-admin@example.com",
            password="TestPassword-123!",
        )

        customer_user = User.objects.create_user(
            username="trip_customer",
            password="TestPassword-123!",
            role=User.Role.CUSTOMER,
        )
        self.customer = CustomerProfile.objects.create(
            user=customer_user,
        )

        captain_user = User.objects.create_user(
            username="trip_captain",
            password="TestPassword-123!",
            role=User.Role.CAPTAIN,
        )
        self.captain = CaptainProfile.objects.create(
            user=captain_user,
        )

        self.wallet = CaptainWallet.objects.create(
            captain=self.captain,
            balance=Decimal("100.00"),
        )

        self.trip = Trip.objects.create(
            customer=self.customer,
            pickup_address="Suez Pickup",
            pickup_latitude="29.966800",
            pickup_longitude="32.549800",
            destination_address="Suez Destination",
            destination_latitude="29.973700",
            destination_longitude="32.526300",
            estimated_fare=Decimal("100.00"),
        )

    def create_required_documents(self):
        for document_type in (
            CaptainDocument.DocumentType.NATIONAL_ID,
            CaptainDocument.DocumentType.DRIVING_LICENSE,
            CaptainDocument.DocumentType.VEHICLE_LICENSE,
        ):
            document = CaptainDocument.objects.create(
                captain=self.captain,
                document_type=document_type,
                file="private/test-document.pdf",
            )
            approve_document(document, self.reviewer)

    def create_approved_vehicle(self):
        vehicle = Vehicle.objects.create(
            captain=self.captain,
            vehicle_type=Vehicle.VehicleType.SCOOTER,
            brand="Test Brand",
            model_name="Test Model",
            manufacture_year=2025,
            plate_number="TRIP-TEST-001",
            chassis_number="TRIP-CHASSIS-001",
            front_photo="vehicles/front/test.jpg",
            rear_photo="vehicles/rear/test.jpg",
            side_photo="vehicles/side/test.jpg",
            plate_photo="vehicles/plate/test.jpg",
        )
        approve_vehicle(vehicle, self.reviewer)
        return vehicle


    def prepare_online_captain(self):
        self.create_required_documents()
        approve_captain(self.captain, self.reviewer)
        vehicle = self.create_approved_vehicle()

        self.captain = set_captain_online_status(
            self.captain,
            True,
        )

        return vehicle


    def test_online_approved_captain_can_accept_trip(self):
        vehicle = self.prepare_online_captain()

        accepted_trip = accept_trip(
            trip_id=self.trip.pk,
            captain=self.captain,
        )

        self.assertEqual(
            accepted_trip.status,
            Trip.Status.ACCEPTED,
        )
        self.assertEqual(accepted_trip.captain, self.captain)
        self.assertEqual(accepted_trip.vehicle, vehicle)
        self.assertIsNotNone(accepted_trip.accepted_at)
        self.assertEqual(
            accepted_trip.commission_percentage,
            Decimal("15.00"),
        )




    def test_captain_with_insufficient_balance_cannot_accept_trip(self):
        self.prepare_online_captain()

        wallet = CaptainWallet.objects.get(captain=self.captain)
        wallet.balance = Decimal("5.00")
        wallet.reserved_balance = Decimal("0.00")
        wallet.save(update_fields=["balance", "reserved_balance"])

        with self.assertRaises(ValidationError):
            accept_trip(
                trip_id=self.trip.pk,
                captain=self.captain,
            )

        self.trip.refresh_from_db()
        wallet.refresh_from_db()

        self.assertEqual(self.trip.status, Trip.Status.REQUESTED)
        self.assertIsNone(self.trip.captain_id)
        self.assertIsNone(self.trip.vehicle_id)

        self.assertEqual(wallet.balance, Decimal("5.00"))
        self.assertEqual(wallet.reserved_balance, Decimal("0.00"))

        self.assertFalse(
            FinancialTransaction.objects.filter(
                reference=f"trip-commission-reserve:{self.trip.pk}"
            ).exists()
        )

    def test_unapproved_captain_cannot_accept_trip(self):
        with self.assertRaises(ValidationError):
            accept_trip(
                trip_id=self.trip.pk,
                captain=self.captain,
            )

        self.trip.refresh_from_db()
        self.assertEqual(
            self.trip.status,
            Trip.Status.REQUESTED,
        )
        self.assertIsNone(self.trip.captain)

    def test_offline_captain_cannot_accept_trip(self):
        self.create_required_documents()
        approve_captain(self.captain, self.reviewer)
        self.create_approved_vehicle()

        with self.assertRaises(ValidationError):
            accept_trip(
                trip_id=self.trip.pk,
                captain=self.captain,
            )

        self.trip.refresh_from_db()
        self.assertEqual(
            self.trip.status,
            Trip.Status.REQUESTED,
        )

    def test_trip_cannot_be_accepted_twice(self):
        self.prepare_online_captain()

        accept_trip(
            trip_id=self.trip.pk,
            captain=self.captain,
        )

        with self.assertRaises(ValidationError):
            accept_trip(
                trip_id=self.trip.pk,
                captain=self.captain,
            )

        self.trip.refresh_from_db()
        self.assertEqual(
            self.trip.status,
            Trip.Status.ACCEPTED,
        )
        self.assertEqual(self.trip.captain, self.captain)



    def test_trip_can_complete_full_lifecycle(self):
        self.prepare_online_captain()

        trip = accept_trip(
            trip_id=self.trip.pk,
            captain=self.captain,
        )
        self.assertEqual(trip.status, Trip.Status.ACCEPTED)

        trip = mark_captain_arriving(
            trip_id=trip.pk,
            captain=self.captain,
        )
        self.assertEqual(trip.status, Trip.Status.ARRIVING)

        trip = mark_captain_arrived(
            trip_id=trip.pk,
            captain=self.captain,
        )
        self.assertEqual(trip.status, Trip.Status.ARRIVED)

        trip = start_trip(
            trip_id=trip.pk,
            captain=self.captain,
        )
        self.assertEqual(trip.status, Trip.Status.IN_PROGRESS)
        self.assertIsNotNone(trip.started_at)

        trip.agreed_fare = Decimal("100.00")
        trip.save(update_fields=["agreed_fare"])

        trip = complete_trip(
            trip_id=trip.pk,
            captain=self.captain,
        )
        self.assertEqual(trip.status, Trip.Status.COMPLETED)
        self.assertEqual(trip.final_fare, Decimal("100.00"))
        self.assertIsNotNone(trip.completed_at)


    # ===== Rollback when financial record creation fails =====

    @patch(
        "trips.services.lifecycle.create_trip_financial_record",
        side_effect=ValidationError("Simulated financial failure"),
    )
    def test_financial_record_failure_rolls_back_trip_completion(
        self,
        mocked_financial_record,
    ):
        self.prepare_online_captain()

        trip = accept_trip(
            trip_id=self.trip.pk,
            captain=self.captain,
        )
        trip = mark_captain_arriving(
            trip_id=trip.pk,
            captain=self.captain,
        )
        trip = mark_captain_arrived(
            trip_id=trip.pk,
            captain=self.captain,
        )
        trip = start_trip(
            trip_id=trip.pk,
            captain=self.captain,
        )

        trip.agreed_fare = Decimal("100.00")
        trip.save(update_fields=["agreed_fare"])

        with self.assertRaises(ValidationError):
            complete_trip(
                trip_id=trip.pk,
                captain=self.captain,

            )

        trip.refresh_from_db()

        self.assertEqual(trip.status, Trip.Status.IN_PROGRESS)
        self.assertIsNone(trip.completed_at)
        self.assertIsNone(trip.final_fare)

        mocked_financial_record.assert_called_once_with(
            trip_id=trip.pk,
        )


        # ===== Rollback when commission deduction fails =====
    @patch(
        "trips.services.lifecycle.deduct_trip_commission",
        side_effect=ValidationError("Simulated commission deduction failure"),
    )
    def test_commission_deduction_failure_rolls_back_trip_completion(
        self,
        mocked_deduction,
    ):
        self.prepare_online_captain()

        trip = accept_trip(
            trip_id=self.trip.pk,
            captain=self.captain,
        )
        trip = mark_captain_arriving(
            trip_id=trip.pk,
            captain=self.captain,
        )
        trip = mark_captain_arrived(
            trip_id=trip.pk,
            captain=self.captain,
        )
        trip = start_trip(
            trip_id=trip.pk,
            captain=self.captain,
        )

        trip.agreed_fare = Decimal("100.00")
        trip.save(update_fields=["agreed_fare"])

        # Snapshot wallet balances before the operation

        wallet = CaptainWallet.objects.get(captain=self.captain)
        balance_before = wallet.balance
        reserved_before = wallet.reserved_balance

        with self.assertRaises(ValidationError):
            complete_trip(
                trip_id=trip.pk,
                captain=self.captain,

            )

        trip.refresh_from_db()

        self.assertEqual(trip.status, Trip.Status.IN_PROGRESS)
        self.assertIsNone(trip.completed_at)
        self.assertIsNone(trip.final_fare)

        # Verify financial record rollback and unchanged wallet balances

        self.assertFalse(
            TripFinancialRecord.objects.filter(trip=trip).exists()
        )

        wallet.refresh_from_db()
        self.assertEqual(wallet.balance, balance_before)
        self.assertEqual(wallet.reserved_balance, reserved_before)

        mocked_deduction.assert_called_once()






    def test_completion_uses_commission_percentage_saved_at_acceptance(self):
        from trips.models import CommissionConfig

        self.prepare_online_captain()

        trip = accept_trip(
            trip_id=self.trip.pk,
            captain=self.captain,
        )

        self.assertEqual(
            trip.commission_percentage,
            Decimal("15.00"),
        )

        CommissionConfig.objects.filter(is_active=True).update(
            percentage=Decimal("20.00")
        )

        mark_captain_arriving(trip_id=trip.pk, captain=self.captain)
        mark_captain_arrived(trip_id=trip.pk, captain=self.captain)
        start_trip(trip_id=trip.pk, captain=self.captain)

        trip.agreed_fare = Decimal("100.00")
        trip.save(update_fields=["agreed_fare"])

        complete_trip(trip_id=trip.pk, captain=self.captain)

        record = TripFinancialRecord.objects.get(trip=trip)

        self.assertEqual(record.commission_percentage, Decimal("15.00"))
        self.assertEqual(record.commission_amount, Decimal("15.00"))
        self.assertEqual(record.captain_earnings, Decimal("85.00"))





    def test_insufficient_balance_rolls_back_trip_completion(self):
        self.prepare_online_captain()

        trip = accept_trip(
            trip_id=self.trip.pk,
            captain=self.captain,
        )
        trip = mark_captain_arriving(
            trip_id=trip.pk,
            captain=self.captain,
        )
        trip = mark_captain_arrived(
            trip_id=trip.pk,
            captain=self.captain,
        )
        trip = start_trip(
            trip_id=trip.pk,
            captain=self.captain,
        )

        trip.agreed_fare = Decimal("200.00")
        trip.save(update_fields=["agreed_fare"])

        wallet = CaptainWallet.objects.get(captain=self.captain)
        wallet.balance = wallet.reserved_balance
        wallet.save(update_fields=["balance", "updated_at"])

        with self.assertRaises(ValidationError):
            complete_trip(
                trip_id=trip.pk,
                captain=self.captain,
            )

        trip.refresh_from_db()
        wallet.refresh_from_db()

        self.assertEqual(trip.status, Trip.Status.IN_PROGRESS)
        self.assertIsNone(trip.completed_at)
        self.assertIsNone(trip.final_fare)

        self.assertEqual(wallet.balance, Decimal("15.00"))
        self.assertEqual(wallet.reserved_balance, Decimal("15.00"))

        self.assertFalse(
            TripFinancialRecord.objects.filter(trip=trip).exists()
        )

        self.assertFalse(
            FinancialTransaction.objects.filter(
                reference=f"trip-commission:{trip.pk}",
            ).exists()
        )


    def test_trip_cannot_be_completed_before_start(self):
        self.prepare_online_captain()

        accept_trip(
            trip_id=self.trip.pk,
            captain=self.captain,
        )

        with self.assertRaises(ValidationError):
            complete_trip(
                trip_id=self.trip.pk,
                captain=self.captain,
            )

        self.trip.refresh_from_db()
        self.assertEqual(
            self.trip.status,
            Trip.Status.ACCEPTED,
        )
        self.assertIsNone(self.trip.final_fare)

    def test_another_captain_cannot_change_trip_status(self):
        self.prepare_online_captain()

        trip = accept_trip(
            trip_id=self.trip.pk,
            captain=self.captain,
        )

        other_user = User.objects.create_user(
            username="another_captain",
            password="TestPassword-123!",
            role=User.Role.CAPTAIN,
        )
        other_captain = CaptainProfile.objects.create(
            user=other_user,
        )

        with self.assertRaises(ValidationError):
            mark_captain_arriving(
                trip_id=trip.pk,
                captain=other_captain,
            )

        trip.refresh_from_db()
        self.assertEqual(trip.status, Trip.Status.ACCEPTED)
        self.assertEqual(trip.captain_id, self.captain.pk)



    @transaction.atomic
    def cancel_trip(*, trip_id, cancelled_by, reason):
        trip = Trip.objects.select_for_update().get(pk=trip_id)

        reason = reason.strip()
        if not reason:
            raise ValidationError("A cancellation reason is required.")

        if trip.status in (
            Trip.Status.COMPLETED,
            Trip.Status.CANCELLED,
            Trip.Status.EXPIRED,
        ):
            raise ValidationError(
                "This trip can no longer be cancelled."
            )

        is_customer = trip.customer.user_id == cancelled_by.pk
        is_captain = (
            trip.captain is not None
            and trip.captain.user_id == cancelled_by.pk
        )
        is_support = (
            cancelled_by.is_staff
            or cancelled_by.is_superuser
            or getattr(cancelled_by, "role", None) == "ADMIN"
        )

        if trip.status == Trip.Status.IN_PROGRESS:
            if not is_support:
                raise ValidationError(
                    "Only support can cancel a trip after it starts."
                )
        elif not (is_customer or is_captain or is_support):
            raise ValidationError(
                "You are not allowed to cancel this trip."
            )

        trip.status = Trip.Status.CANCELLED
        trip.cancelled_at = timezone.now()
        trip.cancelled_by = cancelled_by
        trip.cancellation_reason = reason

        trip.save(
            update_fields=[
                "status",
                "cancelled_at",
                "cancelled_by",
                "cancellation_reason",
                "updated_at",
            ]
        )
        return trip



    def test_customer_can_cancel_before_trip_starts(self):
        self.prepare_online_captain()
        accept_trip(
            trip_id=self.trip.pk,
            captain=self.captain,
        )

        cancelled_trip = cancel_trip(
            trip_id=self.trip.pk,
            cancelled_by=self.customer.user,
            reason="Customer changed plans",
        )

        self.assertEqual(
            cancelled_trip.status,
            Trip.Status.CANCELLED,
        )
        self.assertEqual(
            cancelled_trip.cancelled_by,
            self.customer.user,
        )
        self.assertTrue(cancelled_trip.cancelled_at)
        self.assertEqual(
            cancelled_trip.cancellation_reason,
            "Customer changed plans",
        )

    def test_captain_can_cancel_before_trip_starts(self):
        self.prepare_online_captain()
        accept_trip(
            trip_id=self.trip.pk,
            captain=self.captain,
        )

        cancelled_trip = cancel_trip(
            trip_id=self.trip.pk,
            cancelled_by=self.captain.user,
            reason="Vehicle issue",
        )

        self.assertEqual(
            cancelled_trip.status,
            Trip.Status.CANCELLED,
        )

    def test_customer_cannot_cancel_trip_in_progress(self):
        self.prepare_online_captain()
        accept_trip(
            trip_id=self.trip.pk,
            captain=self.captain,
        )
        mark_captain_arriving(
            trip_id=self.trip.pk,
            captain=self.captain,
        )
        mark_captain_arrived(
            trip_id=self.trip.pk,
            captain=self.captain,
        )
        start_trip(
            trip_id=self.trip.pk,
            captain=self.captain,
        )

        with self.assertRaises(ValidationError):
            cancel_trip(
                trip_id=self.trip.pk,
                cancelled_by=self.customer.user,
                reason="Changed plans",
            )

        self.trip.refresh_from_db()
        self.assertEqual(
            self.trip.status,
            Trip.Status.IN_PROGRESS,
        )

    def test_completed_trip_cannot_be_cancelled(self):
        self.prepare_online_captain()
        accept_trip(
            trip_id=self.trip.pk,
            captain=self.captain,
        )
        mark_captain_arriving(
            trip_id=self.trip.pk,
            captain=self.captain,
        )
        mark_captain_arrived(
            trip_id=self.trip.pk,
            captain=self.captain,
        )
        start_trip(
            trip_id=self.trip.pk,
            captain=self.captain,
        )

        self.trip.refresh_from_db()
        self.trip.agreed_fare = Decimal("100.00")
        self.trip.save(update_fields=["agreed_fare"])

        complete_trip(
            trip_id=self.trip.pk,
            captain=self.captain,
        )

        with self.assertRaises(ValidationError):
            cancel_trip(
                trip_id=self.trip.pk,
                cancelled_by=self.customer.user,
                reason="Too late",
            )

        self.trip.refresh_from_db()
        self.assertEqual(
            self.trip.status,
            Trip.Status.COMPLETED,
        )



    def test_support_can_cancel_trip_in_progress(self):
        self.prepare_online_captain()
        accept_trip(
            trip_id=self.trip.pk,
            captain=self.captain,
        )
        mark_captain_arriving(
            trip_id=self.trip.pk,
            captain=self.captain,
        )
        mark_captain_arrived(
            trip_id=self.trip.pk,
            captain=self.captain,
        )
        start_trip(
            trip_id=self.trip.pk,
            captain=self.captain,
        )

        cancelled_trip = cancel_trip(
            trip_id=self.trip.pk,
            cancelled_by=self.reviewer,
            reason="Emergency support cancellation",
        )

        self.assertEqual(
            cancelled_trip.status,
            Trip.Status.CANCELLED,
        )
        self.assertEqual(
            cancelled_trip.cancelled_by,
            self.reviewer,
        )

    def test_unrelated_user_cannot_cancel_trip(self):
        self.prepare_online_captain()
        accept_trip(
            trip_id=self.trip.pk,
            captain=self.captain,
        )

        unrelated_user = User.objects.create_user(
            username="unrelated_customer",
            password="TestPassword-123!",
            role=User.Role.CUSTOMER,
        )

        with self.assertRaises(ValidationError):
            cancel_trip(
                trip_id=self.trip.pk,
                cancelled_by=unrelated_user,
                reason="Unauthorized cancellation",
            )

        self.trip.refresh_from_db()
        self.assertEqual(
            self.trip.status,
            Trip.Status.ACCEPTED,
        )


class TripAPITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from decimal import Decimal
        from trips.models import CommissionConfig

        CommissionConfig.objects.create(
            name="default",
            percentage=Decimal("15.00"),
            is_active=True,
        )

    def setUp(self):
        self.api_client = APIClient()

        self.customer_user = User.objects.create_user(
            username="api_customer",
            password="test-password-123",
            role=User.Role.CUSTOMER,
        )
        self.customer = CustomerProfile.objects.create(
            user=self.customer_user,
            phone_number="01000000001",
        )

    def test_anonymous_user_cannot_create_trip(self):
        response = self.api_client.post(
            "/api/trips/create/",
            {},
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    @patch(
        "trips.serializers.calculate_road_distance_km",
        return_value=Decimal("2.8944"),
    )
    def test_customer_can_create_trip(self, mocked_road_distance):
        TripPricingConfig.objects.create(
            city="Suez",
            base_fare=Decimal("10.00"),
            price_per_km=Decimal("5.00"),
            minimum_fare=Decimal("15.00"),
            maximum_fare=Decimal("100.00"),
            is_active=True,
        )

        self.api_client.force_authenticate(user=self.customer_user)

        response = self.api_client.post(
            "/api/trips/create/",
            {
                "pickup_address": "Suez",
                "pickup_latitude": "29.9668",
                "pickup_longitude": "32.5498",
                "destination_address": "Port Tawfik",
                "destination_latitude": "29.9500",
                "destination_longitude": "32.5700",
                "payment_method": "CASH",
                "estimated_fare": "1.00",
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            201,
            msg=f"API response: {response.data}",
        )

        mocked_road_distance.assert_called_once_with(
            pickup_latitude=Decimal("29.9668"),
            pickup_longitude=Decimal("32.5498"),
            destination_latitude=Decimal("29.9500"),
            destination_longitude=Decimal("32.5700"),
        )

        created_trip = Trip.objects.get(pk=response.data["id"])

        self.assertEqual(created_trip.estimated_fare, Decimal("24.47"))

        self.assertEqual(
            created_trip.estimated_fare,
            Decimal(response.data["estimated_fare"]),
        )
        self.assertNotEqual(
            created_trip.estimated_fare,
            Decimal("1.00"),
        )

        self.assertEqual(created_trip.status, "REQUESTED")
        self.assertEqual(created_trip.customer, self.customer)

        self.assertEqual(
            Trip.objects.filter(customer=self.customer).count(),
            1,
        )





    @patch(
        "trips.serializers.calculate_road_distance_km",
        side_effect=ValidationError(
            "Road routing is temporarily unavailable. Please try again."
        ),
    )
    def test_customer_cannot_create_trip_when_routing_fails(
        self,
        mocked_road_distance,
    ):
        TripPricingConfig.objects.create(
            city="Suez",
            base_fare=Decimal("10.00"),
            price_per_km=Decimal("5.00"),
            minimum_fare=Decimal("15.00"),
            maximum_fare=Decimal("100.00"),
            is_active=True,
        )

        self.api_client.force_authenticate(user=self.customer_user)

        response = self.api_client.post(
            "/api/trips/create/",
            {
                "pickup_address": "Suez",
                "pickup_latitude": "29.9668",
                "pickup_longitude": "32.5498",
                "destination_address": "Port Tawfik",
                "destination_latitude": "29.9500",
                "destination_longitude": "32.5700",
                "payment_method": "CASH",
            },
            format="json",
        )

        self.assertEqual(
            response.status_code,
            400,
            msg=f"API response: {response.data}",
        )
        mocked_road_distance.assert_called_once()
        self.assertEqual(
            Trip.objects.filter(customer=self.customer).count(),
            0,
        )
    def test_customer_cannot_view_another_customer_trip(self):
        other_user = User.objects.create_user(
            username="other_customer",
            password="test-password-123",
            role=User.Role.CUSTOMER,
        )
        other_customer = CustomerProfile.objects.create(
            user=other_user,
            phone_number="01000000000",
        )
        other_trip = Trip.objects.create(
            customer=other_customer,
            pickup_address="Suez",
            pickup_latitude="29.9668",
            pickup_longitude="32.5498",
            destination_address="Port Tawfik",
            destination_latitude="29.9500",
            destination_longitude="32.5700",
        )

        self.api_client.force_authenticate(user=self.customer_user)
        response = self.api_client.get(
            f"/api/trips/{other_trip.pk}/"
        )

        self.assertEqual(response.status_code, 403)


    def test_customer_can_view_own_trip(self):
        trip = Trip.objects.create(
            customer=self.customer,
            pickup_address="Suez",
            pickup_latitude="29.9668",
            pickup_longitude="32.5498",
            destination_address="Port Tawfik",
            destination_latitude="29.9500",
            destination_longitude="32.5700",
        )

        self.api_client.force_authenticate(user=self.customer_user)

        response = self.api_client.get(f"/api/trips/{trip.pk}/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "REQUESTED")


    def test_unrelated_user_cannot_view_trip(self):
        trip = Trip.objects.create(
            customer=self.customer,
            pickup_address="Suez",
            pickup_latitude="29.9668",
            pickup_longitude="32.5498",
            destination_address="Port Tawfik",
            destination_latitude="29.9500",
            destination_longitude="32.5700",
        )

        unrelated_user = User.objects.create_user(
            username="unrelated_api_user",
            password="test-password-123",
            role=User.Role.CUSTOMER,
        )
        CustomerProfile.objects.create(
            user=unrelated_user,
            phone_number="01000000002",
        )

        self.api_client.force_authenticate(user=unrelated_user)

        response = self.api_client.get(f"/api/trips/{trip.pk}/")

        self.assertEqual(response.status_code, 403)


    def create_approved_captain(self, username="api_captain"):
        from accounts.models import CaptainDocument
        from accounts.services.reviews import (
            approve_captain,
            approve_document,
            approve_vehicle,
            set_captain_online_status,
        )
        from vehicles.models import Vehicle

        reviewer = User.objects.create_superuser(
            username=f"{username}_reviewer",
            password="test-password-123",
            email=f"{username}@example.com",
        )

        captain_user = User.objects.create_user(
            username=username,
            password="test-password-123",
            role=User.Role.CAPTAIN,
        )

        captain = CaptainProfile.objects.create(
            user=captain_user,
            phone_number="01000000003",
        )

        required_documents = {
            CaptainDocument.DocumentType.NATIONAL_ID,
            CaptainDocument.DocumentType.DRIVING_LICENSE,
            CaptainDocument.DocumentType.VEHICLE_LICENSE,
        }

        for document_type in required_documents:
            document = CaptainDocument.objects.create(
                captain=captain,
                document_type=document_type,
                file=f"test_{document_type}.pdf",
            )
            approve_document(document, reviewer)

        approve_captain(captain, reviewer)

        vehicle = Vehicle.objects.create(
            captain=captain,
            vehicle_type=Vehicle.VehicleType.MOTORCYCLE,
            manufacture_year=2024,
            chassis_number=f"TEST-{username}",
            plate_number=f"PLATE-{captain_user.pk}",
            approval_status=Vehicle.ApprovalStatus.PENDING,
        )
        approve_vehicle(vehicle, reviewer)

        CaptainWallet.objects.get_or_create(
            captain=captain,
            defaults={"balance": Decimal("100.00")},
        )

        captain = set_captain_online_status(captain, True)

        return captain_user, captain


    def test_approved_online_captain_can_accept_trip(self):
        captain_user, captain = self.create_approved_captain()

        trip = Trip.objects.create(
            customer=self.customer,
            pickup_address="Suez",
            pickup_latitude="29.9668",
            pickup_longitude="32.5498",
            destination_address="Port Tawfik",
            destination_latitude="29.9500",
            destination_longitude="32.5700",
            estimated_fare=Decimal("100.00"),
        )

        self.api_client.force_authenticate(user=captain_user)
        response = self.api_client.post(
            f"/api/trips/{trip.pk}/actions/accept/",
            {},
            format="json",
        )

        self.assertEqual(
            response.status_code,
            200,
            msg=f"API response: {response.data}",
        )

        trip.refresh_from_db()
        self.assertEqual(trip.status, "ACCEPTED")
        self.assertEqual(trip.captain, captain)


    def test_offline_captain_cannot_accept_trip(self):
        from accounts.services.reviews import set_captain_online_status

        captain_user, captain = self.create_approved_captain(
            username="offline_api_captain"
        )

        set_captain_online_status(captain, False)

        trip = Trip.objects.create(
            customer=self.customer,
            pickup_address="Suez",
            pickup_latitude="29.9668",
            pickup_longitude="32.5498",
            destination_address="Port Tawfik",
            destination_latitude="29.9500",
            destination_longitude="32.5700",
        )

        self.api_client.force_authenticate(user=captain_user)
        response = self.api_client.post(
            f"/api/trips/{trip.pk}/actions/accept/",
            {},
            format="json",
        )

        self.assertEqual(
            response.status_code,
            400,
            msg=f"API response: {response.data}",
        )

        trip.refresh_from_db()
        self.assertEqual(trip.status, "REQUESTED")
        self.assertIsNone(trip.captain_id)


    def test_captain_can_complete_trip_lifecycle_through_api(self):
        captain_user, captain = self.create_approved_captain(
            username="lifecycle_api_captain"
        )

        trip = Trip.objects.create(
            customer=self.customer,
            pickup_address="Suez",
            pickup_latitude="29.9668",
            pickup_longitude="32.5498",
            destination_address="Port Tawfik",
            destination_latitude="29.9500",
            destination_longitude="32.5700",
            estimated_fare=Decimal("100.00"),
            agreed_fare=Decimal("75.00"),
        )

        self.api_client.force_authenticate(user=captain_user)

        actions = [
            ("accept", {}, "ACCEPTED"),
            ("arriving", {}, "ARRIVING"),
            ("arrived", {}, "ARRIVED"),
            ("start", {}, "IN_PROGRESS"),
            ("complete", {}, "COMPLETED"),
        ]

        for action, payload, expected_status in actions:
            response = self.api_client.post(
                f"/api/trips/{trip.pk}/actions/{action}/",
                payload,
                format="json",
            )

            self.assertEqual(
                response.status_code,
                200,
                msg=(
                    f"Action '{action}' failed: "
                    f"{response.data}"
                ),
            )

            trip.refresh_from_db()
            self.assertEqual(trip.status, expected_status)

        self.assertEqual(str(trip.final_fare), "75.00")
        self.assertIsNotNone(trip.accepted_at)
        self.assertEqual(
            trip.commission_percentage,
            Decimal("15.00"),
        )
        self.assertIsNotNone(trip.started_at)
        self.assertIsNotNone(trip.completed_at)


    def test_complete_trip_ignores_invalid_client_final_fare(self):
        captain_user, captain = self.create_approved_captain(
            username="invalid_fare_captain"
        )
        self.api_client.force_authenticate(user=captain_user)

        for index, invalid_fare in enumerate([
            "abc",
            "-10",
            "NaN",
            "Infinity",
            "12.345",
            "100000000",
        ]):
            with self.subTest(final_fare=invalid_fare):
                trip = Trip.objects.create(
                    customer=self.customer,
                    captain=captain,
                    status=Trip.Status.IN_PROGRESS,
                    pickup_address="Suez",
                    pickup_latitude="29.9668",
                    pickup_longitude="32.5498",
                    destination_address="Port Tawfik",
                    destination_latitude="29.9500",
                    destination_longitude="32.5700",
                    agreed_fare=Decimal("12.34"),
                    commission_percentage=Decimal("15.00"),
                )

                response = self.api_client.post(
                    f"/api/trips/{trip.pk}/actions/complete/",
                    {"final_fare": invalid_fare},
                    format="json",
                )

                self.assertEqual(
                    response.status_code,
                    200,
                    msg=f"Fare {invalid_fare}: {response.data}",
                )

                trip.refresh_from_db()
                self.assertEqual(trip.status, Trip.Status.COMPLETED)
                self.assertEqual(trip.final_fare, Decimal("12.34"))




    def test_complete_trip_accepts_extra_trailing_decimal_zero(self):
        captain_user, captain = self.create_approved_captain(
            username="trailing_zero_fare_captain"
        )

        trip = Trip.objects.create(
            customer=self.customer,
            captain=captain,
            status=Trip.Status.IN_PROGRESS,
            pickup_address="Suez",
            pickup_latitude="29.9668",
            pickup_longitude="32.5498",
            destination_address="Port Tawfik",
            destination_latitude="29.9500",
            destination_longitude="32.5700",
            agreed_fare=Decimal("12.34"),
            commission_percentage=Decimal("15.00"),
        )

        self.api_client.force_authenticate(user=captain_user)

        response = self.api_client.post(
            f"/api/trips/{trip.pk}/actions/complete/",
            {"final_fare": "99.99"},
            format="json",
        )

        self.assertEqual(response.status_code, 200, msg=str(response.data))

        trip.refresh_from_db()
        self.assertEqual(trip.status, Trip.Status.COMPLETED)
        self.assertEqual(trip.final_fare, Decimal("12.34"))

    def test_complete_trip_requires_agreed_fare(self):
        captain_user, captain = self.create_approved_captain(
            username="missing_fare_captain"
        )

        trip = Trip.objects.create(
            customer=self.customer,
            captain=captain,
            status=Trip.Status.IN_PROGRESS,
            pickup_address="Suez",
            pickup_latitude="29.9668",
            pickup_longitude="32.5498",
            destination_address="Port Tawfik",
            destination_latitude="29.9500",
            destination_longitude="32.5700",
        )

        self.api_client.force_authenticate(user=captain_user)

        response = self.api_client.post(
            f"/api/trips/{trip.pk}/actions/complete/",
            {},
            format="json",
        )

        self.assertEqual(response.status_code, 400)

        trip.refresh_from_db()
        self.assertEqual(trip.status, "IN_PROGRESS")


    def test_online_captain_sees_only_requested_trips(self):
        captain_user, captain = self.create_approved_captain(
            username="available_trips_captain"
        )

        captain.refresh_from_db()

        captain.current_latitude = Decimal("29.9668")
        captain.current_longitude = Decimal("32.5498")
        captain.location_updated_at = timezone.now()
        captain.save(
            update_fields=[
                "current_latitude",
                "current_longitude",
                "location_updated_at",
            ]
        )

        requested_trip = Trip.objects.create(
            customer=self.customer,
            pickup_address="Suez",
            pickup_latitude="29.9668",
            pickup_longitude="32.5498",
            destination_address="Port Tawfik",
            destination_latitude="29.9500",
            destination_longitude="32.5700",
        )

        accepted_trip = Trip.objects.create(
            customer=self.customer,
            captain=captain,
            status=Trip.Status.ACCEPTED,
            pickup_address="Suez",
            pickup_latitude="29.9668",
            pickup_longitude="32.5498",
            destination_address="Port Tawfik",
            destination_latitude="29.9500",
            destination_longitude="32.5700",
        )

        self.api_client.force_authenticate(user=captain_user)
        response = self.api_client.get("/api/trips/available/")

        self.assertEqual(
            response.status_code,
            200,
            msg=f"API response: {response.data}",
        )
        self.assertEqual(
            [item["id"] for item in response.data],
            [requested_trip.pk],
        )
        self.assertNotIn(
            accepted_trip.pk,
            [item["id"] for item in response.data],
        )


    def test_available_trips_are_sorted_by_pickup_distance(self):
        captain_user, captain = self.create_approved_captain(
            username="distance_order_captain"
        )
        captain.current_latitude = Decimal("29.9668")
        captain.current_longitude = Decimal("32.5498")
        captain.location_updated_at = timezone.now()
        captain.save(
            update_fields=[
                "current_latitude",
                "current_longitude",
                "location_updated_at",
            ]
        )

        farther_trip = Trip.objects.create(
            customer=self.customer,
            pickup_address="Far pickup",
            pickup_latitude="29.9000",
            pickup_longitude="32.5498",
            destination_address="Destination A",
            destination_latitude="29.9500",
            destination_longitude="32.5700",
            estimated_fare=Decimal("50.00"),
        )

        closer_trip = Trip.objects.create(
            customer=self.customer,
            pickup_address="Close pickup",
            pickup_latitude="29.9660",
            pickup_longitude="32.5498",
            destination_address="Destination B",
            destination_latitude="29.9500",
            destination_longitude="32.5700",
            estimated_fare=Decimal("20.00"),
        )

        self.api_client.force_authenticate(user=captain_user)
        response = self.api_client.get("/api/trips/available/")

        self.assertEqual(response.status_code, 200, msg=str(response.data))
        self.assertEqual(
            [item["id"] for item in response.data],
            [closer_trip.pk, farther_trip.pk],
        )


    def test_captain_with_stale_location_cannot_view_available_trips(self):
        captain_user, captain = self.create_approved_captain(
            username="stale_location_captain"
        )
        captain.current_latitude = Decimal("29.9668")
        captain.current_longitude = Decimal("32.5498")
        captain.location_updated_at = timezone.now() - timedelta(minutes=3)
        captain.save(
            update_fields=[
                "current_latitude",
                "current_longitude",
                "location_updated_at",
            ]
        )

        self.api_client.force_authenticate(user=captain_user)
        response = self.api_client.get("/api/trips/available/")

        self.assertEqual(response.status_code, 403)
        self.assertIn("recent captain location", str(response.data).lower())

    def test_customer_cannot_view_available_trips(self):
        self.api_client.force_authenticate(user=self.customer_user)

        response = self.api_client.get("/api/trips/available/")

        self.assertEqual(response.status_code, 403)


    def test_offline_captain_cannot_view_available_trips(self):
        from accounts.services.reviews import set_captain_online_status

        captain_user, captain = self.create_approved_captain(
            username="offline_available_captain"
        )
        set_captain_online_status(captain, False)

        self.api_client.force_authenticate(user=captain_user)
        response = self.api_client.get("/api/trips/available/")

        self.assertEqual(response.status_code, 403)



    def test_captain_without_available_wallet_balance_cannot_view_trips(self):
        captain_user, captain = self.create_approved_captain(
            username="zero_balance_available_captain"
        )

        wallet = CaptainWallet.objects.get(captain=captain)
        wallet.balance = Decimal("0.00")
        wallet.reserved_balance = Decimal("0.00")
        wallet.save(update_fields=["balance", "reserved_balance"])

        self.api_client.force_authenticate(user=captain_user)
        response = self.api_client.get("/api/trips/available/")

        self.assertEqual(response.status_code, 403)



    def test_captain_with_fully_reserved_balance_cannot_view_trips(self):
        captain_user, captain = self.create_approved_captain(
            username="fully_reserved_available_captain"
        )

        wallet = CaptainWallet.objects.get(captain=captain)
        wallet.balance = Decimal("100.00")
        wallet.reserved_balance = Decimal("100.00")
        wallet.save(update_fields=["balance", "reserved_balance"])

        self.api_client.force_authenticate(user=captain_user)
        response = self.api_client.get("/api/trips/available/")

        self.assertEqual(response.status_code, 403)






    def test_available_trips_exposes_only_required_fields(self):
        captain_user, captain = self.create_approved_captain(
            username="privacy_api_captain"
        )

        captain.current_latitude = Decimal("29.9668")
        captain.current_longitude = Decimal("32.5498")
        captain.location_updated_at = timezone.now()
        captain.save(
            update_fields=[
                "current_latitude",
                "current_longitude",
                "location_updated_at",
            ]
        )

        Trip.objects.create(
            customer=self.customer,
            pickup_address="Suez",
            pickup_latitude="29.9668",
            pickup_longitude="32.5498",
            destination_address="Port Tawfik",
            destination_latitude="29.9500",
            destination_longitude="32.5700",
            estimated_fare="75.00",
        )

        self.api_client.force_authenticate(user=captain_user)
        response = self.api_client.get("/api/trips/available/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)

        item = response.data[0]

        expected_fields = {
            "id",
            "pickup_address",
            "pickup_latitude",
            "pickup_longitude",
            "destination_address",
            "destination_latitude",
            "destination_longitude",
            "payment_method",
            "estimated_fare",
            "requested_at",
        }

        self.assertEqual(set(item.keys()), expected_fields)
        self.assertNotIn("customer_username", item)
        self.assertNotIn("captain_username", item)
        self.assertNotIn("cancellation_reason", item)


    def test_customer_cannot_create_trip_without_active_pricing(self):
        TripPricingConfig.objects.filter(city__iexact="Suez").delete()

        self.api_client.force_authenticate(user=self.customer_user)

        response = self.api_client.post(
            "/api/trips/create/",
            {
                "pickup_address": "Suez",
                "pickup_latitude": "29.9668",
                "pickup_longitude": "32.5498",
                "destination_address": "Port Tawfik",
                "destination_latitude": "29.9500",
                "destination_longitude": "32.5700",
                "payment_method": "CASH",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            Trip.objects.filter(customer=self.customer).count(),
            0,
        )


    def test_approved_online_captain_can_create_offer(self):
        captain_user, captain = self.create_approved_captain(
            username="offer_api_captain",
        )

        TripPricingConfig.objects.create(
            city="Suez",
            base_fare=Decimal("10.00"),
            price_per_km=Decimal("5.00"),
            minimum_fare=Decimal("15.00"),
            maximum_fare=Decimal("100.00"),
            is_active=True,
        )

        trip = Trip.objects.create(
            customer=self.customer,
            pickup_address="Suez",
            pickup_latitude="29.9668",
            pickup_longitude="32.5498",
            destination_address="Port Tawfik",
            destination_latitude="29.9500",
            destination_longitude="32.5700",
            estimated_fare=Decimal("50.00"),
        )

        offer = create_captain_offer(
            trip_id=trip.pk,
            captain=captain,
            amount="45.00",
        )

        self.assertEqual(offer.trip_id, trip.pk)
        self.assertEqual(offer.captain_id, captain.pk)
        self.assertEqual(offer.amount, Decimal("45.00"))
        self.assertEqual(offer.status, "PENDING")
        self.assertGreater(offer.expires_at, timezone.now())


    def test_captain_cannot_offer_price_outside_negotiation_range(self):
        _, captain = self.create_approved_captain(
            username="offer_range_captain",
        )

        TripPricingConfig.objects.create(
            city="Suez",
            base_fare=Decimal("10.00"),
            price_per_km=Decimal("5.00"),
            minimum_fare=Decimal("15.00"),
            maximum_fare=Decimal("100.00"),
            is_active=True,
        )

        trip = Trip.objects.create(
            customer=self.customer,
            pickup_address="Suez",
            pickup_latitude="29.9668",
            pickup_longitude="32.5498",
            destination_address="Port Tawfik",
            destination_latitude="29.9500",
            destination_longitude="32.5700",
            estimated_fare=Decimal("50.00"),
        )

        with self.assertRaises(ValidationError):
            create_captain_offer(
                trip_id=trip.pk,
                captain=captain,
                amount="70.00",
            )

        self.assertEqual(CaptainOffer.objects.filter(trip=trip).count(), 0)


    def test_captain_cannot_create_duplicate_pending_offer(self):
        _, captain = self.create_approved_captain(
            username="offer_duplicate_captain",
        )

        TripPricingConfig.objects.create(
            city="Suez",
            base_fare=Decimal("10.00"),
            price_per_km=Decimal("5.00"),
            minimum_fare=Decimal("15.00"),
            maximum_fare=Decimal("100.00"),
            is_active=True,
        )

        trip = Trip.objects.create(
            customer=self.customer,
            pickup_address="Suez",
            pickup_latitude="29.9668",
            pickup_longitude="32.5498",
            destination_address="Port Tawfik",
            destination_latitude="29.9500",
            destination_longitude="32.5700",
            estimated_fare=Decimal("50.00"),
        )

        create_captain_offer(
            trip_id=trip.pk,
            captain=captain,
            amount="45.00",
        )

        with self.assertRaises(ValidationError):
            create_captain_offer(
                trip_id=trip.pk,
                captain=captain,
                amount="40.00",
            )

        self.assertEqual(CaptainOffer.objects.filter(trip=trip).count(), 1)


    def test_customer_can_accept_captain_offer(self):
        _, captain = self.create_approved_captain(
            username="accept_offer_captain",
        )

        TripPricingConfig.objects.create(
            city="Suez",
            base_fare=Decimal("10.00"),
            price_per_km=Decimal("5.00"),
            minimum_fare=Decimal("15.00"),
            maximum_fare=Decimal("100.00"),
        )

        trip = Trip.objects.create(
            customer=self.customer,
            pickup_address="Suez",
            pickup_latitude="29.9668",
            pickup_longitude="32.5498",
            destination_address="Port Tawfik",
            destination_latitude="29.9500",
            destination_longitude="32.5700",
            estimated_fare=Decimal("50.00"),
        )

        offer = create_captain_offer(
            trip_id=trip.pk,
            captain=captain,
            amount="45.00",
        )

        accepted_offer = accept_captain_offer(
            offer_id=offer.pk,
            customer=self.customer,
        )

        trip.refresh_from_db()
        offer.refresh_from_db()

        self.assertEqual(accepted_offer.status, CaptainOffer.Status.ACCEPTED)
        self.assertEqual(trip.status, Trip.Status.ACCEPTED)
        self.assertEqual(trip.captain_id, captain.pk)
        self.assertEqual(trip.agreed_fare, Decimal("45.00"))
        self.assertIsNotNone(trip.accepted_at)
        wallet = CaptainWallet.objects.get(captain=captain)
        self.assertEqual(wallet.reserved_balance, Decimal("7.50"))


    def test_expired_offer_cannot_be_accepted(self):
        _, captain = self.create_approved_captain(
            username="expired_offer_captain",
        )

        TripPricingConfig.objects.create(
            city="Suez",
            base_fare=Decimal("10.00"),
            price_per_km=Decimal("5.00"),
            minimum_fare=Decimal("15.00"),
            maximum_fare=Decimal("100.00"),
        )

        trip = Trip.objects.create(
            customer=self.customer,
            pickup_address="Suez",
            pickup_latitude="29.9668",
            pickup_longitude="32.5498",
            destination_address="Port Tawfik",
            destination_latitude="29.9500",
            destination_longitude="32.5700",
            estimated_fare=Decimal("50.00"),
        )

        offer = create_captain_offer(
            trip_id=trip.pk,
            captain=captain,
            amount="45.00",
        )

        offer.expires_at = timezone.now() - timedelta(minutes=1)
        offer.save(update_fields=["expires_at"])

        with self.assertRaises(ValidationError):
            accept_captain_offer(
                offer_id=offer.pk,
                customer=self.customer,
            )

        offer.refresh_from_db()
        trip.refresh_from_db()

        self.assertEqual(offer.status, CaptainOffer.Status.EXPIRED)
        self.assertEqual(trip.status, Trip.Status.REQUESTED)
        self.assertIsNone(trip.captain_id)


    def test_accepting_one_offer_rejects_other_pending_offers(self):
        _, captain1 = self.create_approved_captain(
            username="first_offer_captain",
        )
        _, captain2 = self.create_approved_captain(
            username="second_offer_captain",
        )

        TripPricingConfig.objects.create(
            city="Suez",
            base_fare=Decimal("10.00"),
            price_per_km=Decimal("5.00"),
            minimum_fare=Decimal("15.00"),
            maximum_fare=Decimal("100.00"),
        )

        trip = Trip.objects.create(
            customer=self.customer,
            pickup_address="Suez",
            pickup_latitude="29.9668",
            pickup_longitude="32.5498",
            destination_address="Port Tawfik",
            destination_latitude="29.9500",
            destination_longitude="32.5700",
            estimated_fare=Decimal("50.00"),
        )

        offer1 = create_captain_offer(
            trip_id=trip.pk,
            captain=captain1,
            amount="45.00",
        )
        offer2 = create_captain_offer(
            trip_id=trip.pk,
            captain=captain2,
            amount="48.00",
        )

        accept_captain_offer(
            offer_id=offer1.pk,
            customer=self.customer,
        )

        offer1.refresh_from_db()
        offer2.refresh_from_db()

        self.assertEqual(offer1.status, CaptainOffer.Status.ACCEPTED)
        self.assertEqual(offer2.status, CaptainOffer.Status.REJECTED)


    def test_customer_can_view_own_trip_offers(self):
        _, captain = self.create_approved_captain(
            username="view_own_offer_captain",
        )

        trip = Trip.objects.create(
            customer=self.customer,
            pickup_address="Suez",
            pickup_latitude="29.9668",
            pickup_longitude="32.5498",
            destination_address="Port Tawfik",
            destination_latitude="29.9500",
            destination_longitude="32.5700",
            estimated_fare=Decimal("50.00"),
        )

        offer = CaptainOffer.objects.create(
            trip=trip,
            captain=captain,
            amount=Decimal("45.00"),
            expires_at=timezone.now() + timedelta(minutes=2),
        )

        self.api_client.force_authenticate(user=self.customer_user)

        response = self.api_client.get(
            f"/api/trips/{trip.pk}/offers/list/"
        )

        self.assertEqual(
            response.status_code,
            200,
            msg=f"API response: {response.data}",
        )
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["id"], offer.pk)
        self.assertEqual(response.data[0]["captain_username"], captain.user.username)
        self.assertEqual(response.data[0]["amount"], "45.00")


    def test_another_customer_cannot_view_trip_offers(self):
        trip = Trip.objects.create(
            customer=self.customer,
            pickup_address="Suez",
            pickup_latitude="29.9668",
            pickup_longitude="32.5498",
            destination_address="Port Tawfik",
            destination_latitude="29.9500",
            destination_longitude="32.5700",
        )

        other_user = User.objects.create_user(
            username="other_offer_customer",
            password="test-password-123",
            role=User.Role.CUSTOMER,
        )
        CustomerProfile.objects.create(
            user=other_user,
            phone_number="01000000009",
        )

        self.api_client.force_authenticate(user=other_user)

        response = self.api_client.get(
            f"/api/trips/{trip.pk}/offers/list/"
        )

        self.assertEqual(response.status_code, 403)


    def test_captain_cannot_view_trip_offers(self):
        captain_user, _ = self.create_approved_captain(
            username="forbidden_offer_view_captain",
        )

        trip = Trip.objects.create(
            customer=self.customer,
            pickup_address="Suez",
            pickup_latitude="29.9668",
            pickup_longitude="32.5498",
            destination_address="Port Tawfik",
            destination_latitude="29.9500",
            destination_longitude="32.5700",
        )

        self.api_client.force_authenticate(user=captain_user)

        response = self.api_client.get(
            f"/api/trips/{trip.pk}/offers/list/"
        )

        self.assertEqual(response.status_code, 403)


    def test_customer_can_accept_offer_through_api(self):
        _, captain = self.create_approved_captain(
            username="api_accept_offer_captain",
        )

        TripPricingConfig.objects.create(
            city="Suez",
            base_fare=Decimal("10.00"),
            price_per_km=Decimal("5.00"),
            minimum_fare=Decimal("15.00"),
            maximum_fare=Decimal("100.00"),
            is_active=True,
        )

        trip = Trip.objects.create(
            customer=self.customer,
            pickup_address="Suez",
            pickup_latitude="29.9668",
            pickup_longitude="32.5498",
            destination_address="Port Tawfik",
            destination_latitude="29.9500",
            destination_longitude="32.5700",
            estimated_fare=Decimal("50.00"),
        )

        offer = create_captain_offer(
            trip_id=trip.pk,
            captain=captain,
            amount="45.00",
        )

        self.api_client.force_authenticate(user=self.customer_user)

        response = self.api_client.post(
            f"/api/trips/{trip.pk}/offers/{offer.pk}/accept/",
            {},
            format="json",
        )

        self.assertEqual(
            response.status_code,
            200,
            msg=f"API response: {response.data}",
        )

        trip.refresh_from_db()
        offer.refresh_from_db()

        self.assertEqual(trip.status, Trip.Status.ACCEPTED)
        self.assertEqual(trip.captain_id, captain.pk)
        self.assertEqual(trip.agreed_fare, Decimal("45.00"))
        self.assertEqual(offer.status, CaptainOffer.Status.ACCEPTED)

    def test_customer_cannot_accept_offer_from_another_trip(self):
        _, captain = self.create_approved_captain(
            username="api_wrong_trip_offer_captain",
        )

        TripPricingConfig.objects.create(
            city="Suez",
            base_fare=Decimal("10.00"),
            price_per_km=Decimal("5.00"),
            minimum_fare=Decimal("15.00"),
            maximum_fare=Decimal("100.00"),
            is_active=True,
        )

        trip1 = Trip.objects.create(
            customer=self.customer,
            pickup_address="Suez",
            pickup_latitude="29.9668",
            pickup_longitude="32.5498",
            destination_address="Port Tawfik",
            destination_latitude="29.9500",
            destination_longitude="32.5700",
            estimated_fare=Decimal("50.00"),
        )

        trip2 = Trip.objects.create(
            customer=self.customer,
            pickup_address="Suez",
            pickup_latitude="29.9668",
            pickup_longitude="32.5498",
            destination_address="Port Tawfik",
            destination_latitude="29.9500",
            destination_longitude="32.5700",
            estimated_fare=Decimal("50.00"),
        )

        offer = create_captain_offer(
            trip_id=trip1.pk,
            captain=captain,
            amount="45.00",
        )

        self.api_client.force_authenticate(user=self.customer_user)

        response = self.api_client.post(
            f"/api/trips/{trip2.pk}/offers/{offer.pk}/accept/",
            {},
            format="json",
        )

        self.assertEqual(response.status_code, 404)

        trip1.refresh_from_db()
        trip2.refresh_from_db()
        offer.refresh_from_db()

        self.assertEqual(trip1.status, Trip.Status.REQUESTED)
        self.assertEqual(trip2.status, Trip.Status.REQUESTED)
        self.assertEqual(offer.status, CaptainOffer.Status.PENDING)


    def test_anonymous_user_cannot_accept_offer(self):
        trip = Trip.objects.create(
            customer=self.customer,
            pickup_address="Suez",
            pickup_latitude="29.9668",
            pickup_longitude="32.5498",
            destination_address="Port Tawfik",
            destination_latitude="29.9500",
            destination_longitude="32.5700",
        )

        self.api_client.force_authenticate(user=None)

        response = self.api_client.post(
            f"/api/trips/{trip.pk}/offers/999/accept/",
            {},
            format="json",
        )

        self.assertEqual(response.status_code, 403)

    def test_captain_cannot_accept_offer(self):
        captain_user, _ = self.create_approved_captain(
            username="forbidden_accept_offer_captain",
        )

        trip = Trip.objects.create(
            customer=self.customer,
            pickup_address="Suez",
            pickup_latitude="29.9668",
            pickup_longitude="32.5498",
            destination_address="Port Tawfik",
            destination_latitude="29.9500",
            destination_longitude="32.5700",
        )

        self.api_client.force_authenticate(user=captain_user)

        response = self.api_client.post(
            f"/api/trips/{trip.pk}/offers/999/accept/",
            {},
            format="json",
        )

        self.assertEqual(response.status_code, 403)

    def test_expired_offer_cannot_be_accepted_through_api(self):
        _, captain = self.create_approved_captain(
            username="api_expired_offer_captain",
        )

        TripPricingConfig.objects.create(
            city="Suez",
            base_fare=Decimal("10.00"),
            price_per_km=Decimal("5.00"),
            minimum_fare=Decimal("15.00"),
            maximum_fare=Decimal("100.00"),
            is_active=True,
        )

        trip = Trip.objects.create(
            customer=self.customer,
            pickup_address="Suez",
            pickup_latitude="29.9668",
            pickup_longitude="32.5498",
            destination_address="Port Tawfik",
            destination_latitude="29.9500",
            destination_longitude="32.5700",
            estimated_fare=Decimal("50.00"),
        )

        offer = create_captain_offer(
            trip_id=trip.pk,
            captain=captain,
            amount="45.00",
        )

        offer.expires_at = timezone.now() - timedelta(minutes=1)
        offer.save(update_fields=["expires_at"])

        self.api_client.force_authenticate(user=self.customer_user)

        response = self.api_client.post(
            f"/api/trips/{trip.pk}/offers/{offer.pk}/accept/",
            {},
            format="json",
        )

        self.assertEqual(
            response.status_code,
            400,
            msg=f"API response: {response.data}",
        )

        trip.refresh_from_db()
        offer.refresh_from_db()

        self.assertEqual(trip.status, Trip.Status.REQUESTED)
        self.assertIsNone(trip.captain_id)
        self.assertEqual(offer.status, CaptainOffer.Status.EXPIRED)





    def test_customer_can_cancel_own_trip(self):
        trip = Trip.objects.create(
            customer=self.customer,
            pickup_address="Suez",
            pickup_latitude="29.9668",
            pickup_longitude="32.5498",
            destination_address="Port Tawfik",
            destination_latitude="29.9500",
            destination_longitude="32.5700",
        )

        self.api_client.force_authenticate(user=self.customer_user)
        response = self.api_client.post(
            f"/api/trips/{trip.pk}/cancel/",
            {"reason": "Changed my plans"},
            format="json",
        )

        self.assertEqual(
            response.status_code,
            200,
            msg=f"API response: {response.data}",
        )

        trip.refresh_from_db()
        self.assertEqual(trip.status, Trip.Status.CANCELLED)
        self.assertEqual(trip.cancelled_by, self.customer_user)
        self.assertEqual(trip.cancellation_reason, "Changed my plans")

    def test_unrelated_user_cannot_cancel_trip(self):
        trip = Trip.objects.create(
            customer=self.customer,
            pickup_address="Suez",
            pickup_latitude="29.9668",
            pickup_longitude="32.5498",
            destination_address="Port Tawfik",
            destination_latitude="29.9500",
            destination_longitude="32.5700",
        )

        unrelated_user = User.objects.create_user(
            username="unrelated_cancel_user",
            password="test-password-123",
            role=User.Role.CUSTOMER,
        )
        CustomerProfile.objects.create(
            user=unrelated_user,
            phone_number="01000000004",
        )

        self.api_client.force_authenticate(user=unrelated_user)
        response = self.api_client.post(
            f"/api/trips/{trip.pk}/cancel/",
            {"reason": "Not my trip"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        trip.refresh_from_db()
        self.assertEqual(trip.status, Trip.Status.REQUESTED)

    def test_cancel_trip_requires_reason(self):
        trip = Trip.objects.create(
            customer=self.customer,
            pickup_address="Suez",
            pickup_latitude="29.9668",
            pickup_longitude="32.5498",
            destination_address="Port Tawfik",
            destination_latitude="29.9500",
            destination_longitude="32.5700",
        )

        self.api_client.force_authenticate(user=self.customer_user)
        response = self.api_client.post(
            f"/api/trips/{trip.pk}/cancel/",
            {"reason": "   "},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        trip.refresh_from_db()
        self.assertEqual(trip.status, Trip.Status.REQUESTED)





    def test_cancelling_accepted_trip_releases_commission_reservation(self):
        from trips.services.lifecycle import accept_trip
        from trips.services.wallet import reserve_trip_commission

        captain_user, captain = self.create_approved_captain(
            username="cancel_reserve_captain"
        )

        trip = Trip.objects.create(
            customer=self.customer,
            pickup_address="Suez",
            pickup_latitude="29.9668",
            pickup_longitude="32.5498",
            destination_address="Port Tawfik",
            destination_latitude="29.9500",
            destination_longitude="32.5700",
            estimated_fare=Decimal("100.00"),
        )

        accept_trip(trip_id=trip.pk, captain=captain)

        wallet = CaptainWallet.objects.get(captain=captain)
        wallet.refresh_from_db()
        reserved_before = wallet.reserved_balance

        self.assertGreater(reserved_before, Decimal("0.00"))

        self.api_client.force_authenticate(user=self.customer_user)
        response = self.api_client.post(
            f"/api/trips/{trip.pk}/cancel/",
            {"reason": "Customer changed plans"},
            format="json",
        )

        self.assertEqual(
            response.status_code,
            200,
            msg=f"API response: {response.data}",
        )

        wallet.refresh_from_db()
        trip.refresh_from_db()

        self.assertEqual(trip.status, Trip.Status.CANCELLED)
        self.assertEqual(wallet.reserved_balance, Decimal("0.00"))
        self.assertEqual(wallet.balance, Decimal("100.00"))


    @patch(
            "trips.services.lifecycle.release_trip_commission_reservation",
            side_effect=ValidationError("Simulated reservation release failure"),
        )
    def test_cancel_failure_rolls_back_trip_cancellation(
            self,
            mocked_release,
        ):
            from trips.services.lifecycle import accept_trip

            _, captain = self.create_approved_captain(
                username="cancel_rollback_captain"
            )

            trip = Trip.objects.create(
                customer=self.customer,
                pickup_address="Suez",
                pickup_latitude="29.9668",
                pickup_longitude="32.5498",
                destination_address="Port Tawfik",
                destination_latitude="29.9500",
                destination_longitude="32.5700",
                estimated_fare=Decimal("100.00"),
            )

            accept_trip(trip_id=trip.pk, captain=captain)

            self.api_client.force_authenticate(user=self.customer_user)
            response = self.api_client.post(
                f"/api/trips/{trip.pk}/cancel/",
                {"reason": "Customer changed plans"},
                format="json",
            )

            self.assertEqual(response.status_code, 400)

            trip.refresh_from_db()
            self.assertEqual(trip.status, Trip.Status.ACCEPTED)
            self.assertIsNone(trip.cancelled_at)

            mocked_release.assert_called_once_with(trip_id=trip.pk)




    def test_customer_cannot_cancel_completed_trip(self):
        trip = Trip.objects.create(
            customer=self.customer,
            pickup_address="Suez",
            pickup_latitude="29.9668",
            pickup_longitude="32.5498",
            destination_address="Port Tawfik",
            destination_latitude="29.9500",
            destination_longitude="32.5700",
            status=Trip.Status.COMPLETED,
        )

        self.api_client.force_authenticate(user=self.customer_user)
        response = self.api_client.post(
            f"/api/trips/{trip.pk}/cancel/",
            {"reason": "Cancel completed trip"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)

        trip.refresh_from_db()
        self.assertEqual(trip.status, Trip.Status.COMPLETED)




    def test_captain_without_profile_cannot_view_available_trips(self):
        captain_user = User.objects.create_user(
            username="captain_without_profile",
            password="test-password-123",
            role=User.Role.CAPTAIN,
        )

        self.api_client.force_authenticate(user=captain_user)
        response = self.api_client.get("/api/trips/available/")

        self.assertEqual(response.status_code, 403)





    def test_accept_offer_rolls_back_when_commission_reservation_fails(self):
        from unittest.mock import patch

        TripPricingConfig.objects.create(
            city="Suez",
            base_fare=Decimal("10.00"),
            price_per_km=Decimal("5.00"),
            minimum_fare=Decimal("15.00"),
            maximum_fare=Decimal("100.00"),
            is_active=True,
        )

        _, captain = self.create_approved_captain(
            username="offer_reserve_failure_captain",
        )

        trip = Trip.objects.create(
            customer=self.customer,
            pickup_address="Suez",
            pickup_latitude="29.9668",
            pickup_longitude="32.5498",
            destination_address="Port Tawfik",
            destination_latitude="29.9500",
            destination_longitude="32.5700",
            estimated_fare=Decimal("50.00"),
        )

        offer = create_captain_offer(
            trip_id=trip.pk,
            captain=captain,
            amount="45.00",
        )

        with patch(
            "trips.services.offers.reserve_trip_commission",
            side_effect=ValidationError("Insufficient wallet balance."),
        ):
            with self.assertRaises(ValidationError):
                accept_captain_offer(
                    offer_id=offer.pk,
                    customer=self.customer,
                )

        trip.refresh_from_db()
        offer.refresh_from_db()

        self.assertEqual(trip.status, Trip.Status.REQUESTED)
        self.assertIsNone(trip.captain_id)
        self.assertEqual(offer.status, CaptainOffer.Status.PENDING)





    def test_captain_cannot_create_offer_with_non_finite_amount(self):
        _, captain = self.create_approved_captain(
            username="offer_non_finite_amount_captain",
        )

        TripPricingConfig.objects.create(
            city="Suez",
            base_fare=Decimal("10.00"),
            price_per_km=Decimal("5.00"),
            minimum_fare=Decimal("15.00"),
            maximum_fare=Decimal("100.00"),
            is_active=True,
        )

        trip = Trip.objects.create(
            customer=self.customer,
            pickup_address="Suez",
            pickup_latitude="29.9668",
            pickup_longitude="32.5498",
            destination_address="Port Tawfik",
            destination_latitude="29.9500",
            destination_longitude="32.5700",
            estimated_fare=Decimal("50.00"),
        )

        for invalid_amount in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(amount=invalid_amount):
                with self.assertRaises(ValidationError):
                    create_captain_offer(
                        trip_id=trip.pk,
                        captain=captain,
                        amount=invalid_amount,
                    )

        self.assertFalse(
            CaptainOffer.objects.filter(trip=trip).exists()
        )



    def test_captain_cannot_create_offer_with_more_than_two_decimal_places(self):
        _, captain = self.create_approved_captain(
            username="offer_decimal_precision_captain",
        )

        TripPricingConfig.objects.create(
            city="Suez",
            base_fare=Decimal("10.00"),
            price_per_km=Decimal("5.00"),
            minimum_fare=Decimal("15.00"),
            maximum_fare=Decimal("100.00"),
            is_active=True,
        )

        trip = Trip.objects.create(
            customer=self.customer,
            pickup_address="Suez",
            pickup_latitude="29.9668",
            pickup_longitude="32.5498",
            destination_address="Port Tawfik",
            destination_latitude="29.9500",
            destination_longitude="32.5700",
            estimated_fare=Decimal("50.00"),
        )

        with self.assertRaises(ValidationError):
            create_captain_offer(
                trip_id=trip.pk,
                captain=captain,
                amount="45.126",
            )

        self.assertFalse(
            CaptainOffer.objects.filter(trip=trip).exists()
        )





    def test_captain_cannot_create_offer_above_decimal_field_maximum(self):
        _, captain = self.create_approved_captain(
            username="offer_maximum_amount_captain",
        )

        TripPricingConfig.objects.create(
            city="Suez",
            base_fare=Decimal("10.00"),
            price_per_km=Decimal("5.00"),
            minimum_fare=Decimal("15.00"),
            maximum_fare=Decimal("100.00"),
            is_active=True,
        )

        trip = Trip.objects.create(
            customer=self.customer,
            pickup_address="Suez",
            pickup_latitude="29.9668",
            pickup_longitude="32.5498",
            destination_address="Port Tawfik",
            destination_latitude="29.9500",
            destination_longitude="32.5700",
            estimated_fare=Decimal("50.00"),
        )

        with self.assertRaises(ValidationError):
            create_captain_offer(
                trip_id=trip.pk,
                captain=captain,
                amount="100000000",
            )

        self.assertFalse(
            CaptainOffer.objects.filter(trip=trip).exists()
        )



    def test_accept_offer_fails_with_insufficient_wallet_balance(self):
        TripPricingConfig.objects.create(
            city="Suez",
            base_fare=Decimal("10.00"),
            price_per_km=Decimal("5.00"),
            minimum_fare=Decimal("15.00"),
            maximum_fare=Decimal("100.00"),
            is_active=True,
        )

        _, captain = self.create_approved_captain(
            username="offer_insufficient_balance_captain",
        )

        wallet = CaptainWallet.objects.get(captain=captain)
        wallet.balance = Decimal("1.00")
        wallet.reserved_balance = Decimal("0.00")
        wallet.save(update_fields=["balance", "reserved_balance"])

        trip = Trip.objects.create(
            customer=self.customer,
            pickup_address="Suez",
            pickup_latitude="29.9668",
            pickup_longitude="32.5498",
            destination_address="Port Tawfik",
            destination_latitude="29.9500",
            destination_longitude="32.5700",
            estimated_fare=Decimal("50.00"),
        )

        offer = create_captain_offer(
            trip_id=trip.pk,
            captain=captain,
            amount="45.00",
        )

        with self.assertRaises(ValidationError):
            accept_captain_offer(
                offer_id=offer.pk,
                customer=self.customer,
            )

        trip.refresh_from_db()
        offer.refresh_from_db()
        wallet.refresh_from_db()

        self.assertEqual(trip.status, Trip.Status.REQUESTED)
        self.assertIsNone(trip.captain_id)
        self.assertIsNone(trip.vehicle_id)          # 👈 الإضافة الجديدة
        self.assertEqual(offer.status, CaptainOffer.Status.PENDING)
        self.assertEqual(wallet.balance, Decimal("1.00"))
        self.assertEqual(wallet.reserved_balance, Decimal("0.00"))

        self.assertFalse(
            FinancialTransaction.objects.filter(
                reference=f"trip-commission-reserve:{trip.pk}"
            ).exists()
        )


    def test_captain_with_invalid_documents_cannot_accept_trip(self):
        from accounts.models import CaptainDocument

        captain_user, captain = self.create_approved_captain(
            username="invalid_docs_captain",
        )

        document = CaptainDocument.objects.filter(
            captain=captain
        ).first()
        document.review_status = CaptainDocument.ReviewStatus.REJECTED
        document.save(update_fields=["review_status"])

        trip = Trip.objects.create(
            customer=self.customer,
            pickup_address="Suez",
            pickup_latitude="29.9668",
            pickup_longitude="32.5498",
            destination_address="Port Tawfik",
            destination_latitude="29.9500",
            destination_longitude="32.5700",
            estimated_fare=Decimal("100.00"),
        )

        self.api_client.force_authenticate(user=captain_user)
        response = self.api_client.post(
            f"/api/trips/{trip.pk}/actions/accept/",
            {},
            format="json",
        )

        self.assertEqual(
            response.status_code,
            400,
            msg=f"API response: {response.data}",
        )

        trip.refresh_from_db()
        self.assertEqual(trip.status, Trip.Status.REQUESTED)
        self.assertIsNone(trip.captain_id)
