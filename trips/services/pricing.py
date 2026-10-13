
from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError

from trips.models import TripPricingConfig


MONEY_QUANTUM = Decimal("0.01")


def calculate_estimated_fare(*, city, distance_km):
    try:
        distance = Decimal(str(distance_km))
    except Exception:
        raise ValidationError("Distance must be a valid number.")

    if not distance.is_finite() or distance < 0:
        raise ValidationError("Distance must be finite and non-negative.")

    config = TripPricingConfig.objects.filter(
        city__iexact=city.strip(),
        is_active=True,
    ).first()

    if config is None:
        raise ValidationError(
            f"No active pricing configuration for city: {city}."
        )

    fare = max(
        config.minimum_fare,
        config.base_fare + distance * config.price_per_km,
    )

    if config.maximum_fare is not None:
        fare = min(fare, config.maximum_fare)

    return fare.quantize(
        MONEY_QUANTUM,
        rounding=ROUND_HALF_UP,
    )
