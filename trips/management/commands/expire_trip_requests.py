
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from trips.models import Trip, CaptainOffer
from trips.services.offers import (
    TRIP_REQUEST_EXPIRY_MINUTES,
    expire_trip_if_needed,
)


class Command(BaseCommand):
    help = "Expire old trip requests and pending captain offers."

    def handle(self, *args, **options):
        now = timezone.now()
        trip_cutoff = now - timedelta(
            minutes=TRIP_REQUEST_EXPIRY_MINUTES
        )

        expired_trips = 0

        trip_ids = Trip.objects.filter(
            status=Trip.Status.REQUESTED,
            captain__isnull=True,
            requested_at__lte=trip_cutoff,
        ).values_list("pk", flat=True)

        for trip_id in trip_ids.iterator():
            if expire_trip_if_needed(trip_id=trip_id):
                expired_trips += 1

        with transaction.atomic():
            expired_offers = CaptainOffer.objects.filter(
                status=CaptainOffer.Status.PENDING,
                expires_at__lte=now,
            ).update(
                status=CaptainOffer.Status.EXPIRED,
                responded_at=now,
                updated_at=now,
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Expired trips: {expired_trips} | "
                f"Expired offers: {expired_offers}"
            )
        )
