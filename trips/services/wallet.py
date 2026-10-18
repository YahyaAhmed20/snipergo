from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.models import CaptainWallet
from trips.models import (
    FinancialTransaction,
    TripFinancialRecord,
)


CENT = Decimal("0.01")

def _normalize_amount(amount):
    try:
        amount = Decimal(str(amount))
    except (TypeError, ValueError, ArithmeticError):
        raise ValidationError("Amount must be a valid decimal.")

    if not amount.is_finite() or amount <= 0:
        raise ValidationError("Amount must be greater than zero.")

    try:
        amount = amount.quantize(CENT, rounding=ROUND_HALF_UP)
    except ArithmeticError:
        raise ValidationError("Amount exceeds the allowed maximum.")

    if amount <= 0:
        raise ValidationError(
            "Amount must be at least 0.01 EGP."
        )

    if amount > Decimal("99999999.99"):
        raise ValidationError("Amount exceeds the allowed maximum.")

    return amount

@transaction.atomic
def credit_wallet(*, captain, amount, reference, description=""):
    """
    Credit a wallet after an independently verified payment
    or an approved cash deposit.

    Call only from trusted server-side workflows.
    """
    amount = _normalize_amount(amount)

    if not reference or not reference.strip():
        raise ValidationError("A transaction reference is required.")

    # Lock the captain row to serialize wallet creation and credits
    # for this captain on databases supporting select_for_update().
    from accounts.models import CaptainProfile

    captain = CaptainProfile.objects.select_for_update().get(
        pk=captain.pk
    )

    wallet, _ = CaptainWallet.objects.get_or_create(
        captain=captain,
        defaults={"balance": Decimal("0.00")},
    )

    wallet = CaptainWallet.objects.select_for_update().get(
        pk=wallet.pk
    )

    existing = FinancialTransaction.objects.filter(
        reference=reference
    ).first()

    if existing:
        if (
            existing.wallet_id != wallet.pk
            or existing.captain_id != captain.pk
            or existing.transaction_type
            != FinancialTransaction.TransactionType.WALLET_TOPUP
            or existing.amount != amount
        ):
            raise ValidationError(
                "This reference has already been used."
            )

        return wallet

    wallet.balance += amount
    wallet.save(update_fields=["balance", "updated_at"])

    FinancialTransaction.objects.create(
        captain=captain,
        wallet=wallet,
        transaction_type=(
            FinancialTransaction.TransactionType.WALLET_TOPUP
        ),
        amount=amount,
        reference=reference,
        description=description,
    )

    return wallet


