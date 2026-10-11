from rest_framework.test import APIClient

from django.core.exceptions import ValidationError
from django.db import transaction
from django.test import TestCase
from django.utils import timezone

from accounts.models import (
    CaptainDocument,
    CaptainProfile,
    CustomerProfile,
    User,
)
from accounts.services.reviews import (
    approve_captain,
    approve_document,
    approve_vehicle,
    set_captain_online_status,
)
from trips.models import Trip
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

        self.trip = Trip.objects.create(
            customer=self.customer,
            pickup_address="Suez Pickup",
            pickup_latitude="29.966800",
            pickup_longitude="32.549800",
            destination_address="Suez Destination",
            destination_latitude="29.973700",
            destination_longitude="32.526300",
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

        trip = complete_trip(
            trip_id=trip.pk,
            captain=self.captain,
            final_fare=100,
        )
        self.assertEqual(trip.status, Trip.Status.COMPLETED)
        self.assertEqual(trip.final_fare, 100)
        self.assertIsNotNone(trip.completed_at)

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
                final_fare=100,
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
        complete_trip(
            trip_id=self.trip.pk,
            captain=self.captain,
            final_fare=100,
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

    def test_customer_can_create_trip(self):
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
            201,
            msg=f"API response: {response.data}",
        )

        created_trip = Trip.objects.get(pk=response.data["id"])

        self.assertEqual(created_trip.status, "REQUESTED")
        self.assertEqual(created_trip.customer, self.customer)

        self.assertEqual(
            Trip.objects.filter(customer=self.customer).count(),
            1,
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
            approval_status=Vehicle.ApprovalStatus.PENDING,
        )
        approve_vehicle(vehicle, reviewer)

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
        )

        self.api_client.force_authenticate(user=captain_user)

        actions = [
            ("accept", {}, "ACCEPTED"),
            ("arriving", {}, "ARRIVING"),
            ("arrived", {}, "ARRIVED"),
            ("start", {}, "IN_PROGRESS"),
            ("complete", {"final_fare": "75.00"}, "COMPLETED"),
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
        self.assertIsNotNone(trip.started_at)
        self.assertIsNotNone(trip.completed_at)


    def test_complete_trip_rejects_invalid_final_fare(self):
        captain_user, captain = self.create_approved_captain(
            username="invalid_fare_captain"
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

        for invalid_fare in ["abc", "-10", "NaN", "Infinity"]:
            with self.subTest(final_fare=invalid_fare):
                response = self.api_client.post(
                    f"/api/trips/{trip.pk}/actions/complete/",
                    {"final_fare": invalid_fare},
                    format="json",
                )

                self.assertEqual(
                    response.status_code,
                    400,
                    msg=f"Fare {invalid_fare}: {response.data}",
                )

                trip.refresh_from_db()
                self.assertEqual(trip.status, "IN_PROGRESS")


    def test_complete_trip_requires_final_fare(self):
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
        
        
    
    def test_available_trips_exposes_only_required_fields(self):
        captain_user, captain = self.create_approved_captain(
            username="privacy_api_captain"
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
