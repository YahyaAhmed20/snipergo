from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from accounts.models import CaptainProfile
from accounts.services.reviews import validate_captain_documents
from trips.models import CaptainOffer, CommissionConfig, Trip
from trips.services.negotiation import calculate_negotiation_range
from trips.services.wallet import reserve_trip_commission


OFFER_EXPIRY_MINUTES = 2


@transaction.atomic
def create_captain_offer(*, trip_id, captain, amount):
    trip = Trip.objects.select_for_update().get(pk=trip_id)

    captain = CaptainProfile.objects.select_for_update().get(
        pk=captain.pk
    )

    if trip.status != Trip.Status.REQUESTED or trip.captain_id is not None:
        raise ValidationError("This trip is no longer available.")

    if captain.approval_status != CaptainProfile.ApprovalStatus.APPROVED:
        raise ValidationError("Captain is not approved.")

    if captain.operational_status != CaptainProfile.OperationalStatus.ONLINE:
        raise ValidationError("Captain must be online to submit an offer.")

    if not captain.vehicles.filter(
        approval_status="APPROVED"
    ).exists():
        raise ValidationError("Captain needs an approved vehicle.")

    try:
        validate_captain_documents(captain)
    except ValidationError:
        raise ValidationError("Captain's required documents are not valid.")

    if trip.estimated_fare is None:
        raise ValidationError("Trip has no estimated fare.")

    try:
        offer_amount = Decimal(str(amount))
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationError("Offer amount must be a valid number.")

    if not offer_amount.is_finite() or offer_amount < 0:
        raise ValidationError("Offer amount must be finite and non-negative.")

    if offer_amount != offer_amount.quantize(Decimal("0.01")):
        raise ValidationError(
            "Offer amount cannot have more than two decimal places."
        )

    if offer_amount >= Decimal("100000000"):
        raise ValidationError(
            "Offer amount exceeds the allowed maximum."
        )

    minimum_offer, maximum_offer = calculate_negotiation_range(
        city="Suez",
        estimated_fare=trip.estimated_fare,
    )

    if not minimum_offer <= offer_amount <= maximum_offer:
        raise ValidationError(
            f"Offer must be between {minimum_offer} and {maximum_offer}."
        )

    if CaptainOffer.objects.filter(
        trip=trip,
        captain=captain,
        status=CaptainOffer.Status.PENDING,
    ).exists():
        raise ValidationError(
            "You already have a pending offer for this trip."
        )

    try:
        with transaction.atomic():
            offer = CaptainOffer.objects.create(
                trip=trip,
                captain=captain,
                amount=offer_amount.quantize(Decimal("0.01")),
                expires_at=timezone.now() + timedelta(
                    minutes=OFFER_EXPIRY_MINUTES
                ),
            )
    except IntegrityError:
        if CaptainOffer.objects.filter(
            trip=trip,
            captain=captain,
            status=CaptainOffer.Status.PENDING,
        ).exists():
            raise ValidationError(
                "You already have a pending offer for this trip."
            )
        raise

    return offer


def accept_captain_offer(*, offer_id, customer):
    # Expire an outdated offer in a committed transaction first.
    if expire_offer_if_needed(offer_id=offer_id):
        raise ValidationError("This offer has expired.")

    with transaction.atomic():
        offer_ref = CaptainOffer.objects.only("trip_id").get(pk=offer_id)

        trip = Trip.objects.select_for_update().get(
            pk=offer_ref.trip_id
        )
        offer = CaptainOffer.objects.select_for_update().get(
            pk=offer_id
        )

        # Check ownership before exposing offer state.
        if trip.customer_id != customer.pk:
            raise ValidationError(
                "You cannot accept an offer for this trip."
            )

        if trip.status != Trip.Status.REQUESTED or trip.captain_id is not None:
            raise ValidationError("This trip is no longer available.")

        if offer.status != CaptainOffer.Status.PENDING:
            raise ValidationError("This offer is no longer pending.")

        now = timezone.now()

        if offer.expires_at <= now:
            # Persist expiration outside the transaction that is about
            # to reject acceptance.
            pass_expiration = True
        else:
            pass_expiration = False

        if not pass_expiration:
            captain = CaptainProfile.objects.select_for_update().get(
                pk=offer.captain_id
            )

            if (
                captain.approval_status
                != CaptainProfile.ApprovalStatus.APPROVED
                or captain.operational_status
                != CaptainProfile.OperationalStatus.ONLINE
            ):
                raise ValidationError("Captain is no longer eligible.")

            try:
                validate_captain_documents(captain)
            except ValidationError:
                raise ValidationError(
                    "Captain's required documents are not valid."
                )

            vehicle = captain.vehicles.filter(
                approval_status="APPROVED"
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

            trip.captain = captain
            trip.vehicle = vehicle
            trip.agreed_fare = offer.amount
            trip.commission_percentage = commission_config.percentage
            trip.status = Trip.Status.ACCEPTED
            trip.accepted_at = now
            trip.save(
                update_fields=[
                    "captain",
                    "vehicle",
                    "agreed_fare",
                    "commission_percentage",
                    "status",
                    "accepted_at",
                    "updated_at",
                ]
            )

            offer.status = CaptainOffer.Status.ACCEPTED
            offer.responded_at = now
            offer.save(
                update_fields=["status", "responded_at", "updated_at"]
            )

            reserve_trip_commission(trip_id=trip.pk)

            CaptainOffer.objects.filter(
                trip=trip,
                status=CaptainOffer.Status.PENDING,
            ).exclude(pk=offer.pk).update(
                status=CaptainOffer.Status.REJECTED,
                responded_at=now,
            )

            return offer

    # The atomic block has ended, so expiration can be committed safely.
    if pass_expiration:
        expire_offer_if_needed(offer_id=offer_id)
        raise ValidationError("This offer has expired.")


def expire_offer_if_needed(*, offer_id):
    with transaction.atomic():
        offer = CaptainOffer.objects.select_for_update().get(pk=offer_id)

        if (
            offer.status == CaptainOffer.Status.PENDING
            and offer.expires_at <= timezone.now()
        ):
            offer.status = CaptainOffer.Status.EXPIRED
            offer.responded_at = timezone.now()
            offer.save(
                update_fields=["status", "responded_at", "updated_at"]
            )
            return True

    return False