@transaction.atomic
def deduct_trip_commission(*, trip_financial_record_id):
    """
    Settle a completed trip's commission exactly once.
    Consume the reserved amount first and charge only any extra
    commission from the available wallet balance.
    """
    record = (
        TripFinancialRecord.objects.select_for_update(of=("self",))
        .select_related("trip", "trip__captain")
        .get(pk=trip_financial_record_id)
    )
    trip = record.trip

    if trip.status != trip.Status.COMPLETED:
        raise ValidationError("Only completed trips can be charged.")

    if trip.captain_id is None:
        raise ValidationError("The trip has no assigned captain.")

    wallet = CaptainWallet.objects.select_for_update().filter(
        captain=trip.captain
    ).first()

    if wallet is None:
        raise ValidationError("The captain wallet does not exist.")

    reserve_reference = f"trip-commission-reserve:{trip.pk}"
    release_reference = f"trip-commission-release:{trip.pk}"


    reservation = FinancialTransaction.objects.filter(
        reference=reserve_reference
    ).first()

    if reservation and (
        reservation.wallet_id != wallet.pk
        or reservation.captain_id != trip.captain_id
        or reservation.transaction_type
        != FinancialTransaction.TransactionType.COMMISSION_RESERVE
    ):
        raise ValidationError(
            "The commission reservation is inconsistent."
        )


    released = FinancialTransaction.objects.filter(
        reference=release_reference
    ).first()

    reference = f"trip-commission:{record.pk}"
    existing = FinancialTransaction.objects.filter(
        reference=reference
    ).first()

    if released:
        if (
            released.wallet_id != wallet.pk
            or released.captain_id != trip.captain_id
            or released.transaction_type
            != FinancialTransaction.TransactionType.COMMISSION_RELEASE
        ):
            raise ValidationError(
                "The reservation release is inconsistent."
            )

        raise ValidationError(
            "Cannot deduct commission after its reservation was released."
        )

    if existing:
        if (
            existing.wallet_id != wallet.pk
            or existing.captain_id != trip.captain_id
            or existing.financial_record_id != record.pk
            or existing.transaction_type
            != FinancialTransaction.TransactionType.COMMISSION_DEDUCTION
            or existing.amount != record.commission_amount
        ):
            raise ValidationError(
                "The commission transaction is inconsistent."
            )
        return wallet

    if record.commission_amount == Decimal("0.00"):
        if reservation:
            if reservation.amount <= Decimal("0.00"):
                raise ValidationError(
                    "The commission reservation is inconsistent."
                )

            release_reference = (
                f"trip-commission-release-zero:{trip.pk}"
            )
            existing_release = FinancialTransaction.objects.filter(
                reference=release_reference
            ).first()

            if existing_release:
                if (
                    existing_release.wallet_id != wallet.pk
                    or existing_release.captain_id != trip.captain_id
                    or existing_release.transaction_type
                    != FinancialTransaction.TransactionType.COMMISSION_RELEASE
                    or existing_release.amount != reservation.amount
                ):
                    raise ValidationError(
                        "The zero-commission release is inconsistent."
                    )
            else:
                if wallet.reserved_balance < reservation.amount:
                    raise ValidationError("Reserved balance is inconsistent.")

                wallet.reserved_balance -= reservation.amount
                wallet.save(
                    update_fields=["reserved_balance", "updated_at"]
                )

                FinancialTransaction.objects.create(
                    captain=trip.captain,
                    wallet=wallet,
                    transaction_type=(
                        FinancialTransaction.TransactionType.COMMISSION_RELEASE
                    ),
                    amount=reservation.amount,
                    reference=release_reference,
                    description=(
                        f"Zero-commission reservation released for trip #{trip.pk}"
                    ),
                )

        record.commission_status = (
            TripFinancialRecord.CommissionStatus.COLLECTED
        )
        record.save(update_fields=["commission_status"])
        return wallet

    reserved_amount = (
        reservation.amount if reservation else Decimal("0.00")
    )

    if wallet.reserved_balance < reserved_amount:
        raise ValidationError("Reserved balance is inconsistent.")

    extra_amount = max(
        record.commission_amount - reserved_amount,
        Decimal("0.00"),
    )

    if wallet.available_balance < extra_amount:
        raise ValidationError(
            "Insufficient available wallet balance to settle commission."
        )

    wallet.reserved_balance -= reserved_amount
    wallet.balance -= record.commission_amount

    if wallet.balance < 0:
        raise ValidationError(
            "Insufficient wallet balance to pay trip commission."
        )

    excess_reservation = max(
        reserved_amount - record.commission_amount,
        Decimal("0.00"),
    )

    wallet.save(
        update_fields=["balance", "reserved_balance", "updated_at"]
    )

    if excess_reservation > 0:
        FinancialTransaction.objects.create(
            captain=trip.captain,
            wallet=wallet,
            transaction_type=(
                FinancialTransaction.TransactionType.COMMISSION_RELEASE
            ),
            amount=excess_reservation,
            reference=f"trip-commission-release-excess:{trip.pk}",
            description=(
                f"Excess commission reservation released for trip #{trip.pk}"
            ),
        )

    FinancialTransaction.objects.create(
        captain=trip.captain,
        wallet=wallet,
        financial_record=record,
        transaction_type=(
            FinancialTransaction.TransactionType.COMMISSION_DEDUCTION
        ),
        amount=record.commission_amount,
        reference=reference,
        description=f"Commission for trip #{record.trip_id}",
    )

    record.commission_status = (
        TripFinancialRecord.CommissionStatus.COLLECTED
    )
    record.save(update_fields=["commission_status"])

    return wallet


