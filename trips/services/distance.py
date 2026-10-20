from decimal import Decimal, InvalidOperation
from math import asin, cos, isfinite, radians, sin, sqrt

import requests
from django.conf import settings
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

    if not all(isfinite(value) for value in coordinates):
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


def calculate_road_distance_km(
    *,
    pickup_latitude,
    pickup_longitude,
    destination_latitude,
    destination_longitude,
):
    # Reuse existing validation before making an external request.
    calculate_distance_km(
        pickup_latitude=pickup_latitude,
        pickup_longitude=pickup_longitude,
        destination_latitude=destination_latitude,
        destination_longitude=destination_longitude,
    )

    try:
        pickup_lat = float(Decimal(str(pickup_latitude)))
        pickup_lon = float(Decimal(str(pickup_longitude)))
        destination_lat = float(Decimal(str(destination_latitude)))
        destination_lon = float(Decimal(str(destination_longitude)))
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationError("Invalid coordinates.")

    base_url = getattr(
        settings,
        "OSRM_ROUTING_URL",
        "https://router.project-osrm.org",
    ).rstrip("/")
    timeout = getattr(settings, "OSRM_REQUEST_TIMEOUT", 10)

    url = (
        f"{base_url}/route/v1/driving/"
        f"{pickup_lon},{pickup_lat};{destination_lon},{destination_lat}"
    )

    try:
        response = requests.get(
            url,
            params={"overview": "false"},
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise ValidationError(
            "Road routing is temporarily unavailable. Please try again."
        ) from exc

    if not isinstance(data, dict) or data.get("code") != "Ok":
        raise ValidationError(
            "No valid driving route was found between these locations."
        )

    routes = data.get("routes")
    if not isinstance(routes, list) or not routes:
        raise ValidationError(
            "No valid driving route was found between these locations."
        )

    route = routes[0]
    if not isinstance(route, dict):
        raise ValidationError(
            "The routing service returned an invalid route."
        )

    distance_meters = route.get("distance")
    if (
        isinstance(distance_meters, bool)
        or not isinstance(distance_meters, (int, float))
        or not isfinite(distance_meters)
        or distance_meters < 0
    ):
        raise ValidationError("The routing service returned an invalid distance.")

    return Decimal(str(distance_meters)) / Decimal("1000")
