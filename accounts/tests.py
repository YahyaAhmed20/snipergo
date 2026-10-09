from django.test import TestCase

# Create your tests here.

from datetime import timedelta
from tempfile import TemporaryDirectory

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.test import Client
from django.utils import timezone

from accounts.models import User, CaptainProfile, CaptainDocument
from accounts.services.reviews import (
    approve_captain,
    reject_captain,
    approve_document,
    reject_document,
    approve_vehicle,
    reject_vehicle,
)
from vehicles.models import Vehicle


@override_settings(MEDIA_ROOT=TemporaryDirectory().name)
class ReviewServiceTests(TestCase):
    def setUp(self):
        self.reviewer = User.objects.create_superuser(
            username="review_admin",
            email="admin@example.com",
            password="TestPassword-123!",
        )

        self.captain_user = User.objects.create_user(
            username="captain_test",
            password="TestPassword-123!",
            role=User.Role.CAPTAIN,
        )

        self.captain = CaptainProfile.objects.create(
            user=self.captain_user,
        )

    def create_document(
        self,
        document_type,
        expires_at=None,
        status=CaptainDocument.ReviewStatus.APPROVED,
    ):
        return CaptainDocument.objects.create(
            captain=self.captain,
            document_type=document_type,
            file="private/test-document.pdf",
            expires_at=expires_at,
            review_status=status,
            reviewed_by=self.reviewer,
            reviewed_at=timezone.now(),
        )

    def create_required_documents(self):
        for document_type in (
            CaptainDocument.DocumentType.NATIONAL_ID,
            CaptainDocument.DocumentType.DRIVING_LICENSE,
            CaptainDocument.DocumentType.VEHICLE_LICENSE,
        ):
            self.create_document(document_type)

    def create_vehicle(self):
        return Vehicle.objects.create(
            captain=self.captain,
            vehicle_type=Vehicle.VehicleType.SCOOTER,
            brand="Test Brand",
            model_name="Test Model",
            manufacture_year=2025,
            plate_number="TEST-001",
            chassis_number="TEST-CHASSIS-001",
            front_photo="vehicles/front/test.jpg",
            rear_photo="vehicles/rear/test.jpg",
            side_photo="vehicles/side/test.jpg",
            plate_photo="vehicles/plate/test.jpg",
        )

    def test_captain_cannot_be_approved_without_documents(self):
        with self.assertRaises(ValidationError):
            approve_captain(self.captain, self.reviewer)

        self.captain.refresh_from_db()
        self.assertEqual(
            self.captain.approval_status,
            CaptainProfile.ApprovalStatus.PENDING,
        )

    def test_captain_cannot_be_approved_with_expired_document(self):
        self.create_required_documents()
        document = self.captain.documents.get(
            document_type=CaptainDocument.DocumentType.DRIVING_LICENSE
        )
        document.expires_at = timezone.localdate() - timedelta(days=1)
        document.save(update_fields=["expires_at"])

        with self.assertRaises(ValidationError):
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

    def test_reject_captain_records_reason_reviewer_and_time(self):
        reject_captain(
            self.captain,
            self.reviewer,
            "Invalid identity document",
        )

        self.captain.refresh_from_db()
        self.assertEqual(
            self.captain.approval_status,
            CaptainProfile.ApprovalStatus.REJECTED,
        )
        self.assertEqual(
            self.captain.rejection_reason,
            "Invalid identity document",
        )
        self.assertEqual(self.captain.reviewed_by, self.reviewer)
        self.assertIsNotNone(self.captain.reviewed_at)

    def test_reject_vehicle_requires_reason(self):
        vehicle = self.create_vehicle()

        with self.assertRaises(ValidationError):
            reject_vehicle(vehicle, self.reviewer, "   ")

        vehicle.refresh_from_db()
        self.assertEqual(
            vehicle.approval_status,
            Vehicle.ApprovalStatus.PENDING,
        )

    def test_approve_document_marks_as_approved(self):
        document = self.create_document(
            CaptainDocument.DocumentType.NATIONAL_ID,
            status=CaptainDocument.ReviewStatus.PENDING,
        )

        approve_document(document, self.reviewer)

        document.refresh_from_db()
        self.assertEqual(
            document.review_status,
            CaptainDocument.ReviewStatus.APPROVED,
        )

    def test_reject_document_requires_reason(self):
        document = self.create_document(
            CaptainDocument.DocumentType.NATIONAL_ID,
            status=CaptainDocument.ReviewStatus.PENDING,
        )

        with self.assertRaises(ValidationError):
            reject_document(document, self.reviewer, "   ")

        document.refresh_from_db()
        self.assertEqual(
            document.review_status,
            CaptainDocument.ReviewStatus.PENDING,
        )

    def test_reject_document_records_reason_and_reviewer(self):
        document = self.create_document(
            CaptainDocument.DocumentType.NATIONAL_ID,
            status=CaptainDocument.ReviewStatus.PENDING,
        )

        reject_document(
            document,
            self.reviewer,
            "Document image is unclear",
        )

        document.refresh_from_db()
        self.assertEqual(
            document.review_status,
            CaptainDocument.ReviewStatus.REJECTED,
        )
        self.assertEqual(
            document.rejection_reason,
            "Document image is unclear",
        )
        self.assertEqual(document.reviewed_by, self.reviewer)
        self.assertIsNotNone(document.reviewed_at)


class CaptainDocumentAdminTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            username="document_admin",
            email="documents@example.com",
            password="TestPassword-123!",
        )

        self.captain_user = User.objects.create_user(
            username="document_captain",
            password="TestPassword-123!",
            role=User.Role.CAPTAIN,
        )

        self.captain = CaptainProfile.objects.create(
            user=self.captain_user,
        )

        self.document = CaptainDocument.objects.create(
            captain=self.captain,
            document_type=CaptainDocument.DocumentType.NATIONAL_ID,
            file="private/test-document.pdf",
        )

        self.client = Client()
        self.client.force_login(self.admin_user)

    def test_non_staff_cannot_download_document(self):
        self.client.logout()

        url = reverse(
            "admin:accounts_captaindocument_file",
            args=[self.document.pk],
        )

        response = self.client.get(url)

        self.assertIn(response.status_code, (302, 403))

    def test_staff_without_document_change_permission_cannot_download(self):
        staff_user = User.objects.create_user(
            username="limited_staff",
            email="limited@example.com",
            password="TestPassword-123!",
            is_staff=True,
        )

        self.client.force_login(staff_user)

        url = reverse(
            "admin:accounts_captaindocument_file",
            args=[self.document.pk],
        )

        response = self.client.get(url)

        self.assertEqual(response.status_code, 403)

    def test_review_page_opens_for_admin(self):
        url = reverse(
            "admin:accounts_captaindocument_review",
            args=[self.document.pk],
        )

        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Review captain document")

    def test_approve_document_through_admin(self):
        url = reverse(
            "admin:accounts_captaindocument_review",
            args=[self.document.pk],
        )

        response = self.client.post(
            url,
            {"action": "approve"},
        )

        self.assertEqual(response.status_code, 302)

        self.document.refresh_from_db()
        self.assertEqual(
            self.document.review_status,
            CaptainDocument.ReviewStatus.APPROVED,
        )
        self.assertEqual(
            self.document.reviewed_by,
            self.admin_user,
        )

    def test_non_staff_cannot_open_review_page(self):
        self.client.logout()

        url = reverse(
            "admin:accounts_captaindocument_review",
            args=[self.document.pk],
        )

        response = self.client.get(url)

        self.assertIn(response.status_code, (302, 403))

    def test_authorized_admin_can_download_document(self):
        self.document.file.save(
            "test-document.txt",
            SimpleUploadedFile(
                "test-document.txt",
                b"SNIPER GO test document",
                content_type="text/plain",
            ),
            save=True,
        )

        url = reverse(
            "admin:accounts_captaindocument_file",
            args=[self.document.pk],
        )

        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertIn("private", response["Cache-Control"])
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(
            b"".join(response.streaming_content),
            b"SNIPER GO test document",
        )