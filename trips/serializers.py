
from rest_framework import serializers

from accounts.models import CustomerProfile
from trips.models import Trip


class TripCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Trip
        fields = [
            "id",
            "pickup_address",
            "pickup_latitude",
            "pickup_longitude",
            "destination_address",
            "destination_latitude",
            "destination_longitude",
            "payment_method",
            "estimated_fare",
        ]
        read_only_fields = ["id", "estimated_fare"]

    def create(self, validated_data):
        user = self.context["request"].user

        try:
            customer = user.customer_profile
        except CustomerProfile.DoesNotExist:
            raise serializers.ValidationError(
                "A customer profile is required to create a trip."
            )

        return Trip.objects.create(
            customer=customer,
            **validated_data,
        )


class TripDetailSerializer(serializers.ModelSerializer):
    customer_username = serializers.CharField(
        source="customer.user.username",
        read_only=True,
    )
    captain_username = serializers.CharField(
        source="captain.user.username",
        read_only=True,
        allow_null=True,
    )

    class Meta:
        model = Trip
        fields = [
            "id",
            "customer_username",
            "captain_username",
            "pickup_address",
            "pickup_latitude",
            "pickup_longitude",
            "destination_address",
            "destination_latitude",
            "destination_longitude",
            "status",
            "payment_method",
            "estimated_fare",
            "agreed_fare",
            "final_fare",
            "requested_at",
            "accepted_at",
            "started_at",
            "completed_at",
            "cancelled_at",
            "cancellation_reason",
        ]
        read_only_fields = fields




class AvailableTripSerializer(serializers.ModelSerializer):
    class Meta:
        model = Trip
        fields = [
            "id",
            "pickup_address",
            "pickup_latitude",
            "pickup_longitude",
            "destination_address",
            "destination_latitude",
            "destination_longitude",
            "payment_method",
            "estimated_fare",
            "requested_at",
        ]
        read_only_fields = fields
