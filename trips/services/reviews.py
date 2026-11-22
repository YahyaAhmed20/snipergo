from asgiref.sync import async_to_sync
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Avg, Count

from accounts.models import CaptainProfile, CustomerProfile, Notification, User
from accounts.services.notifications import (
    create_notification,
    dispatch_notification_websocket,
)
from trips.models import Trip, TripReview


def create_trip_review(*, trip, customer, rating, comment=""):
    """
    Create a trip review for a completed trip.

    Validates:
    - customer owns the trip
    - trip is COMPLETED
    - rating is in range 1-5
    - no existing review for the trip (OneToOneField enforces at DB level)
    """
    if not isinstance(trip, Trip):
        trip = Trip.objects.select_for_update().get(pk=trip)

    if not isinstance(customer, CustomerProfile):
        customer = CustomerProfile.objects.get(pk=customer)

    if trip.customer_id != customer.pk:
        raise ValidationError(
            "You can only review trips you booked yourself."
        )

    if trip.status != Trip.Status.COMPLETED:
        raise ValidationError(
            "Reviews are only allowed for completed trips."
        )

    if not (1 <= rating <= 5):
        raise ValidationError(
            "Rating must be between 1 and 5."
        )

    if TripReview.objects.filter(trip_id=trip.pk).exists():
        raise ValidationError(
            "This trip already has a review."
        )

    captain = trip.captain
    if captain is None:
        raise ValidationError(
            "The trip has no assigned captain."
        )

    if not isinstance(captain, CaptainProfile):
        captain = CaptainProfile.objects.select_for_update().get(
            pk=captain.pk
        )

    with transaction.atomic():
        review = TripReview.objects.create(
            customer=customer,
            captain=captain,
            trip=trip,
            rating=rating,
            comment=comment,
        )
        recalculate_captain_rating(captain=captain)

        # Notify captain of new review
        notification = create_notification(
            user=captain.user,
            type=Notification.Type.REVIEW_RECEIVED,
            title="تقييم جديد",
            message=f"لديك تقييم جديد للرحلة #{trip.pk}",
            payload={
                "trip_id": trip.pk,
                "customer_id": customer.pk,
                "review_id": review.pk,
                "rating": rating,
            },
        )
        transaction.on_commit(
            lambda n=notification: async_to_sync(dispatch_notification_websocket)(n)
        )

    return review


def get_captain_reviews(*, captain):
    """
    Return all reviews received by a captain, newest first.
    """
    if not isinstance(captain, CaptainProfile):
        captain = CaptainProfile.objects.get(pk=captain)

    return (
        TripReview.objects
        .filter(captain=captain)
        .select_related("customer__user", "trip")
        .order_by("-created_at")
    )


def recalculate_captain_rating(*, captain):
    """
    Recalculate CaptainProfile.average_rating from actual TripReview records.

    Does NOT touch total_trips (which retains its existing meaning as
    completed trips count). Review count is derived from TripReview records.
    """
    if not isinstance(captain, CaptainProfile):
        captain = CaptainProfile.objects.get(pk=captain)

    aggregation = (
        TripReview.objects
        .filter(captain=captain)
        .aggregate(
            avg_rating=Avg("rating"),
            review_count=Count("id"),
        )
    )

    avg = aggregation["avg_rating"]
    count = aggregation["review_count"]

    captain.average_rating = avg if avg is not None else 0
    captain.save(update_fields=["average_rating", "updated_at"])

    return captain
