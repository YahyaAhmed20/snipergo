from asgiref.sync import async_to_sync
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from datetime import timedelta

from trips.services.offers import TRIP_REQUEST_EXPIRY_MINUTES
from trips.services.wallet import (
    deduct_trip_commission,
    release_trip_commission_reservation,
    reserve_trip_commission,
    InsufficientCommissionBalance,
)
from accounts.models import CaptainProfile, CaptainWallet, Notification, User
from accounts.services.notifications import (
    create_notification,
    dispatch_notification_websocket,
)
from accounts.services.reviews import validate_captain_documents
from trips.models import CommissionConfig, FinancialTransaction, Trip
from trips.services.finance import create_trip_financial_record
from vehicles.models import Vehicle
from decimal import Decimal, InvalidOperation


def accept_trip(*, trip_id, captain):
    from trips.services.offers import (
        expire_trip_if_needed,
        TRIP_REQUEST_EXPIRY_MINUTES,
        _ExpiredTripRequest,
    )

    if expire_trip_if_needed(trip_id=trip_id):
        raise ValidationError("This trip has expired.")

    try:
        return _accept_trip_atomic(
            trip_id=trip_id,
            captain=captain,
        )
    except _ExpiredTripRequest:
        if expire_trip_if_needed(trip_id=trip_id):
            raise ValidationError("This trip has expired.")
        raise ValidationError("This trip is no longer available.")


@transaction.atomic
def _accept_trip_atomic(*, trip_id, captain):
    from trips.services.offers import (
        TRIP_REQUEST_EXPIRY_MINUTES,
        _ExpiredTripRequest,
    )

    trip = Trip.objects.select_for_update().get(pk=trip_id)
    captain = CaptainProfile.objects.select_for_update().get(
        pk=captain.pk
    )

    if (
        trip.status == Trip.Status.REQUESTED
        and trip.captain_id is None
        and trip.requested_at
        <= timezone.now()
        - timedelta(minutes=TRIP_REQUEST_EXPIRY_MINUTES)
    ):
        raise _ExpiredTripRequest(trip.pk)

    if (
        trip.status != Trip.Status.REQUESTED
        or trip.captain_id is not None
    ):
        raise ValidationError("This trip is no longer available.")

    if captain.approval_status != CaptainProfile.ApprovalStatus.APPROVED:
        raise ValidationError("Captain is not approved.")

    if captain.operational_status != CaptainProfile.OperationalStatus.ONLINE:
        raise ValidationError("Captain must be online.")

    try:
        validate_captain_documents(captain)
    except ValidationError:
        raise ValidationError(
            "Captain's required documents are not valid."
        )

    vehicle = Vehicle.objects.filter(
        captain=captain,
        approval_status=Vehicle.ApprovalStatus.APPROVED,
    ).first()

    if vehicle is None:
        raise ValidationError("Captain has no approved vehicle.")

    commission_config = CommissionConfig.objects.filter(
        is_active=True
    ).order_by("id").first()

    if commission_config is None:
        raise ValidationError(
            "No active commission configuration exists."
        )

    if trip.estimated_fare is None:
        raise ValidationError("The trip has no estimated fare.")

    if (
        not trip.estimated_fare.is_finite()
        or trip.estimated_fare <= 0
        or trip.estimated_fare >= Decimal("100000000")
    ):
        raise ValidationError("The estimated fare is invalid.")

    trip.commission_percentage = commission_config.percentage
    trip.captain = captain
    trip.vehicle = vehicle
    trip.agreed_fare = trip.estimated_fare
    trip.status = Trip.Status.ACCEPTED
    trip.accepted_at = timezone.now()

    trip.save(
        update_fields=[
            "captain",
            "vehicle",
            "agreed_fare",
            "status",
            "accepted_at",
            "commission_percentage",
            "updated_at",
        ]
    )

    reserve_trip_commission(trip_id=trip.pk)
    return trip


@transaction.atomic
def mark_captain_arriving(*, trip_id, captain):
    trip = Trip.objects.select_for_update().get(pk=trip_id)

    if trip.captain_id != captain.pk:
        raise ValidationError("This trip is not assigned to this captain.")

    if trip.status != Trip.Status.ACCEPTED:
        raise ValidationError(
            "Only accepted trips can be marked as arriving."
        )

    trip.status = Trip.Status.ARRIVING
    trip.save(update_fields=["status", "updated_at"])

    # Notify customer of status change
    notification = create_notification(
        user=trip.customer.user,
        type=Notification.Type.TRIP_STATUS_CHANGED,
        title="تحديث حالة الرحلة",
        message=f"الرحلة #{trip.pk}: الكابتن جاري الوصول",
        payload={
            "trip_id": trip.pk,
            "status": Trip.Status.ARRIVING,
        },
    )
    transaction.on_commit(
        lambda n=notification: async_to_sync(dispatch_notification_websocket)(n)
    )
    return trip


@transaction.atomic
def mark_captain_arrived(*, trip_id, captain):
    trip = Trip.objects.select_for_update().get(pk=trip_id)

    if trip.captain_id != captain.pk:
        raise ValidationError("This trip is not assigned to this captain.")

    if trip.status != Trip.Status.ARRIVING:
        raise ValidationError(
            "The captain must be on the way before arriving."
        )

    trip.status = Trip.Status.ARRIVED
    trip.save(update_fields=["status", "updated_at"])

    # Notify customer of status change
    notification = create_notification(
        user=trip.customer.user,
        type=Notification.Type.TRIP_STATUS_CHANGED,
        title="تحديث حالة الرحلة",
        message=f"الرحلة #{trip.pk}: الكابتن وصل",
        payload={
            "trip_id": trip.pk,
            "status": Trip.Status.ARRIVED,
        },
    )
    transaction.on_commit(
        lambda n=notification: async_to_sync(dispatch_notification_websocket)(n)
    )
    return trip


