from io import BytesIO
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from PIL import Image

from accounts.models import (
    CaptainDocument,
    CaptainProfile,
    User,
)
from accounts.services.reviews import (
    approve_captain,
    approve_document,
    approve_vehicle,
    reject_document,
    reject_vehicle,
)
from vehicles.models import Vehicle
from vehicles.models import Vehicle
from accounts.services.reviews import approve_vehicle, reject_vehicle


class VehicleReviewServiceTests(TestCase):
    def setUp(self):
        self.reviewer = User.objects.create_superuser(
            username="vehicle_admin",
            email="vehicle-admin@example.com",
            password="TestPassword-123!",
        )
        self.captain_user = User.objects.create_user(
            username="vehicle_captain",
            password="TestPassword-123!",
            role=User.Role.CAPTAIN,
        )
        self.captain = CaptainProfile.objects.create(
            user=self.captain_user,
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

    def create_vehicle(self):
        return Vehicle.objects.create(
            captain=self.captain,
            vehicle_type=Vehicle.VehicleType.SCOOTER,
            brand="Test Brand",
            model_name="Test Model",
            manufacture_year=2025,
            plate_number="VEH-TEST-001",
            chassis_number="VEH-CHASSIS-001",
            front_photo="vehicles/front/test.jpg",
            rear_photo="vehicles/rear/test.jpg",
            side_photo="vehicles/side/test.jpg",
            plate_photo="vehicles/plate/test.jpg",
        )

    def approve_captain_for_vehicle(self):
        self.create_required_documents()
        approve_captain(self.captain, self.reviewer)

    def test_vehicle_cannot_be_approved_before_captain(self):
        self.create_required_documents()
        vehicle = self.create_vehicle()

        with self.assertRaises(ValidationError):
            approve_vehicle(vehicle, self.reviewer)

        vehicle.refresh_from_db()
        self.assertEqual(
            vehicle.approval_status,
            Vehicle.ApprovalStatus.PENDING,
        )

    def test_vehicle_cannot_be_approved_without_required_documents(self):
        self.captain.approval_status = CaptainProfile.ApprovalStatus.APPROVED
        self.captain.save(update_fields=["approval_status"])
        vehicle = self.create_vehicle()

        with self.assertRaises(ValidationError):
            approve_vehicle(vehicle, self.reviewer)

        vehicle.refresh_from_db()
        self.assertEqual(
            vehicle.approval_status,
            Vehicle.ApprovalStatus.PENDING,
        )

    def test_approved_captain_can_have_vehicle_approved(self):
        self.approve_captain_for_vehicle()
        vehicle = self.create_vehicle()

        approve_vehicle(vehicle, self.reviewer)

        vehicle.refresh_from_db()
        self.assertEqual(
            vehicle.approval_status,
            Vehicle.ApprovalStatus.APPROVED,
        )
        self.assertEqual(vehicle.reviewed_by, self.reviewer)
        self.assertIsNotNone(vehicle.reviewed_at)

    def test_reject_vehicle_requires_reason(self):
        vehicle = self.create_vehicle()

        with self.assertRaises(ValidationError):
            reject_vehicle(vehicle, self.reviewer, "   ")

        vehicle.refresh_from_db()
        self.assertEqual(
            vehicle.approval_status,
            Vehicle.ApprovalStatus.PENDING,
        )

    def test_reject_vehicle_records_reason_reviewer_and_time(self):
        vehicle = self.create_vehicle()

        reject_vehicle(
            vehicle,
            self.reviewer,
            "Vehicle documents are unclear",
        )

        vehicle.refresh_from_db()
        self.assertEqual(
            vehicle.approval_status,
            Vehicle.ApprovalStatus.REJECTED,
        )
        self.assertEqual(
            vehicle.rejection_reason,
            "Vehicle documents are unclear",
        )
        self.assertEqual(vehicle.reviewed_by, self.reviewer)
        self.assertIsNotNone(vehicle.reviewed_at)


class VehicleAdminTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            username="vehicle_admin_ui",
            email="vehicle-admin-ui@example.com",
            password="TestPassword-123!",
        )
        self.captain_user = User.objects.create_user(
            username="vehicle_admin_captain",
            password="TestPassword-123!",
            role=User.Role.CAPTAIN,
        )
        self.captain = CaptainProfile.objects.create(
            user=self.captain_user,
        )
        self.vehicle = Vehicle.objects.create(
            captain=self.captain,
            vehicle_type=Vehicle.VehicleType.SCOOTER,
            brand="Admin Test",
            model_name="Test Model",
            manufacture_year=2025,
            plate_number="ADMIN-TEST-001",
            chassis_number="ADMIN-CHASSIS-001",
            front_photo="vehicles/front/admin.jpg",
            rear_photo="vehicles/rear/admin.jpg",
            side_photo="vehicles/side/admin.jpg",
            plate_photo="vehicles/plate/admin.jpg",
        )
        self.client = Client()
        self.client.force_login(self.admin_user)

    def test_vehicle_change_page_shows_status_as_readonly(self):
        url = reverse(
            "admin:vehicles_vehicle_change",
            args=[self.vehicle.pk],
        )
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pending")
        self.assertNotContains(
            response,
            '<select name="approval_status"',
            html=False,
        )

    def test_reject_page_opens_for_admin(self):
        url = reverse(
            "admin:vehicles_vehicle_reject",
            args=[self.vehicle.pk],
        )
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Reject vehicle")

    def test_reject_page_requires_reason(self):
        url = reverse(
            "admin:vehicles_vehicle_reject",
            args=[self.vehicle.pk],
        )
        response = self.client.post(url, {"reason": "   "})

        self.assertEqual(response.status_code, 200)
        self.vehicle.refresh_from_db()
        self.assertEqual(
            self.vehicle.approval_status,
            Vehicle.ApprovalStatus.PENDING,
        )



