
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.models import WalletTopUpRequest
from trips.services.wallet import credit_wallet


@transaction.atomic
def approve_cash_topup(*, request_id, admin_user):
    if (
        not admin_user
        or not admin_user.is_authenticated
        or not admin_user.is_active
        or not admin_user.is_staff
        or not admin_user.has_perm(
            "accounts.change_wallettopuprequest"
        )
    ):
        raise PermissionDenied(
            "You are not authorized to approve cash deposits."
        )

    topup = (
        WalletTopUpRequest.objects
        .select_for_update()
        .get(pk=request_id)
    )

    if topup.method != WalletTopUpRequest.Method.CASH:
        raise ValidationError(
            "Only cash deposits can be approved through this service."
        )

    if topup.status != WalletTopUpRequest.Status.PENDING:
        raise ValidationError(
            "This top-up request has already been processed."
        )

    wallet = credit_wallet(
        captain=topup.captain,
        amount=topup.amount,
        reference=f"wallet-topup:{topup.reference}",
        description=f"Approved cash deposit: {topup.reference}",
    )

    topup.status = WalletTopUpRequest.Status.COMPLETED
    topup.reviewed_by = admin_user
    topup.reviewed_at = timezone.now()
    topup.save(
        update_fields=[
            "status",
            "reviewed_by",
            "reviewed_at",
            "updated_at",
        ]
    )

    return wallet




@transaction.atomic
def reject_cash_topup(*, request_id, admin_user, reason):
    if (
        not admin_user
        or not admin_user.is_authenticated
        or not admin_user.is_active
        or not admin_user.is_staff
        or not admin_user.has_perm(
            "accounts.change_wallettopuprequest"
        )
    ):
        raise PermissionDenied(
            "You are not authorized to reject cash deposits."
        )

    reason = (reason or "").strip()
    if len(reason) < 5 or len(reason) > 2000:
        raise ValidationError(
            "Rejection reason must be between 5 and 2000 characters."
        )

    topup = (
        WalletTopUpRequest.objects
        .select_for_update()
        .get(pk=request_id)
    )

    if topup.method != WalletTopUpRequest.Method.CASH:
        raise ValidationError(
            "Only cash deposits can be rejected through this service."
        )

    if topup.status != WalletTopUpRequest.Status.PENDING:
        raise ValidationError(
            "This top-up request has already been processed."
        )

    topup.status = WalletTopUpRequest.Status.REJECTED
    topup.reviewed_by = admin_user
    topup.reviewed_at = timezone.now()
    topup.rejection_reason = reason
    topup.save(
        update_fields=[
            "status",
            "reviewed_by",
            "reviewed_at",
            "rejection_reason",
            "updated_at",
        ]
    )

    return topup
