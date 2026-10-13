from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

from django.core.exceptions import ValidationError

from trips.models import TripPricingConfig


MONEY_QUANTUM = Decimal("0.01")


def calculate_negotiation_range(*, city, estimated_fare):
    if not isinstance(city, str) or not city.strip():
        raise ValidationError("A valid city is required.")

    try:
        fare = Decimal(str(estimated_fare))
    except Exception:
        raise ValidationError("Estimated fare must be a valid number.")

    if not fare.is_finite() or fare < 0:
        raise ValidationError(
            "Estimated fare must be finite and non-negative."
        )

    config = TripPricingConfig.objects.filter(
        city__iexact=city.strip(),
        is_active=True,
    ).first()

    if config is None:
        raise ValidationError(
            f"No active pricing configuration for city: {city}."
        )

    percentage = config.negotiation_percent / Decimal("100")
    lower_bound = fare * (Decimal("1") - percentage)
    upper_bound = fare * (Decimal("1") + percentage)

    lower_bound = max(config.minimum_fare, lower_bound)

    if config.maximum_fare is not None:
        upper_bound = min(config.maximum_fare, upper_bound)

    minimum_offer = lower_bound.quantize(
        MONEY_QUANTUM,
        rounding=ROUND_CEILING,
    )
    maximum_offer = upper_bound.quantize(
        MONEY_QUANTUM,
        rounding=ROUND_FLOOR,
    )

    if minimum_offer > maximum_offer:
        raise ValidationError(
            "No valid negotiation range exists for this fare."
        )

    return minimum_offer, maximum_offer
