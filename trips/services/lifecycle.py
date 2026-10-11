from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.models import CaptainProfile
from trips.models import Trip
from vehicles.models import Vehicle


@transaction.atomic
def accept_trip(*, trip_id, captain):
    trip = Trip.objects.select_for_update().get(pk=trip_id)
    captain = CaptainProfile.objects.select_for_update().get(
        pk=captain.pk
    )

    if trip.status != Trip.Status.REQUESTED:
        raise ValidationError("This trip is no longer available.")

    if captain.approval_status != CaptainProfile.ApprovalStatus.APPROVED:
        raise ValidationError("Captain is not approved.")

    if captain.operational_status != CaptainProfile.OperationalStatus.ONLINE:
        raise ValidationError("Captain must be online.")

    vehicle = Vehicle.objects.filter(
        captain=captain,
        approval_status=Vehicle.ApprovalStatus.APPROVED,
    ).first()

    if vehicle is None:
        raise ValidationError("Captain has no approved vehicle.")

    trip.captain = captain
    trip.vehicle = vehicle
    trip.status = Trip.Status.ACCEPTED
    trip.accepted_at = timezone.now()

    trip.save(
        update_fields=[
            "captain",
            "vehicle",
            "status",
            "accepted_at",
            "updated_at",
        ]
    )

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
    return trip


@transaction.atomic
def complete_trip(*, trip_id, captain, final_fare):
    trip = Trip.objects.select_for_update().get(pk=trip_id)

    if trip.captain_id != captain.pk:
        raise ValidationError("This trip is not assigned to this captain.")

    if trip.status != Trip.Status.IN_PROGRESS:
        raise ValidationError(
            "Only trips in progress can be completed."
        )

    if final_fare is None or final_fare < 0:
        raise ValidationError("Final fare must be zero or greater.")

    trip.status = Trip.Status.COMPLETED
    trip.final_fare = final_fare
    trip.completed_at = timezone.now()
    trip.save(
        update_fields=[
            "status",
            "final_fare",
            "completed_at",
            "updated_at",
        ]
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
    return trip