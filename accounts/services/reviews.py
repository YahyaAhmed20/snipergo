
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.models import CaptainDocument, CaptainProfile
from vehicles.models import Vehicle


REQUIRED_DOCUMENTS = {
    CaptainDocument.DocumentType.NATIONAL_ID,
    CaptainDocument.DocumentType.DRIVING_LICENSE,
    CaptainDocument.DocumentType.VEHICLE_LICENSE,
}


def validate_captain_documents(captain):
    documents = captain.documents.filter(
        document_type__in=REQUIRED_DOCUMENTS,
        review_status=CaptainDocument.ReviewStatus.APPROVED,
    )

    approved_types = set(
        documents.values_list("document_type", flat=True)
    )

    missing = REQUIRED_DOCUMENTS - approved_types
    if missing:
        raise ValidationError(
            "Required documents are missing or not approved: "
            + ", ".join(sorted(missing))
        )

    if documents.filter(
        expires_at__lt=timezone.localdate()
    ).exists():
        raise ValidationError(
            "One or more required documents have expired."
        )


@transaction.atomic
def approve_captain(captain, reviewer):
    validate_captain_documents(captain)

    captain.approval_status = CaptainProfile.ApprovalStatus.APPROVED
    captain.rejection_reason = ""
    captain.reviewed_at = timezone.now()
    captain.reviewed_by = reviewer
    captain.operational_status = CaptainProfile.OperationalStatus.OFFLINE

    captain.save(
        update_fields=[
            "approval_status",
            "rejection_reason",
            "reviewed_at",
            "reviewed_by",
            "operational_status",
            "updated_at",
        ]
    )


@transaction.atomic
def reject_captain(captain, reviewer, reason):
    reason = reason.strip()
    if not reason:
        raise ValidationError("A rejection reason is required.")

    captain.approval_status = CaptainProfile.ApprovalStatus.REJECTED
    captain.rejection_reason = reason
    captain.reviewed_at = timezone.now()
    captain.reviewed_by = reviewer
    captain.operational_status = CaptainProfile.OperationalStatus.OFFLINE

    captain.save(
        update_fields=[
            "approval_status",
            "rejection_reason",
            "reviewed_at",
            "reviewed_by",
            "operational_status",
            "updated_at",
        ]
    )

    Vehicle.objects.filter(captain=captain).exclude(
        approval_status=Vehicle.ApprovalStatus.REJECTED
    ).update(
        approval_status=Vehicle.ApprovalStatus.PENDING,
        rejection_reason="",
        reviewed_at=None,
        reviewed_by=None,
    )


@transaction.atomic
def approve_vehicle(vehicle, reviewer):
    captain = vehicle.captain

    if captain.approval_status != CaptainProfile.ApprovalStatus.APPROVED:
        raise ValidationError("The captain must be approved first.")

    validate_captain_documents(captain)

    vehicle.approval_status = Vehicle.ApprovalStatus.APPROVED
    vehicle.rejection_reason = ""
    vehicle.reviewed_at = timezone.now()
    vehicle.reviewed_by = reviewer

    vehicle.save(
        update_fields=[
            "approval_status",
            "rejection_reason",
            "reviewed_at",
            "reviewed_by",
            "updated_at",
        ]
    )


@transaction.atomic
def reject_vehicle(vehicle, reviewer, reason):
    reason = reason.strip()
    if not reason:
        raise ValidationError("A rejection reason is required.")

    vehicle.approval_status = Vehicle.ApprovalStatus.REJECTED
    vehicle.rejection_reason = reason
    vehicle.reviewed_at = timezone.now()
    vehicle.reviewed_by = reviewer

    vehicle.save(
        update_fields=[
            "approval_status",
            "rejection_reason",
            "reviewed_at",
            "reviewed_by",
            "updated_at",
        ]
    )



@transaction.atomic
def approve_document(document, reviewer):
    document = CaptainDocument.objects.select_for_update().get(
        pk=document.pk
    )

    if (
        document.expires_at
        and document.expires_at < timezone.localdate()
    ):
        raise ValidationError(
            "Cannot approve an expired document."
        )

    document.review_status = CaptainDocument.ReviewStatus.APPROVED
    document.rejection_reason = ""
    document.reviewed_at = timezone.now()
    document.reviewed_by = reviewer

    document.save(
        update_fields=[
            "review_status",
            "rejection_reason",
            "reviewed_at",
            "reviewed_by",
        ]
    )


@transaction.atomic
def reject_document(document, reviewer, reason):
    reason = reason.strip()

    if not reason:
        raise ValidationError(
            "A rejection reason is required."
        )

    document = CaptainDocument.objects.select_for_update().get(
        pk=document.pk
    )

    document.review_status = CaptainDocument.ReviewStatus.REJECTED
    document.rejection_reason = reason
    document.reviewed_at = timezone.now()
    document.reviewed_by = reviewer

    document.save(
        update_fields=[
            "review_status",
            "rejection_reason",
            "reviewed_at",
            "reviewed_by",
        ]
    )



@transaction.atomic
def set_captain_online_status(captain, is_online):
    captain = CaptainProfile.objects.select_for_update().get(
        pk=captain.pk
    )

    if is_online:
        if captain.approval_status != CaptainProfile.ApprovalStatus.APPROVED:
            raise ValidationError(
                "Captain must be approved before going online."
            )

        validate_captain_documents(captain)

        approved_vehicle_exists = Vehicle.objects.filter(
            captain=captain,
            approval_status=Vehicle.ApprovalStatus.APPROVED,
        ).exists()

        if not approved_vehicle_exists:
            raise ValidationError(
                "Captain must have an approved vehicle before going online."
            )

        captain.operational_status = CaptainProfile.OperationalStatus.ONLINE

    else:
        captain.operational_status = CaptainProfile.OperationalStatus.OFFLINE

    captain.save(
        update_fields=["operational_status", "updated_at"]
    )

    return captain
