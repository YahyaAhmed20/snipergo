from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from accounts.models import (
    CaptainProfile,
    CaptainWallet,
    CustomerProfile,
)
from trips.models import Trip, TripReview
from trips.services.reviews import (
    create_trip_review,
    get_captain_reviews,
    recalculate_captain_rating,
)

User = get_user_model()


def _make_user(username, role, password="test-password"):
    return User.objects.create_user(
        username=username,
        password=password,
        role=role,
    )


def _make_customer(username="cust"):
    user = _make_user(username, User.Role.CUSTOMER)
    return CustomerProfile.objects.create(user=user), user


def _make_captain(username="capt"):
    user = _make_user(username, User.Role.CAPTAIN)
    captain = CaptainProfile.objects.create(user=user)
    CaptainWallet.objects.create(captain=captain, balance=Decimal("500.00"))
    return captain, user


def _make_completed_trip(customer, captain, final_fare=Decimal("100.00")):
    return Trip.objects.create(
        customer=customer,
        captain=captain,
        pickup_address="Pickup",
        pickup_latitude=Decimal("29.9670"),
        pickup_longitude=Decimal("32.5498"),
        destination_address="Destination",
        destination_latitude=Decimal("29.9800"),
        destination_longitude=Decimal("32.5600"),
        status=Trip.Status.COMPLETED,
        final_fare=final_fare,
        agreed_fare=final_fare,
        commission_percentage=Decimal("10.00"),
    )


class TripReviewServiceTests(TestCase):
    def setUp(self):
        self.customer, self.customer_user = _make_customer("review_customer")
        self.captain, self.captain_user = _make_captain("review_captain")
        self.trip = _make_completed_trip(self.customer, self.captain)

    def test_customer_can_review_own_completed_trip(self):
        review = create_trip_review(
            trip=self.trip,
            customer=self.customer,
            rating=5,
            comment="رحلة ممتازة",
        )
        self.assertEqual(review.rating, 5)
        self.assertEqual(review.comment, "رحلة ممتازة")
        self.assertEqual(review.customer, self.customer)
        self.assertEqual(review.captain, self.captain)
        self.assertEqual(review.trip, self.trip)

    def test_cannot_review_non_completed_trip(self):
        pending_trip = Trip.objects.create(
            customer=self.customer,
            captain=self.captain,
            pickup_address="Pickup",
            pickup_latitude=Decimal("29.9670"),
            pickup_longitude=Decimal("32.5498"),
            destination_address="Destination",
            destination_latitude=Decimal("29.9800"),
            destination_longitude=Decimal("32.5600"),
            status=Trip.Status.REQUESTED,
        )
        with self.assertRaises(ValidationError):
            create_trip_review(
                trip=pending_trip,
                customer=self.customer,
                rating=5,
            )

    def test_customer_cannot_review_another_customers_trip(self):
        other_customer, _ = _make_customer("other_customer")
        other_trip = _make_completed_trip(other_customer, self.captain)
        with self.assertRaises(ValidationError):
            create_trip_review(
                trip=other_trip,
                customer=self.customer,
                rating=5,
            )

    def test_rating_below_1_rejected(self):
        with self.assertRaises(ValidationError):
            create_trip_review(
                trip=self.trip,
                customer=self.customer,
                rating=0,
            )

    def test_rating_above_5_rejected(self):
        with self.assertRaises(ValidationError):
            create_trip_review(
                trip=self.trip,
                customer=self.customer,
                rating=6,
            )

    def test_duplicate_review_rejected(self):
        create_trip_review(
            trip=self.trip,
            customer=self.customer,
            rating=5,
        )
        with self.assertRaises(ValidationError):
            create_trip_review(
                trip=self.trip,
                customer=self.customer,
                rating=3,
            )

    def test_duplicate_review_blocked_at_database_level(self):
        create_trip_review(
            trip=self.trip,
            customer=self.customer,
            rating=5,
        )
        with self.assertRaises((IntegrityError, ValidationError)):
            with transaction.atomic():
                TripReview.objects.create(
                    customer=self.customer,
                    captain=self.captain,
                    trip=self.trip,
                    rating=3,
                )

    def test_captain_average_rating_calculated(self):
        create_trip_review(trip=self.trip, customer=self.customer, rating=5)
        self.captain.refresh_from_db()
        self.assertEqual(self.captain.average_rating, 5)

    def test_review_count_calculated_separately(self):
        create_trip_review(trip=self.trip, customer=self.customer, rating=5)
        reviews = list(get_captain_reviews(captain=self.captain))
        self.assertEqual(len(reviews), 1)

    def test_total_trips_retains_meaning(self):
        self.captain.total_trips = 10
        self.captain.save(update_fields=["total_trips"])
        create_trip_review(trip=self.trip, customer=self.customer, rating=5)
        self.captain.refresh_from_db()
        self.assertEqual(self.captain.total_trips, 10)

    def test_multiple_reviews_calculate_correct_average(self):
        customer2, _ = _make_customer("review_customer2")
        captain2, _ = _make_captain("review_captain2")

        trip1 = _make_completed_trip(self.customer, self.captain, Decimal("100.00"))
        trip2 = _make_completed_trip(customer2, self.captain, Decimal("200.00"))

        create_trip_review(trip=trip1, customer=self.customer, rating=5)
        create_trip_review(trip=trip2, customer=customer2, rating=3)

        self.captain.refresh_from_db()
        self.assertEqual(self.captain.average_rating, 4)

    def test_captain_can_see_own_reviews(self):
        create_trip_review(trip=self.trip, customer=self.customer, rating=5)
        reviews = list(get_captain_reviews(captain=self.captain))
        self.assertEqual(len(reviews), 1)
        self.assertEqual(reviews[0].rating, 5)

    def test_captain_cannot_see_another_captains_reviews(self):
        other_captain, _ = _make_captain("other_review_captain")
        other_trip = _make_completed_trip(self.customer, other_captain)
        create_trip_review(trip=other_trip, customer=self.customer, rating=5)

        reviews = list(get_captain_reviews(captain=self.captain))
        self.assertEqual(len(reviews), 0)

        other_reviews = list(get_captain_reviews(captain=other_captain))
        self.assertEqual(len(other_reviews), 1)


