from datetime import timedelta
from decimal import Decimal
from io import BytesIO
from tempfile import TemporaryDirectory
from accounts.forms import CaptainDocumentForm
from django.core.exceptions import ValidationError
from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from accounts.models import (
    CaptainDocument,
    CaptainProfile,
    CaptainWallet,
    User,
)
from accounts.services.reviews import (
    approve_captain,
    reject_captain,
    approve_document,
    reject_document,
    approve_vehicle,
    reject_vehicle,
    set_captain_online_status,
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

        CaptainWallet.objects.create(
            captain=self.captain,
            balance=Decimal("100.00"),
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

    def test_pending_captain_cannot_go_online(self):
        with self.assertRaises(ValidationError):
            set_captain_online_status(self.captain, True)

        self.captain.refresh_from_db()
        self.assertEqual(
            self.captain.operational_status,
            CaptainProfile.OperationalStatus.OFFLINE,
        )

    def test_approved_captain_without_documents_cannot_go_online(self):
        self.captain.approval_status = (
            CaptainProfile.ApprovalStatus.APPROVED
        )
        self.captain.save(update_fields=["approval_status"])

        with self.assertRaises(ValidationError):
            set_captain_online_status(self.captain, True)

    def test_captain_without_approved_vehicle_cannot_go_online(self):
        self.create_required_documents()
        self.captain.approval_status = (
            CaptainProfile.ApprovalStatus.APPROVED
        )
        self.captain.save(update_fields=["approval_status"])

        with self.assertRaises(ValidationError):
            set_captain_online_status(self.captain, True)

    def test_eligible_captain_can_go_online(self):
        self.create_required_documents()
        self.captain.approval_status = (
            CaptainProfile.ApprovalStatus.APPROVED
        )
        self.captain.save(update_fields=["approval_status"])

        vehicle = self.create_vehicle()
        vehicle.approval_status = Vehicle.ApprovalStatus.APPROVED
        vehicle.save(update_fields=["approval_status"])

        set_captain_online_status(self.captain, True)

        self.captain.refresh_from_db()
        self.assertEqual(
            self.captain.operational_status,
            CaptainProfile.OperationalStatus.ONLINE,
        )

    def test_pending_required_document_cannot_go_online(self):
        self.create_required_documents()
        document = self.captain.documents.get(
            document_type=CaptainDocument.DocumentType.DRIVING_LICENSE
        )
        document.review_status = CaptainDocument.ReviewStatus.PENDING
        document.save(update_fields=["review_status"])

        self.captain.approval_status = CaptainProfile.ApprovalStatus.APPROVED
        self.captain.save(update_fields=["approval_status"])

        vehicle = self.create_vehicle()
        vehicle.approval_status = Vehicle.ApprovalStatus.APPROVED
        vehicle.save(update_fields=["approval_status"])

        with self.assertRaises(ValidationError):
            set_captain_online_status(self.captain, True)

        self.captain.refresh_from_db()
        self.assertEqual(
            self.captain.operational_status,
            CaptainProfile.OperationalStatus.OFFLINE,
        )

    def test_rejected_required_document_cannot_go_online(self):
        self.create_required_documents()
        document = self.captain.documents.get(
            document_type=CaptainDocument.DocumentType.DRIVING_LICENSE
        )
        document.review_status = CaptainDocument.ReviewStatus.REJECTED
        document.save(update_fields=["review_status"])

        self.captain.approval_status = CaptainProfile.ApprovalStatus.APPROVED
        self.captain.save(update_fields=["approval_status"])

        vehicle = self.create_vehicle()
        vehicle.approval_status = Vehicle.ApprovalStatus.APPROVED
        vehicle.save(update_fields=["approval_status"])

        with self.assertRaises(ValidationError):
            set_captain_online_status(self.captain, True)

    def test_expired_required_document_cannot_go_online(self):
        self.create_required_documents()
        document = self.captain.documents.get(
            document_type=CaptainDocument.DocumentType.DRIVING_LICENSE
        )
        document.expires_at = timezone.localdate() - timedelta(days=1)
        document.save(update_fields=["expires_at"])

        self.captain.approval_status = CaptainProfile.ApprovalStatus.APPROVED
        self.captain.save(update_fields=["approval_status"])

        vehicle = self.create_vehicle()
        vehicle.approval_status = Vehicle.ApprovalStatus.APPROVED
        vehicle.save(update_fields=["approval_status"])

        with self.assertRaises(ValidationError):
            set_captain_online_status(self.captain, True)

    def test_eligible_captain_can_go_offline(self):
        self.create_required_documents()
        self.captain.approval_status = CaptainProfile.ApprovalStatus.APPROVED
        self.captain.save(update_fields=["approval_status"])

        vehicle = self.create_vehicle()
        vehicle.approval_status = Vehicle.ApprovalStatus.APPROVED
        vehicle.save(update_fields=["approval_status"])

        set_captain_online_status(self.captain, True)
        set_captain_online_status(self.captain, False)

        self.captain.refresh_from_db()
        self.assertEqual(
            self.captain.operational_status,
            CaptainProfile.OperationalStatus.OFFLINE,
        )

    def test_rejecting_captain_resets_pending_vehicle_review(self):
        vehicle = self.create_vehicle()
        vehicle.approval_status = Vehicle.ApprovalStatus.APPROVED
        vehicle.save(update_fields=["approval_status"])

        reject_captain(
            self.captain,
            self.reviewer,
            "Required review failed",
        )

        vehicle.refresh_from_db()
        self.assertEqual(
            vehicle.approval_status,
            Vehicle.ApprovalStatus.PENDING,
        )


@override_settings(MEDIA_ROOT=TemporaryDirectory().name)
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


class CaptainDocumentUploadTests(TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

        self.document_field = CaptainDocument._meta.get_field("file")
        self.original_storage = self.document_field.storage

        self.document_field.storage = FileSystemStorage(
            location=self.temp_dir.name,
        )
        self.addCleanup(
            setattr,
            self.document_field,
            "storage",
            self.original_storage,
        )

        self.captain_user = User.objects.create_user(
            username="upload_captain",
            password="TestPassword-123!",
            role=User.Role.CAPTAIN,
        )
        self.captain = CaptainProfile.objects.create(
            user=self.captain_user,
        )

        self.other_user = User.objects.create_user(
            username="other_captain",
            password="TestPassword-123!",
            role=User.Role.CAPTAIN,
        )
        self.other_captain = CaptainProfile.objects.create(
            user=self.other_user,
        )

        self.url = reverse("accounts:captain_documents")
        self.client.force_login(self.captain_user)

    def make_upload(self, name="identity.jpg", content=None):
        if content is None:
            image = Image.new("RGB", (20, 20), color="white")
            buffer = BytesIO()
            image.save(buffer, format="JPEG")
            content = buffer.getvalue()

        return SimpleUploadedFile(
            name,
            content,
            content_type="image/jpeg",
        )

    def test_captain_can_upload_document(self):
        response = self.client.post(
            self.url,
            {
                "document_type": CaptainDocument.DocumentType.NATIONAL_ID,
                "file": self.make_upload(),
                "expires_at": "",
            },
        )

        self.assertRedirects(response, self.url)

        document = CaptainDocument.objects.get(captain=self.captain)

        self.assertEqual(
            document.review_status,
            CaptainDocument.ReviewStatus.PENDING,
        )
        self.assertEqual(
            document.document_type,
            CaptainDocument.DocumentType.NATIONAL_ID,
        )
        self.assertTrue(document.file.name)

    def test_invalid_file_extension_is_rejected(self):
        response = self.client.post(
            self.url,
            {
                "document_type": CaptainDocument.DocumentType.NATIONAL_ID,
                "file": self.make_upload("malware.exe"),
                "expires_at": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            CaptainDocument.objects.filter(captain=self.captain).exists()
        )
        self.assertContains(
            response,
            "Only PDF, JPG, JPEG, and PNG files are allowed.",
            status_code=200,
        )

    def test_file_larger_than_10_mb_is_rejected(self):
        large_content = b"x" * (10 * 1024 * 1024 + 1)

        response = self.client.post(
            self.url,
            {
                "document_type": CaptainDocument.DocumentType.NATIONAL_ID,
                "file": self.make_upload("large.jpg", large_content),
                "expires_at": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            CaptainDocument.objects.filter(captain=self.captain).exists()
        )

    def test_customer_cannot_upload_captain_documents(self):
        customer = User.objects.create_user(
            username="upload_customer",
            password="TestPassword-123!",
            role=User.Role.CUSTOMER,
        )
        self.client.force_login(customer)

        response = self.client.post(
            self.url,
            {
                "document_type": CaptainDocument.DocumentType.NATIONAL_ID,
                "file": self.make_upload(),
                "expires_at": "",
            },
        )

        self.assertRedirects(
            response,
            reverse("accounts:dashboard"),
        )
        self.assertFalse(CaptainDocument.objects.exists())

    def test_captain_only_sees_own_documents(self):
        CaptainDocument.objects.create(
            captain=self.other_captain,
            document_type=CaptainDocument.DocumentType.NATIONAL_ID,
            file="other-captain/identity.jpg",
        )

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            list(response.context["documents"]),
            [],
        )


    def test_expired_driving_license_is_rejected(self):
        form = CaptainDocumentForm(
            data={
                "document_type": CaptainDocument.DocumentType.DRIVING_LICENSE,
                "expires_at": (
                    timezone.localdate() - timedelta(days=1)
                ).isoformat(),
            },
            files={"file": self.make_upload()},
        )

        self.assertFalse(form.is_valid())
        self.assertIn("expires_at", form.errors)
        self.assertIn(
            "Expiration date cannot be in the past.",
            form.errors["expires_at"],
        )

    def test_future_vehicle_license_expiry_is_accepted(self):
        form = CaptainDocumentForm(
            data={
                "document_type": CaptainDocument.DocumentType.VEHICLE_LICENSE,
                "expires_at": (
                    timezone.localdate() + timedelta(days=30)
                ).isoformat(),
            },
            files={"file": self.make_upload()},
        )

        self.assertTrue(form.is_valid(), form.errors)


class CaptainWalletTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="wallet_test_captain",
            password="TestPassword123!",
            role=User.Role.CAPTAIN,
        )
        self.captain = CaptainProfile.objects.create(
            user=self.user,
        )
        self.wallet = CaptainWallet.objects.create(
            captain=self.captain,
            balance=Decimal("100.00"),
            reserved_balance=Decimal("20.00"),
        )

    def test_available_balance(self):
        self.assertEqual(
            self.wallet.available_balance,
            Decimal("80.00"),
        )

    def test_reserved_balance_cannot_be_negative(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                CaptainWallet.objects.filter(
                    pk=self.wallet.pk
                ).update(
                    reserved_balance=Decimal("-1.00")
                )

    def test_reserved_balance_cannot_exceed_balance(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                CaptainWallet.objects.filter(
                    pk=self.wallet.pk
                ).update(
                    reserved_balance=Decimal("101.00")
                )

    def test_reserved_balance_can_equal_balance(self):
        self.wallet.reserved_balance = Decimal("100.00")
        self.wallet.save(
            update_fields=["reserved_balance", "updated_at"]
        )
        self.wallet.refresh_from_db()

        self.assertEqual(
            self.wallet.available_balance,
            Decimal("0.00"),
        )

    def test_balance_cannot_be_negative(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                CaptainWallet.objects.filter(
                    pk=self.wallet.pk
                ).update(
                    balance=Decimal("-1.00")
                )

class CaptainLocationUpdateAPITests(TestCase):
    def setUp(self):
        self.client = Client()

        self.captain_user = User.objects.create_user(
            username="location_captain",
            password="TestPassword-123!",
            role=User.Role.CAPTAIN,
        )
        self.captain = CaptainProfile.objects.create(
            user=self.captain_user,
        )

        self.customer_user = User.objects.create_user(
            username="location_customer",
            password="TestPassword-123!",
            role=User.Role.CUSTOMER,
        )

        self.url = reverse("accounts:captain_location_update")

    def test_captain_can_update_location(self):
        self.client.force_login(self.captain_user)

        response = self.client.post(
            self.url,
            data={
                "latitude": "29.966800",
                "longitude": "32.549800",
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)

        self.captain.refresh_from_db()
        self.assertEqual(
            self.captain.current_latitude,
            Decimal("29.966800"),
        )
        self.assertEqual(
            self.captain.current_longitude,
            Decimal("32.549800"),
        )
        self.assertIsNotNone(self.captain.location_updated_at)
        self.assertEqual(
            response.json()["detail"],
            "Location updated successfully.",
        )

    def test_customer_cannot_update_captain_location(self):
        self.client.force_login(self.customer_user)

        response = self.client.post(
            self.url,
            data={
                "latitude": "29.966800",
                "longitude": "32.549800",
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)
        self.captain.refresh_from_db()
        self.assertIsNone(self.captain.current_latitude)
        self.assertIsNone(self.captain.current_longitude)

    def test_invalid_coordinates_are_rejected(self):
        self.client.force_login(self.captain_user)

        response = self.client.post(
            self.url,
            data={
                "latitude": "91",
                "longitude": "32.549800",
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.captain.refresh_from_db()
        self.assertIsNone(self.captain.current_latitude)

    def test_missing_coordinates_are_rejected(self):
        self.client.force_login(self.captain_user)

        response = self.client.post(
            self.url,
            data={"latitude": "29.966800"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)

    def test_anonymous_user_cannot_update_location(self):
        response = self.client.post(
            self.url,
            data={
                "latitude": "29.966800",
                "longitude": "32.549800",
            },
            content_type="application/json",
        )

        self.assertIn(response.status_code, (401, 403))

class CaptainDashboardWalletTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="dashboard_wallet_captain",
            password="TestPassword123!",
            role=User.Role.CAPTAIN,
        )
        self.captain = CaptainProfile.objects.create(
            user=self.user,
        )
        self.wallet = CaptainWallet.objects.create(
            captain=self.captain,
            balance=Decimal("100.00"),
            reserved_balance=Decimal("25.00"),
        )
        self.client.force_login(self.user)

    def test_dashboard_displays_wallet_balances(self):
        response = self.client.get("/accounts/dashboard/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "100.00")
        self.assertContains(response, "25.00")
        self.assertContains(response, "75.00")
