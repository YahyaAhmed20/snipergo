from django.db import models

# Create your models here.

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models


class Trip(models.Model):
    class Status(models.TextChoices):
        REQUESTED = "REQUESTED", "Requested"
        ACCEPTED = "ACCEPTED", "Accepted"
        ARRIVING = "ARRIVING", "Captain arriving"
        ARRIVED = "ARRIVED", "Captain arrived"
        IN_PROGRESS = "IN_PROGRESS", "In progress"
        COMPLETED = "COMPLETED", "Completed"
        CANCELLED = "CANCELLED", "Cancelled"
        EXPIRED = "EXPIRED", "Expired"

    class PaymentMethod(models.TextChoices):
        CASH = "CASH", "Cash"
        ONLINE = "ONLINE", "Online"

    customer = models.ForeignKey(
        "accounts.CustomerProfile",
        on_delete=models.PROTECT,
        related_name="trips",
    )
    captain = models.ForeignKey(
        "accounts.CaptainProfile",
        on_delete=models.PROTECT,
        related_name="trips",
        null=True,
        blank=True,
    )
    vehicle = models.ForeignKey(
        "vehicles.Vehicle",
        on_delete=models.PROTECT,
        related_name="trips",
        null=True,
        blank=True,
    )

    pickup_address = models.CharField(max_length=255)
    pickup_latitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
    )
    pickup_longitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
    )
    destination_address = models.CharField(max_length=255)
    destination_latitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
    )
    destination_longitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
    )

    status = models.CharField(
        max_length=15,
        choices=Status.choices,
        default=Status.REQUESTED,
        db_index=True,
    )
    payment_method = models.CharField(
        max_length=10,
        choices=PaymentMethod.choices,
        default=PaymentMethod.CASH,
    )

    estimated_fare = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0)],
        null=True,
        blank=True,
    )
    agreed_fare = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0)],
        null=True,
        blank=True,
    )
    final_fare = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0)],
        null=True,
        blank=True,
    )

    requested_at = models.DateTimeField(auto_now_add=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    cancellation_reason = models.CharField(
        max_length=255,
        blank=True,
    )
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="cancelled_trips",
        null=True,
        blank=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-requested_at"]
        indexes = [
            models.Index(fields=["status", "requested_at"]),
            models.Index(fields=["captain", "status"]),
            models.Index(fields=["customer", "status"]),
        ]

    def __str__(self):
        return f"Trip #{self.pk} - {self.get_status_display()}"
