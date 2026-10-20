from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from accounts.models import CustomerProfile
from trips.models import CaptainOffer, Trip
from trips.services.distance import calculate_road_distance_km
from trips.services.pricing import calculate_estimated_fare


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

        try:
            distance_km = calculate_road_distance_km(
                pickup_latitude=validated_data["pickup_latitude"],
                pickup_longitude=validated_data["pickup_longitude"],
                destination_latitude=validated_data["destination_latitude"],
                destination_longitude=validated_data["destination_longitude"],
            )

            estimated_fare = calculate_estimated_fare(
                city="Suez",
                distance_km=distance_km,
            )

        except DjangoValidationError as exc:
            raise serializers.ValidationError(
                {"estimated_fare": exc.messages}
            )

        return Trip.objects.create(
            customer=customer,
            estimated_fare=estimated_fare,
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


class CaptainOfferCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = CaptainOffer
        fields = ["id", "trip", "amount", "status", "expires_at", "created_at"]
        read_only_fields = [
            "id",
            "trip",
            "status",
            "expires_at",
            "created_at",
        ]


class CaptainOfferListSerializer(serializers.ModelSerializer):
    captain_username = serializers.CharField(
        source="captain.user.username",
        read_only=True,
    )

    class Meta:
        model = CaptainOffer
        fields = [
            "id",
            "captain_username",
            "amount",
            "status",
            "expires_at",
            "created_at",
        ]
        read_only_fields = fields
