
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.utils import timezone


class Vehicle(models.Model):
    class VehicleType(models.TextChoices):
        SCOOTER = "SCOOTER", "Scooter"
        MOTORCYCLE = "MOTORCYCLE", "Motorcycle"

    class ApprovalStatus(models.TextChoices):
        PENDING = "PENDING", "Pending"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"

    captain = models.ForeignKey(
        "accounts.CaptainProfile",
        on_delete=models.PROTECT,
        related_name="vehicles",
    )
    vehicle_type = models.CharField(
        max_length=12,
        choices=VehicleType.choices,
    )
    brand = models.CharField(max_length=100)
    model_name = models.CharField(max_length=100)
    manufacture_year = models.PositiveSmallIntegerField(
        validators=[
            MinValueValidator(1950),
            MaxValueValidator(timezone.now().year + 1),
        ],
    )
    plate_number = models.CharField(max_length=30, unique=True)
    chassis_number = models.CharField(max_length=100, unique=True)

    front_photo = models.ImageField(upload_to="vehicles/front/")
    rear_photo = models.ImageField(upload_to="vehicles/rear/")
    side_photo = models.ImageField(upload_to="vehicles/side/")
    plate_photo = models.ImageField(upload_to="vehicles/plate/")

    approval_status = models.CharField(
        max_length=10,
        choices=ApprovalStatus.choices,
        default=ApprovalStatus.PENDING,
        db_index=True,
    )
    rejection_reason = models.TextField(blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_vehicles",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.brand} {self.model_name} - {self.plate_number}"
