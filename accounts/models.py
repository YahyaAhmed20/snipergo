from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone

from .storage import CaptainDocumentStorage


class User(AbstractUser):
    class Role(models.TextChoices):
        CUSTOMER = "CUSTOMER", "Customer"
        CAPTAIN = "CAPTAIN", "Captain"
        ADMIN = "ADMIN", "Admin"

    role = models.CharField(
        max_length=10,
        choices=Role.choices,
        default=Role.CUSTOMER,
        db_index=True,
    )

    def __str__(self):
        return f"{self.username} ({self.role})"


class CustomerProfile(models.Model):
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="customer_profile",
    )
    phone_number = models.CharField(max_length=20, blank=True)
    profile_image = models.ImageField(
        upload_to="profiles/customers/",
        blank=True,
    )
    is_phone_verified = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Customer profile: {self.user.username}"


class CaptainProfile(models.Model):
    class ApprovalStatus(models.TextChoices):
        PENDING = "PENDING", "Pending"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"

    class OperationalStatus(models.TextChoices):
        OFFLINE = "OFFLINE", "Offline"
        ONLINE = "ONLINE", "Online"
        SUSPENDED = "SUSPENDED", "Suspended"

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="captain_profile",
    )
    phone_number = models.CharField(max_length=20, blank=True)
    profile_image = models.ImageField(
        upload_to="profiles/captains/",
        blank=True,
    )
    approval_status = models.CharField(
        max_length=10,
        choices=ApprovalStatus.choices,
        default=ApprovalStatus.PENDING,
        db_index=True,
    )
    rejection_reason = models.TextField(blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_captain_profiles",
    )
    operational_status = models.CharField(
        max_length=10,
        choices=OperationalStatus.choices,
        default=OperationalStatus.OFFLINE,
        db_index=True,
    )
    current_latitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
    )
    current_longitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
    )
    location_updated_at = models.DateTimeField(
        null=True,
        blank=True,
    )
    average_rating = models.DecimalField(
        max_digits=3,
        decimal_places=2,
        default=0,
    )
    total_trips = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Captain profile: {self.user.username}"


class CaptainWallet(models.Model):
    captain = models.OneToOneField(
        CaptainProfile,
        on_delete=models.PROTECT,
        related_name="wallet",
    )
    balance = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
    )
    reserved_balance = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Captain Wallet"
        verbose_name_plural = "Captain Wallets"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(balance__gte=0),
                name="captain_wallet_balance_nonnegative",
            ),
            models.CheckConstraint(
                condition=models.Q(reserved_balance__gte=0),
                name="captain_wallet_reserved_nonnegative",
            ),
            models.CheckConstraint(
                condition=models.Q(reserved_balance__lte=models.F("balance")),
                name="captain_wallet_reserved_lte_balance",
            ),
        ]

    @property
    def available_balance(self):
        return self.balance - self.reserved_balance

    def __str__(self):
        return f"Wallet: {self.captain.user.username} - {self.balance} EGP"


class CaptainDocument(models.Model):
    class DocumentType(models.TextChoices):
        NATIONAL_ID = "NATIONAL_ID", "National ID"
        DRIVING_LICENSE = "DRIVING_LICENSE", "Driving License"
        VEHICLE_LICENSE = "VEHICLE_LICENSE", "Vehicle License"
        OTHER = "OTHER", "Other"

    class ReviewStatus(models.TextChoices):
        PENDING = "PENDING", "Pending"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"

    captain = models.ForeignKey(
        CaptainProfile,
        on_delete=models.CASCADE,
        related_name="documents",
    )
    document_type = models.CharField(
        max_length=20,
        choices=DocumentType.choices,
    )
    file = models.FileField(
        storage=CaptainDocumentStorage(),
        upload_to="",
    )
    expires_at = models.DateField(null=True, blank=True)
    review_status = models.CharField(
        max_length=10,
        choices=ReviewStatus.choices,
        default=ReviewStatus.PENDING,
        db_index=True,
    )
    rejection_reason = models.TextField(blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_captain_documents",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return (
            f"{self.captain.user.username} - "
            f"{self.get_document_type_display()}"
        )


class WalletTopUpRequest(models.Model):
    class Method(models.TextChoices):
        PAYMOB = "PAYMOB", "Paymob"
        CASH = "CASH", "Cash deposit"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        COMPLETED = "COMPLETED", "Completed"
        REJECTED = "REJECTED", "Rejected"

    captain = models.ForeignKey(
        CaptainProfile,
        on_delete=models.PROTECT,
        related_name="top_up_requests",
    )
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
    )
    method = models.CharField(
        max_length=10,
        choices=Method.choices,
    )
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    reference = models.CharField(
        max_length=100,
        unique=True,
    )
    payment_reference = models.CharField(
        max_length=150,
        blank=True,
        db_index=True,
    )

    payment_order_id = models.CharField(
        max_length=100,
        blank=True,
        db_index=True,
    )
    payment_transaction_id = models.CharField(
        max_length=100,
        blank=True,
        db_index=True,
    )
    payment_verified_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    reviewed_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reviewed_wallet_topups",
    )
    reviewed_at = models.DateTimeField(
        null=True,
        blank=True,
    )
    rejection_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name="wallet_topup_amount_positive",
            ),
            models.UniqueConstraint(
                fields=["payment_order_id"],
                condition=~models.Q(payment_order_id=""),
                name="unique_paymob_order_id_nonempty",
            ),
            models.UniqueConstraint(
                fields=["payment_transaction_id"],
                condition=~models.Q(payment_transaction_id=""),
                name="unique_paymob_transaction_id_nonempty",
            ),
        ]

    def __str__(self):
        return (
            f"{self.captain.user.username} - "
            f"{self.amount} EGP - {self.status}"
        )