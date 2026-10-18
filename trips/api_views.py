# ===== الاستيرادات =====
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404
from django.utils import timezone

from rest_framework import generics, permissions, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import (
    CaptainProfile,
    CaptainWallet,
    CustomerProfile,
)
from trips.models import CaptainOffer, Trip
from trips.serializers import (
    TripCreateSerializer,
    TripDetailSerializer,
    AvailableTripSerializer,
    CaptainOfferCreateSerializer,
    CaptainOfferListSerializer,
)
from trips.services.distance import calculate_distance_km
from trips.services.lifecycle import (
    accept_trip,
    mark_captain_arriving,
    mark_captain_arrived,
    start_trip,
    complete_trip,
    cancel_trip,
)
from trips.services.offers import (
    accept_captain_offer,
    create_captain_offer,
)


# ===== Trip Create =====
class TripCreateAPIView(generics.CreateAPIView):
    serializer_class = TripCreateSerializer
    permission_classes = [permissions.IsAuthenticated]

    def perform_create(self, serializer):
        user = self.request.user

        if user.role != "CUSTOMER":
            raise PermissionDenied("Only customers can create trips.")

        if not CustomerProfile.objects.filter(user=user).exists():
            raise PermissionDenied("Customer profile is required.")

        serializer.save()


# ===== Trip Detail =====
class TripDetailAPIView(generics.RetrieveAPIView):
    serializer_class = TripDetailSerializer
    permission_classes = [permissions.IsAuthenticated]
    queryset = Trip.objects.select_related(
        "customer__user",
        "captain__user",
    )

    def get_object(self):
        trip = get_object_or_404(
            self.get_queryset(),
            pk=self.kwargs["pk"],
        )

        user = self.request.user

        is_customer = trip.customer.user_id == user.pk
        is_captain = (
            trip.captain is not None
            and trip.captain.user_id == user.pk
        )
        is_admin = user.is_staff or user.is_superuser or user.role == "ADMIN"

        if not (is_customer or is_captain or is_admin):
            raise PermissionDenied(
                "You do not have permission to view this trip."
            )

        return trip


# ===== Trip Offers List =====
class CaptainOfferListAPIView(generics.ListAPIView):
    serializer_class = CaptainOfferListSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        trip = get_object_or_404(
            Trip.objects.select_related("customer__user"),
            pk=self.kwargs["pk"],
        )

        user = self.request.user

        is_customer = trip.customer.user_id == user.pk
        is_admin = (
            user.is_staff
            or user.is_superuser
            or user.role == "ADMIN"
        )

        if not (is_customer or is_admin):
            raise PermissionDenied(
                "You do not have permission to view these offers."
            )

        return (
            CaptainOffer.objects
            .filter(trip=trip)
            .select_related("captain__user")
            .order_by("-created_at")
        )


# ===== Accept Captain Offer =====
class CaptainOfferAcceptAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk, offer_id):
        user = request.user

        if user.role != "CUSTOMER":
            raise PermissionDenied(
                "Only customers can accept captain offers."
            )

        try:
            customer = CustomerProfile.objects.get(user_id=user.pk)
        except CustomerProfile.DoesNotExist:
            raise PermissionDenied(
                "Customer profile is required."
            )

        # Verify the offer belongs to the trip in the URL
        offer = get_object_or_404(
            CaptainOffer.objects.only("id", "trip_id"),
            pk=offer_id,
            trip_id=pk,
        )

        try:
            accepted_offer = accept_captain_offer(
                offer_id=offer.pk,
                customer=customer,
            )
        except ValidationError as exc:
            return Response(
                {"detail": exc.messages},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            CaptainOfferListSerializer(accepted_offer).data,
            status=status.HTTP_200_OK,
        )


