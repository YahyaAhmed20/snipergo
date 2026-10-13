from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from trips.models import TripPricingConfig
from trips.services.distance import calculate_distance_km
from trips.services.negotiation import calculate_negotiation_range
from trips.services.pricing import calculate_estimated_fare


class TripPricingTests(TestCase):
    def setUp(self):
        self.config = TripPricingConfig.objects.create(
            city="Suez",
            base_fare=Decimal("10.00"),
            price_per_km=Decimal("5.00"),
            minimum_fare=Decimal("15.00"),
            maximum_fare=Decimal("100.00"),
            is_active=True,
        )

    def test_calculates_fare_from_distance(self):
        fare = calculate_estimated_fare(
            city="Suez",
            distance_km="4",
        )
        self.assertEqual(fare, Decimal("30.00"))

    def test_applies_minimum_fare(self):
        fare = calculate_estimated_fare(
            city="Suez",
            distance_km="0.5",
        )
        self.assertEqual(fare, Decimal("15.00"))

    def test_applies_maximum_fare(self):
        fare = calculate_estimated_fare(
            city="Suez",
            distance_km="30",
        )
        self.assertEqual(fare, Decimal("100.00"))

    def test_rejects_negative_distance(self):
        with self.assertRaises(ValidationError):
            calculate_estimated_fare(
                city="Suez",
                distance_km="-1",
            )

    def test_rejects_unknown_city(self):
        with self.assertRaises(ValidationError):
            calculate_estimated_fare(
                city="Unknown City",
                distance_km="5",
            )

    def test_rejects_maximum_fare_below_minimum(self):
        config = TripPricingConfig(
            city="Invalid City",
            base_fare=Decimal("10.00"),
            price_per_km=Decimal("5.00"),
            minimum_fare=Decimal("50.00"),
            maximum_fare=Decimal("20.00"),
        )
        with self.assertRaises(ValidationError):
            config.full_clean()

    def test_calculates_distance_between_two_points(self):
        distance = calculate_distance_km(
            pickup_latitude="29.9668",
            pickup_longitude="32.5498",
            destination_latitude="29.9804",
            destination_longitude="32.5430",
        )
        self.assertGreater(distance, Decimal("0"))
        self.assertLess(distance, Decimal("5"))

    def test_rejects_invalid_latitude(self):
        with self.assertRaises(ValidationError):
            calculate_distance_km(
                pickup_latitude="100",
                pickup_longitude="32.5",
                destination_latitude="29.9",
                destination_longitude="32.6",
            )

    def test_negotiation_percent_defaults_to_twenty(self):
        config = TripPricingConfig.objects.create(
            city="Cairo",
        )

        self.assertEqual(
            config.negotiation_percent,
            Decimal("20.00"),
        )

    def test_rejects_negotiation_percent_above_one_hundred(self):
        config = TripPricingConfig(
            city="Invalid Percent",
            negotiation_percent=Decimal("100.01"),
        )

        with self.assertRaises(ValidationError):
            config.full_clean()

    def test_rejects_negative_negotiation_percent(self):
        config = TripPricingConfig(
            city="Negative Percent",
            negotiation_percent=Decimal("-1.00"),
        )

        with self.assertRaises(ValidationError):
            config.full_clean()

    def test_calculates_negotiation_range(self):
        minimum, maximum = calculate_negotiation_range(
            city="Suez",
            estimated_fare=Decimal("100.00"),
        )
        self.assertEqual(minimum, Decimal("80.00"))
        self.assertEqual(maximum, Decimal("100.00"))

    def test_rejects_negative_estimated_fare(self):
        with self.assertRaises(ValidationError):
            calculate_negotiation_range(
                city="Suez",
                estimated_fare=Decimal("-10.00"),
            )