@transaction.atomic
def reserve_trip_commission(*, trip_id):
    """
    Reserve the estimated commission for an accepted trip.
    This is a reservation, not a deduction.
    """
    from trips.models import Trip, CommissionConfig

    trip = Trip.objects.select_for_update(of=("self",)).select_related(
        "captain"
    ).get(pk=trip_id)

    if trip.status != Trip.Status.ACCEPTED:
        raise ValidationError("Only accepted trips can reserve commission.")

    if trip.captain_id is None:
        raise ValidationError("The trip has no assigned captain.")

    if trip.estimated_fare is None or trip.estimated_fare <= 0:
        raise ValidationError("The trip must have a valid estimated fare.")

    if trip.commission_percentage is None:
        raise ValidationError(
            "The trip commission percentage has not been set."
        )

    commission = (
        trip.estimated_fare
        * trip.commission_percentage
        / Decimal("100")
    ).quantize(CENT, rounding=ROUND_HALF_UP)

    wallet = CaptainWallet.objects.select_for_update().filter(
        captain=trip.captain
    ).first()

    if wallet is None:
        raise ValidationError("The captain wallet does not exist.")

    if commission == Decimal("0.00"):
        return wallet

    reference = f"trip-commission-reserve:{trip.pk}"
    release_reference = f"trip-commission-release:{trip.pk}"

    existing = FinancialTransaction.objects.filter(
        reference=reference
    ).first()

    released = FinancialTransaction.objects.filter(
        reference=release_reference
    ).first()

    deducted = FinancialTransaction.objects.filter(
        financial_record__trip_id=trip.pk,
        transaction_type=FinancialTransaction.TransactionType.COMMISSION_DEDUCTION,
    ).exists()

    if released or deducted:
        raise ValidationError(
            "Cannot reuse a reservation that was released or deducted."
        )

    if existing:
        if (
            existing.wallet_id != wallet.pk
            or existing.captain_id != trip.captain_id
            or existing.transaction_type
            != FinancialTransaction.TransactionType.COMMISSION_RESERVE
            or existing.amount != commission
        ):
            raise ValidationError(
                "The commission reservation is inconsistent."
            )

        return wallet

    if wallet.available_balance < commission:
        raise ValidationError("Insufficient available wallet balance.")

    wallet.reserved_balance += commission
    wallet.save(update_fields=["reserved_balance", "updated_at"])

    FinancialTransaction.objects.create(
        captain=trip.captain,
        wallet=wallet,
        transaction_type=FinancialTransaction.TransactionType.COMMISSION_RESERVE,
        amount=commission,
        reference=reference,
        description=f"Commission reservation for trip #{trip.pk}",
    )

    return wallet


@transaction.atomic
def release_trip_commission_reservation(*, trip_id):
    """
    Release a trip's reserved commission when the trip is cancelled.
    """
    from trips.models import Trip

    trip = Trip.objects.select_for_update(of=("self",)).get(pk=trip_id)

    if trip.status != Trip.Status.CANCELLED:
        raise ValidationError("Only cancelled trips can release reservations.")

    deducted = FinancialTransaction.objects.filter(
        financial_record__trip_id=trip.pk,
        transaction_type=FinancialTransaction.TransactionType.COMMISSION_DEDUCTION,
    ).exists()

    if deducted:
        raise ValidationError(
            "Cannot release a reservation after commission deduction."
        )

    wallet = CaptainWallet.objects.select_for_update().filter(
        captain=trip.captain
    ).first()

    if wallet is None:
        raise ValidationError("The captain wallet does not exist.")

    reference = f"trip-commission-release:{trip.pk}"

    reserve_reference = f"trip-commission-reserve:{trip.pk}"


    reservation = FinancialTransaction.objects.filter(
        reference=reserve_reference
    ).first()

    if reservation and (
        reservation.wallet_id != wallet.pk
        or reservation.captain_id != trip.captain_id
        or reservation.transaction_type
        != FinancialTransaction.TransactionType.COMMISSION_RESERVE
    ):
        raise ValidationError(
            "The commission reservation is inconsistent."
        )


    existing = FinancialTransaction.objects.filter(
        reference=reference
    ).first()

    if existing:
        if (
            existing.wallet_id != wallet.pk
            or existing.captain_id != trip.captain_id
            or existing.transaction_type
            != FinancialTransaction.TransactionType.COMMISSION_RELEASE
            or reservation is None
            or existing.amount != reservation.amount
        ):
            raise ValidationError("The reservation release is inconsistent.")

        return wallet

    if reservation is None:
        return wallet

    if wallet.reserved_balance < reservation.amount:
        raise ValidationError("Reserved balance is inconsistent.")

    wallet.reserved_balance -= reservation.amount
    wallet.save(update_fields=["reserved_balance", "updated_at"])

    FinancialTransaction.objects.create(
        captain=trip.captain,
        wallet=wallet,
        transaction_type=FinancialTransaction.TransactionType.COMMISSION_RELEASE,
        amount=reservation.amount,
        reference=reference,
        description=f"Commission reservation released for trip #{trip.pk}",
    )

    return wallet