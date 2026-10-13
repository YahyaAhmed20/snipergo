from decimal import Decimal, InvalidOperation
from math import asin, cos, radians, sin, sqrt

from django.core.exceptions import ValidationError


EARTH_RADIUS_KM = 6371.0


def calculate_distance_km(
    *,
    pickup_latitude,
    pickup_longitude,
    destination_latitude,
    destination_longitude,
):
    try:
        pickup_lat = float(Decimal(str(pickup_latitude)))
        pickup_lon = float(Decimal(str(pickup_longitude)))
        destination_lat = float(Decimal(str(destination_latitude)))
        destination_lon = float(Decimal(str(destination_longitude)))
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationError("Invalid coordinates.")

    coordinates = [
        pickup_lat,
        pickup_lon,
        destination_lat,
        destination_lon,
    ]

    if not all(map(lambda value: value == value and abs(value) != float("inf"), coordinates)):
        raise ValidationError("Coordinates must be finite.")

    if not (-90 <= pickup_lat <= 90 and -90 <= destination_lat <= 90):
        raise ValidationError("Latitude must be between -90 and 90.")

    if not (-180 <= pickup_lon <= 180 and -180 <= destination_lon <= 180):
        raise ValidationError("Longitude must be between -180 and 180.")

    lat1 = radians(pickup_lat)
    lat2 = radians(destination_lat)
    delta_lat = radians(destination_lat - pickup_lat)
    delta_lon = radians(destination_lon - pickup_lon)

    a = (
        sin(delta_lat / 2) ** 2
        + cos(lat1) * cos(lat2) * sin(delta_lon / 2) ** 2
    )
    a = min(1.0, max(0.0, a))

    distance = 2 * EARTH_RADIUS_KM * asin(sqrt(a))

    return Decimal(str(distance))