@transaction.atomic
def start_trip(*, trip_id, captain):
    trip = Trip.objects.select_for_update().get(pk=trip_id)

    if trip.captain_id != captain.pk:
        raise ValidationError("This trip is not assigned to this captain.")

    if trip.status != Trip.Status.ARRIVED:
        raise ValidationError(
            "The trip can only start after the captain arrives."
        )

    trip.status = Trip.Status.IN_PROGRESS
    trip.started_at = timezone.now()
    trip.save(
        update_fields=["status", "started_at", "updated_at"]
    )

    # Notify customer of status change
    notification = create_notification(
        user=trip.customer.user,
        type=Notification.Type.TRIP_STATUS_CHANGED,
        title="تحديث حالة الرحلة",
        message=f"الرحلة #{trip.pk}: بدأت",
        payload={
            "trip_id": trip.pk,
            "status": Trip.Status.IN_PROGRESS,
        },
    )
    transaction.on_commit(
        lambda n=notification: async_to_sync(dispatch_notification_websocket)(n)
    )
    return trip


@transaction.atomic
def complete_trip(*, trip_id, captain, final_fare=None):
    trip = Trip.objects.select_for_update().get(pk=trip_id)

    if trip.captain_id != captain.pk:
        raise ValidationError("This trip is not assigned to this captain.")

    if trip.status != Trip.Status.IN_PROGRESS:
        raise ValidationError(
            "Only trips in progress can be completed."
        )

    if trip.agreed_fare is None:
        raise ValidationError(
            "The trip cannot be completed without an agreed fare."
        )

    agreed_fare = trip.agreed_fare

    if (
        not agreed_fare.is_finite()
        or agreed_fare < 0
        or agreed_fare >= Decimal("100000000")
    ):
        raise ValidationError("The agreed fare is invalid.")

    trip.status = Trip.Status.COMPLETED
    trip.final_fare = agreed_fare
    trip.completed_at = timezone.now()

    trip.save(
        update_fields=[
            "status",
            "final_fare",
            "completed_at",
            "updated_at",
        ]
    )

    record = create_trip_financial_record(trip_id=trip.pk)

    try:
        with transaction.atomic():
            deduct_trip_commission(
                trip_financial_record_id=record.pk,
            )
    except InsufficientCommissionBalance:
        wallet = (
            CaptainWallet.objects.select_for_update()
            .filter(captain=trip.captain)
            .first()
        )

        if wallet is None:
            raise ValidationError("The captain wallet does not exist.")

        reference = f"trip-commission-due:{record.pk}"
        existing_due = FinancialTransaction.objects.filter(
            reference=reference
        ).first()

        if existing_due is None:
            FinancialTransaction.objects.create(
                captain=trip.captain,
                wallet=wallet,
                financial_record=record,
                transaction_type=(
                    FinancialTransaction.TransactionType.COMMISSION_DUE
                ),
                amount=record.commission_amount,
                reference=reference,
                description=f"Outstanding commission for trip #{trip.pk}",
            )
        elif (
            existing_due.captain_id != trip.captain_id
            or existing_due.wallet_id != wallet.pk
            or existing_due.financial_record_id != record.pk
            or existing_due.transaction_type
            != FinancialTransaction.TransactionType.COMMISSION_DUE
            or existing_due.amount != record.commission_amount
        ):
            raise ValidationError(
                "The outstanding commission transaction is inconsistent."
            )

    # Notify customer of trip completion
    notification = create_notification(
        user=trip.customer.user,
        type=Notification.Type.TRIP_STATUS_CHANGED,
        title="تحديث حالة الرحلة",
        message=f"الرحلة #{trip.pk}: تم الإكمال",
        payload={
            "trip_id": trip.pk,
            "status": Trip.Status.COMPLETED,
        },
    )
    transaction.on_commit(
        lambda n=notification: async_to_sync(dispatch_notification_websocket)(n)
    )

    return trip


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
        raise ValidationError("This trip can no longer be cancelled.")

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

    if trip.captain_id is not None:
        release_trip_commission_reservation(trip_id=trip.pk)

    # Notify the non-actor party of trip cancellation.
    # Never notify the actor who initiated the cancellation.
    recipient_users = []
    if is_customer and trip.captain_id is not None:
        recipient_users.append(trip.captain.user)
    elif is_captain:
        recipient_users.append(trip.customer.user)
    elif is_support:
        recipient_users.append(trip.customer.user)
        if trip.captain_id is not None:
            recipient_users.append(trip.captain.user)

    for recipient_user in recipient_users:
        notification = create_notification(
            user=recipient_user,
            type=Notification.Type.TRIP_STATUS_CHANGED,
            title="تحديث حالة الرحلة",
            message=f"الرحلة #{trip.pk}: تم الإلغاء",
            payload={
                "trip_id": trip.pk,
                "status": Trip.Status.CANCELLED,
                "cancelled_by": cancelled_by.pk,
            },
        )
        transaction.on_commit(
            lambda n=notification: async_to_sync(dispatch_notification_websocket)(n)
        )

    return trip