class TripReviewAPITests(TestCase):
    def setUp(self):
        self.customer, self.customer_user = _make_customer("api_review_customer")
        self.captain, self.captain_user = _make_captain("api_review_captain")
        self.trip = _make_completed_trip(self.customer, self.captain)

    def _login_customer(self):
        self.client.force_login(self.customer_user)

    def _login_captain(self):
        self.client.force_login(self.captain_user)

    def test_customer_can_submit_review_via_api(self):
        self._login_customer()
        url = reverse("trips_api:review_create", kwargs={"pk": self.trip.pk})
        response = self.client.post(
            url,
            {"rating": 5, "comment": "رحلة رائعة"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["rating"], 5)
        self.assertEqual(response.data["customer_name"], self.customer.user.username)

    def test_captain_cannot_submit_customer_review(self):
        self._login_captain()
        url = reverse("trips_api:review_create", kwargs={"pk": self.trip.pk})
        response = self.client.post(
            url,
            {"rating": 5, "comment": "test"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)

    def test_anonymous_cannot_submit_review(self):
        url = reverse("trips_api:review_create", kwargs={"pk": self.trip.pk})
        response = self.client.post(
            url,
            {"rating": 5, "comment": "test"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)

    def test_cannot_review_non_completed_trip_via_api(self):
        pending_trip = Trip.objects.create(
            customer=self.customer,
            captain=self.captain,
            pickup_address="Pickup",
            pickup_latitude=Decimal("29.9670"),
            pickup_longitude=Decimal("32.5498"),
            destination_address="Destination",
            destination_latitude=Decimal("29.9800"),
            destination_longitude=Decimal("32.5600"),
            status=Trip.Status.REQUESTED,
        )
        self._login_customer()
        url = reverse("trips_api:review_create", kwargs={"pk": pending_trip.pk})
        response = self.client.post(
            url,
            {"rating": 5},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_rating_out_of_range_rejected_via_api(self):
        self._login_customer()
        url = reverse("trips_api:review_create", kwargs={"pk": self.trip.pk})
        response = self.client.post(
            url,
            {"rating": 0},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

        response = self.client.post(
            url,
            {"rating": 6},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_duplicate_review_rejected_via_api(self):
        self._login_customer()
        url = reverse("trips_api:review_create", kwargs={"pk": self.trip.pk})
        self.client.post(url, {"rating": 5}, content_type="application/json")
        response = self.client.post(
            url,
            {"rating": 3},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_captain_can_view_own_reviews(self):
        create_trip_review(trip=self.trip, customer=self.customer, rating=5)
        self._login_captain()
        url = reverse("trips_api:captain_my_reviews")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["rating"], 5)

    def test_customer_cannot_access_captain_review_history(self):
        self._login_customer()
        url = reverse("trips_api:captain_my_reviews")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)

    def test_captain_cannot_see_another_captains_reviews(self):
        other_captain, _ = _make_captain("other_api_captain")
        other_trip = _make_completed_trip(self.customer, other_captain)
        create_trip_review(trip=other_trip, customer=self.customer, rating=5)

        self._login_captain()
        url = reverse("trips_api:captain_my_reviews")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 0)


class TripReviewModelTests(TestCase):
    def setUp(self):
        self.customer, _ = _make_customer("model_customer")
        self.captain, _ = _make_captain("model_captain")
        self.trip = _make_completed_trip(self.customer, self.captain)

    def test_tripreview_model_fields(self):
        review = TripReview.objects.create(
            customer=self.customer,
            captain=self.captain,
            trip=self.trip,
            rating=4,
            comment="جيد",
        )
        self.assertEqual(review.rating, 4)
        self.assertEqual(review.comment, "جيد")
        self.assertIsNotNone(review.created_at)

    def test_one_review_per_trip_constraint(self):
        TripReview.objects.create(
            customer=self.customer,
            captain=self.captain,
            trip=self.trip,
            rating=5,
        )
        with self.assertRaises((IntegrityError, ValidationError)):
            TripReview.objects.create(
                customer=self.customer,
                captain=self.captain,
                trip=self.trip,
                rating=3,
            )

    def test_average_rating_default_zero(self):
        self.captain.refresh_from_db()
        self.assertEqual(self.captain.average_rating, 0)

    def test_recalculate_captain_rating(self):
        create_trip_review(trip=self.trip, customer=self.customer, rating=5)
        recalculate_captain_rating(captain=self.captain)
        self.captain.refresh_from_db()
        self.assertEqual(self.captain.average_rating, 5)