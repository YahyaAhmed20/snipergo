
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from trips.services.geofence import (
    is_inside_suez,
    validate_trip_geofence,
)


class SuezGeofenceTests(SimpleTestCase):
    def test_known_suez_coordinate_is_inside(self):
        self.assertTrue(is_inside_suez(32.5498, 29.9668))

    def test_coordinate_outside_suez_is_rejected(self):
        self.assertFalse(is_inside_suez(32.90, 29.90))

    def test_non_numeric_coordinates_raise_validation_error(self):
        with self.assertRaises(ValidationError):
            is_inside_suez("invalid", 29.9668)

    def test_longitude_out_of_range_raises_validation_error(self):
        with self.assertRaises(ValidationError):
            is_inside_suez(181, 29.9668)

    def test_latitude_out_of_range_raises_validation_error(self):
        with self.assertRaises(ValidationError):
            is_inside_suez(32.5498, 91)

    def test_outside_pickup_is_rejected(self):
        with self.assertRaises(ValidationError) as context:
            validate_trip_geofence(
                pickup_latitude=29.90,
                pickup_longitude=32.90,
                destination_latitude=29.9668,
                destination_longitude=32.5498,
            )

        self.assertIn("pickup", context.exception.message_dict)

    def test_outside_destination_is_rejected(self):
        with self.assertRaises(ValidationError) as context:
            validate_trip_geofence(
                pickup_latitude=29.9668,
                pickup_longitude=32.5498,
                destination_latitude=29.90,
                destination_longitude=32.90,
            )

        self.assertIn("destination", context.exception.message_dict)
