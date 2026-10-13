from decimal import Decimal

from django.db import models

# Create your models here.

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
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



class TripPricingConfig(models.Model):
    city = models.CharField(
        max_length=100,
        unique=True,
        default="Suez",
    )
    base_fare = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=10,
        validators=[MinValueValidator(0)],
    )
    price_per_km = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=5,
        validators=[MinValueValidator(0)],
    )
    minimum_fare = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=15,
        validators=[MinValueValidator(0)],
    )
    maximum_fare = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
    )
    negotiation_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("20.00"),
        validators=[
            MinValueValidator(Decimal("0.00")),
            MaxValueValidator(Decimal("100.00")),
        ],
    )
    is_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Trip Pricing Configuration"
        verbose_name_plural = "Trip Pricing Configurations"

    def __str__(self):
        return f"{self.city} Pricing"

    def clean(self):
        super().clean()
        if (
            self.maximum_fare is not None
            and self.maximum_fare < self.minimum_fare
        ):
            raise ValidationError({
                "maximum_fare": "Maximum fare cannot be less than minimum fare."
            })

        if not Decimal("0.00") <= self.negotiation_percent <= Decimal("100.00"):
            raise ValidationError({
                "negotiation_percent": "Negotiation percentage must be between 0 and 100."
            })


class CaptainOffer(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        ACCEPTED = "ACCEPTED", "Accepted"
        REJECTED = "REJECTED", "Rejected"
        EXPIRED = "EXPIRED", "Expired"
        WITHDRAWN = "WITHDRAWN", "Withdrawn"

    trip = models.ForeignKey(
        Trip,
        on_delete=models.CASCADE,
        related_name="offers",
    )
    captain = models.ForeignKey(
        "accounts.CaptainProfile",
        on_delete=models.PROTECT,
        related_name="trip_offers",
    )
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0)],
    )
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    expires_at = models.DateTimeField()
    responded_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["trip", "status"]),
            models.Index(fields=["captain", "status"]),
            models.Index(fields=["expires_at", "status"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["trip", "captain"],
                condition=models.Q(status="PENDING"),
                name="unique_pending_offer_per_captain_trip",
            ),
        ]

    def __str__(self):
        return f"Trip #{self.trip_id} - Offer {self.amount}"



class CommissionConfig(models.Model):
    name = models.CharField(
        max_length=100,
        unique=True,
        default="default",
    )
    percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("10.00"),
        validators=[
            MinValueValidator(Decimal("0.00")),
            MaxValueValidator(Decimal("100.00")),
        ],
    )
    is_active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Commission Configuration"
        verbose_name_plural = "Commission Configurations"

    def __str__(self):
        return f"{self.name}: {self.percentage}%"


class TripFinancialRecord(models.Model):
    class SettlementStatus(models.TextChoices):
        PENDING = "PENDING", "Pending"
        SETTLED = "SETTLED", "Settled"

    class CollectionStatus(models.TextChoices):
        PENDING = "PENDING", "Pending"
        COLLECTED = "COLLECTED", "Collected"
        FAILED = "FAILED", "Failed"
        REFUNDED = "REFUNDED", "Refunded"

    class CommissionStatus(models.TextChoices):
        PENDING = "PENDING", "Pending"
        COLLECTED = "COLLECTED", "Collected"
        WAIVED = "WAIVED", "Waived"

    trip = models.OneToOneField(
        Trip,
        on_delete=models.PROTECT,
        related_name="financial_record",
    )
    gross_fare = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    commission_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        validators=[
            MinValueValidator(Decimal("0.00")),
            MaxValueValidator(Decimal("100.00")),
        ],
    )
    commission_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    captain_earnings = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
    )

    collection_status = models.CharField(
        max_length=10,
        choices=CollectionStatus.choices,
        default=CollectionStatus.PENDING,
        db_index=True,
    )
    commission_status = models.CharField(
        max_length=10,
        choices=CommissionStatus.choices,
        default=CommissionStatus.PENDING,
        db_index=True,
    )
    settlement_status = models.CharField(
        max_length=10,
        choices=SettlementStatus.choices,
        default=SettlementStatus.PENDING,
        db_index=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    settled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Trip Financial Record"
        verbose_name_plural = "Trip Financial Records"

    def __str__(self):
        return f"Trip #{self.trip_id} Financial Record"



class FinancialTransaction(models.Model):
    class TransactionType(models.TextChoices):
        WALLET_TOPUP = "WALLET_TOPUP", "Wallet Top-up"
        COMMISSION_DEDUCTION = "COMMISSION_DEDUCTION", "Commission Deduction"
        COMMISSION_DUE = "COMMISSION_DUE", "Commission Due"
        COMMISSION_PAYMENT = "COMMISSION_PAYMENT", "Commission Payment"
        CAPTAIN_PAYOUT = "CAPTAIN_PAYOUT", "Captain Payout"
        COMMISSION_RESERVE = "COMMISSION_RESERVE", "Commission Reserve"
        COMMISSION_RELEASE = "COMMISSION_RELEASE", "Commission Release"

    captain = models.ForeignKey(
        "accounts.CaptainProfile",
        on_delete=models.PROTECT,
        related_name="financial_transactions",
    )
    wallet = models.ForeignKey(
        "accounts.CaptainWallet",
        on_delete=models.PROTECT,
        related_name="transactions",
        null=True,
        blank=True,
    )
    financial_record = models.ForeignKey(
        TripFinancialRecord,
        on_delete=models.PROTECT,
        related_name="transactions",
        null=True,
        blank=True,
    )
    transaction_type = models.CharField(
        max_length=20,
        choices=TransactionType.choices,
        db_index=True,
    )
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    reference = models.CharField(
        max_length=100,
        unique=True,
    )
    description = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["captain", "transaction_type", "created_at"],
            ),
        ]
        verbose_name = "Financial Transaction"
        verbose_name_plural = "Financial Transactions"

    def __str__(self):
        return (
            f"{self.transaction_type} - "
            f"Captain #{self.captain_id} - {self.amount}"
        )