@override_settings(MEDIA_ROOT=TemporaryDirectory().name)
class VehicleRegistrationViewTests(TestCase):
    def setUp(self):
        self.captain_user = User.objects.create_user(
            username="register_vehicle_captain",
            password="TestPassword-123!",
            role=User.Role.CAPTAIN,
        )
        self.captain = CaptainProfile.objects.create(
            user=self.captain_user,
        )
        self.other_captain_user = User.objects.create_user(
            username="other_vehicle_captain",
            password="TestPassword-123!",
            role=User.Role.CAPTAIN,
        )
        self.other_captain = CaptainProfile.objects.create(
            user=self.other_captain_user,
        )
        self.url = reverse("vehicles:my_vehicles")

    def make_image(self, name):
        buffer = BytesIO()
        Image.new("RGB", (20, 20), color="white").save(
            buffer,
            format="JPEG",
        )
        return SimpleUploadedFile(
            name,
            buffer.getvalue(),
            content_type="image/jpeg",
        )

    def vehicle_data(self):
        return {
            "vehicle_type": Vehicle.VehicleType.SCOOTER,
            "brand": "Test Brand",
            "model_name": "Test Model",
            "manufacture_year": 2025,
            "plate_number": "REG-TEST-001",
            "chassis_number": "REG-CHASSIS-001",
            "front_photo": self.make_image("front.jpg"),
            "rear_photo": self.make_image("rear.jpg"),
            "side_photo": self.make_image("side.jpg"),
            "plate_photo": self.make_image("plate.jpg"),
        }

    def test_anonymous_user_is_redirected_to_login(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login", response.url)

    def test_captain_can_register_vehicle(self):
        self.client.force_login(self.captain_user)

        response = self.client.post(self.url, self.vehicle_data())

        self.assertRedirects(response, self.url)
        vehicle = Vehicle.objects.get(plate_number="REG-TEST-001")
        self.assertEqual(vehicle.captain, self.captain)
        self.assertEqual(
            vehicle.approval_status,
            Vehicle.ApprovalStatus.PENDING,
        )

    def test_customer_cannot_register_vehicle(self):
        customer = User.objects.create_user(
            username="vehicle_customer",
            password="TestPassword-123!",
            role=User.Role.CUSTOMER,
        )
        self.client.force_login(customer)

        response = self.client.post(self.url, self.vehicle_data())

        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            Vehicle.objects.filter(plate_number="REG-TEST-001").exists()
        )

    def test_captain_only_sees_own_vehicles(self):
        Vehicle.objects.create(
            captain=self.other_captain,
            vehicle_type=Vehicle.VehicleType.SCOOTER,
            brand="Other Brand",
            model_name="Other Model",
            manufacture_year=2025,
            plate_number="OTHER-REG-001",
            chassis_number="OTHER-CHASSIS-001",
            front_photo="vehicles/front/other.jpg",
            rear_photo="vehicles/rear/other.jpg",
            side_photo="vehicles/side/other.jpg",
            plate_photo="vehicles/plate/other.jpg",
        )
        self.client.force_login(self.captain_user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertQuerySetEqual(
            response.context["vehicles"],
            [],
        )

    def test_missing_required_photo_is_rejected(self):
        self.client.force_login(self.captain_user)
        data = self.vehicle_data()
        data.pop("plate_photo")

        response = self.client.post(self.url, data)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            Vehicle.objects.filter(plate_number="REG-TEST-001").exists()
        )