from functools import lru_cache
import json
from pathlib import Path

from django.core.exceptions import ValidationError
from shapely.geometry import Point, shape


BOUNDARY_FILE = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "suez_governorate.geojson"
)


@lru_cache(maxsize=1)
def _load_suez_boundary():
    try:
        with BOUNDARY_FILE.open("r", encoding="utf-8") as file:
            data = json.load(file)

        features = data.get("features", [])
        if len(features) != 1:
            raise ValueError("Expected exactly one governorate boundary.")

        feature = features[0]
        if feature.get("properties", {}).get("@id") != "relation/3062185":
            raise ValueError("Unexpected governorate boundary ID.")

        polygon = shape(feature["geometry"])
        if polygon.is_empty or not polygon.is_valid:
            raise ValueError("Governorate boundary geometry is invalid.")

        return polygon

    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(
            f"Unable to load Suez geofence boundary: {exc}"
        ) from exc


def is_inside_suez(longitude, latitude):
    """Return True when a coordinate is covered by the Suez boundary."""
    try:
        longitude = float(longitude)
        latitude = float(latitude)
    except (TypeError, ValueError):
        raise ValidationError("Invalid geographic coordinates.")

    if not (
        -180 <= longitude <= 180
        and -90 <= latitude <= 90
    ):
        raise ValidationError("Invalid geographic coordinates.")

    return _load_suez_boundary().covers(Point(longitude, latitude))


def validate_trip_geofence(
    pickup_latitude,
    pickup_longitude,
    destination_latitude,
    destination_longitude,
):
    """Reject a trip if either endpoint is outside Suez Governorate."""
    if not is_inside_suez(pickup_longitude, pickup_latitude):
        raise ValidationError(
            {"pickup": "Pickup location is outside Suez Governorate."}
        )

    if not is_inside_suez(destination_longitude, destination_latitude):
        raise ValidationError(
            {
                "destination": (
                    "Destination is outside Suez Governorate."
                )
            }
        )