# ===== Available Trips (for captains) =====
class AvailableTripsAPIView(generics.ListAPIView):
    serializer_class = AvailableTripSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user

        if user.role != "CAPTAIN":
            raise PermissionDenied(
                "Only captains can view available trips."
            )

        try:
            captain = CaptainProfile.objects.get(user_id=user.pk)
        except CaptainProfile.DoesNotExist:
            raise PermissionDenied("Captain profile is required.")

        if (
            captain.approval_status
            != CaptainProfile.ApprovalStatus.APPROVED
            or captain.operational_status
            != CaptainProfile.OperationalStatus.ONLINE
        ):
            raise PermissionDenied(
                "Only approved online captains can view available trips."
            )

        try:
            wallet = CaptainWallet.objects.get(captain=captain)
        except CaptainWallet.DoesNotExist:
            raise PermissionDenied(
                "A funded wallet is required to view available trips."
            )

        if wallet.available_balance <= 0:
            raise PermissionDenied(
                "Insufficient available wallet balance."
            )

        now = timezone.now()
        location_max_age = timedelta(minutes=2)

        if (
            captain.current_latitude is None
            or captain.current_longitude is None
            or captain.location_updated_at is None
            or captain.location_updated_at < now - location_max_age
            or captain.location_updated_at > now + timedelta(seconds=30)
        ):
            raise PermissionDenied(
                "A recent captain location is required. Update your location and try again."
            )

        trips = list(
            Trip.objects.filter(
                status=Trip.Status.REQUESTED,
                captain__isnull=True,
            ).select_related(
                "customer__user",
            )
        )

        def pickup_distance(trip):
            try:
                return calculate_distance_km(
                    pickup_latitude=captain.current_latitude,
                    pickup_longitude=captain.current_longitude,
                    destination_latitude=trip.pickup_latitude,
                    destination_longitude=trip.pickup_longitude,
                )
            except ValidationError:
                return Decimal("Infinity")

        return sorted(
            trips,
            key=lambda trip: (
                pickup_distance(trip),
                trip.requested_at,
                trip.pk,
            ),
        )


# ===== Captain Trip Actions =====
class CaptainTripActionAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk, action):
        if request.user.role != "CAPTAIN":
            raise PermissionDenied("Only captains can perform this action.")

        try:
            captain = request.user.captain_profile
        except CaptainProfile.DoesNotExist:
            raise PermissionDenied("Captain profile is required.")

        try:
            if action == "accept":
                trip = accept_trip(trip_id=pk, captain=captain)

            elif action == "arriving":
                trip = mark_captain_arriving(trip_id=pk, captain=captain)

            elif action == "arrived":
                trip = mark_captain_arrived(trip_id=pk, captain=captain)

            elif action == "start":
                trip = start_trip(trip_id=pk, captain=captain)

            elif action == "complete":
                # Final fare must always equal the agreed fare.
                trip = complete_trip(
                    trip_id=pk,
                    captain=captain,
                )

            else:
                return Response(
                    {"detail": "Unknown action."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        except ValidationError as exc:
            return Response(
                {"detail": exc.messages},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            TripDetailSerializer(trip).data,
            status=status.HTTP_200_OK,
        )


# ===== Captain Offer Create =====
class CaptainOfferCreateAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        if request.user.role != "CAPTAIN":
            raise PermissionDenied("Only captains can submit offers.")

        try:
            captain = CaptainProfile.objects.get(user_id=request.user.pk)
        except CaptainProfile.DoesNotExist:
            raise PermissionDenied("Captain profile is required.")

        serializer = CaptainOfferCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            offer = create_captain_offer(
                trip_id=pk,
                captain=captain,
                amount=serializer.validated_data["amount"],
            )
        except Trip.DoesNotExist:
            return Response(
                {"detail": "Trip not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        except ValidationError as exc:
            return Response(
                {"detail": exc.messages},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            CaptainOfferCreateSerializer(offer).data,
            status=status.HTTP_201_CREATED,
        )


class TripCancelAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        reason = request.data.get("reason")

        if not isinstance(reason, str) or not reason.strip():
            return Response(
                {"detail": "A cancellation reason is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            trip = cancel_trip(
                trip_id=pk,
                cancelled_by=request.user,
                reason=reason,
            )
        except Trip.DoesNotExist:
            return Response(
                {"detail": "Trip not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        except ValidationError as exc:
            return Response(
                {"detail": exc.messages},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            TripDetailSerializer(trip).data,
            status=status.HTTP_200_OK,
        )