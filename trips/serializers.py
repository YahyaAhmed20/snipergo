from datetime import timedelta
from django.core.exceptions import ValidationError
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone
from rest_framework import serializers
from accounts.models import CustomerProfile
from trips.models import CaptainOffer, Trip, TripReview
from trips.services.distance import (
    calculate_road_distance_km,
    calculate_distance_km,
)
from trips.services.pricing import calculate_estimated_fare
from trips.services.geofence import validate_trip_geofence


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
            validate_trip_geofence(
                pickup_latitude=validated_data["pickup_latitude"],
                pickup_longitude=validated_data["pickup_longitude"],
                destination_latitude=validated_data["destination_latitude"],
                destination_longitude=validated_data["destination_longitude"],
            )

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
                exc.message_dict
                if hasattr(exc, "message_dict")
                else exc.messages
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
    captain_location = serializers.SerializerMethodField()

    def get_captain_location(self, obj):
        if obj.status not in {
            Trip.Status.ACCEPTED,
            Trip.Status.ARRIVING,
            Trip.Status.ARRIVED,
            Trip.Status.IN_PROGRESS,
        }:
            return None

        captain = obj.captain
        if captain is None:
            return None

        latitude = captain.current_latitude
        longitude = captain.current_longitude
        updated_at = captain.location_updated_at

        if latitude is None or longitude is None or updated_at is None:
            return None

        now = timezone.now()
        if updated_at > now or updated_at < now - timedelta(minutes=2):
            return None

        return {
            "latitude": str(latitude),
            "longitude": str(longitude),
            "updated_at": updated_at.isoformat(),
        }

    class Meta:
        model = Trip
        fields = [
            "id",
            "customer_username",
            "captain_username",
            "captain_location",
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
    minimum_offer = serializers.SerializerMethodField()
    maximum_offer = serializers.SerializerMethodField()

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
            "minimum_offer",
            "maximum_offer",
            "requested_at",
        ]
        read_only_fields = fields

    def _get_negotiation_range(self, obj):
        if not hasattr(obj, "_negotiation_range"):
            from trips.services.negotiation import (
                calculate_negotiation_range,
            )
            from django.core.exceptions import ValidationError

            try:
                obj._negotiation_range = calculate_negotiation_range(
                    city="Suez",
                    estimated_fare=obj.estimated_fare,
                )
            except ValidationError:
                obj._negotiation_range = None

        return obj._negotiation_range

    def get_minimum_offer(self, obj):
        negotiation_range = self._get_negotiation_range(obj)
        return negotiation_range[0] if negotiation_range else None

    def get_maximum_offer(self, obj):
        negotiation_range = self._get_negotiation_range(obj)
        return negotiation_range[1] if negotiation_range else None


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
    is_expired = serializers.SerializerMethodField()

    class Meta:
        model = CaptainOffer
        fields = [
            "id",
            "captain_username",
            "amount",
            "status",
            "is_expired",
            "expires_at",
            "created_at",
        ]
        read_only_fields = fields

    def get_is_expired(self, obj):
        return obj.status == CaptainOffer.Status.EXPIRED
        
        
        
class CaptainMyOfferSerializer(serializers.ModelSerializer):
    trip_id = serializers.IntegerField(read_only=True)
    pickup_address = serializers.CharField(
        source="trip.pickup_address",
        read_only=True,
    )
    destination_address = serializers.CharField(
        source="trip.destination_address",
        read_only=True,
    )
    trip_status = serializers.CharField(
        source="trip.status",
        read_only=True,
    )
    is_expired = serializers.SerializerMethodField()

    class Meta:
        model = CaptainOffer
        fields = [
            "id",
            "trip_id",
            "pickup_address",
            "destination_address",
            "trip_status",
            "amount",
            "status",
            "is_expired",
            "expires_at",
            "created_at",
        ]
        read_only_fields = fields

    def get_is_expired(self, obj):
        return obj.status == CaptainOffer.Status.EXPIRED


class TripReviewSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(
        source="customer.user.username",
        read_only=True,
    )
    trip_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = TripReview
        fields = [
            "id",
            "trip_id",
            "customer_name",
            "rating",
            "comment",
            "created_at",
        ]
        read_only_fields = fields


class TripReviewCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = TripReview
        fields = ["rating", "comment"]

    def validate_rating(self, value):
        if value < 1 or value > 5:
            raise serializers.ValidationError(
                "Rating must be between 1 and 5."
            )
        return value