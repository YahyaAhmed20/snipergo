from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db import transaction

from trips.models import (
    Trip,
    TripFinancialRecord,
)


CENT = Decimal("0.01")


def calculate_commission(*, gross_fare, percentage):
    if not isinstance(gross_fare, Decimal):
        gross_fare = Decimal(str(gross_fare))

    if not isinstance(percentage, Decimal):
        percentage = Decimal(str(percentage))

    if not gross_fare.is_finite() or gross_fare < 0:
        raise ValidationError("Gross fare must be a finite non-negative amount.")

    if not percentage.is_finite() or not Decimal("0") <= percentage <= Decimal("100"):
        raise ValidationError("Commission percentage must be between 0 and 100.")

    commission = (gross_fare * percentage / Decimal("100")).quantize(
        CENT,
        rounding=ROUND_HALF_UP,
    )

    captain_earnings = (gross_fare - commission).quantize(
        CENT,
        rounding=ROUND_HALF_UP,
    )

    return commission, captain_earnings


@transaction.atomic
def create_trip_financial_record(*, trip_id):
    trip = Trip.objects.select_for_update().get(pk=trip_id)

    if trip.status != Trip.Status.COMPLETED:
        raise ValidationError(
            "Financial records can only be created for completed trips."
        )

    if trip.captain_id is None:
        raise ValidationError("The completed trip must have an assigned captain.")

    if trip.final_fare is None:
        raise ValidationError("The completed trip must have a final fare.")

    existing_record = TripFinancialRecord.objects.filter(
        trip=trip
    ).first()

    if existing_record:
        return existing_record

    if trip.commission_percentage is None:
        raise ValidationError(
            "The trip commission percentage has not been set."
        )

    commission, captain_earnings = calculate_commission(
        gross_fare=trip.final_fare,
        percentage=trip.commission_percentage,
    )

    return TripFinancialRecord.objects.create(
        trip=trip,
        gross_fare=trip.final_fare,
        commission_percentage=trip.commission_percentage,
        commission_amount=commission,
        captain_earnings=captain_earnings,
    )


@transaction.atomic
def confirm_cash_collection(*, trip_id, confirmed_by):
    trip = Trip.objects.select_for_update().get(pk=trip_id)

    if trip.status != Trip.Status.COMPLETED:
        raise ValidationError(
            "Cash collection can only be confirmed for completed trips."
        )

    if trip.payment_method != Trip.PaymentMethod.CASH:
        raise ValidationError(
            "This operation is only allowed for cash trips."
        )

    if confirmed_by.pk != getattr(trip.captain, "user_id", None):
        if not (
            confirmed_by.is_staff
            or confirmed_by.is_superuser
            or getattr(confirmed_by, "role", None) == "ADMIN"
        ):
            raise ValidationError(
                "Only the assigned captain or support can confirm collection."
            )

    record = TripFinancialRecord.objects.select_for_update().filter(
        trip=trip
    ).first()

    if record is None:
        raise ValidationError(
            "The trip financial record does not exist."
        )

    if (
        record.collection_status
        == TripFinancialRecord.CollectionStatus.REFUNDED
    ):
        raise ValidationError(
            "A refunded collection cannot be marked as collected."
        )

    if (
        record.collection_status
        == TripFinancialRecord.CollectionStatus.COLLECTED
    ):
        return record

    record.collection_status = (
        TripFinancialRecord.CollectionStatus.COLLECTED
    )
    record.save(update_fields=["collection_status"])

    return record