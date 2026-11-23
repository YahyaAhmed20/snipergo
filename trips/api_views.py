# ===== الاستيرادات =====
from datetime import timedelta
import requests

from django.conf import settings
from rest_framework.throttling import SimpleRateThrottle, UserRateThrottle

from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404
from django.utils import timezone

from trips.services.finance import confirm_cash_collection
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
    CaptainMyOfferSerializer,
    TripReviewSerializer,
    TripReviewCreateSerializer,
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
    expire_pending_offers_for_trips,
    expire_trip_if_needed,
)
from trips.services.reviews import (
    create_trip_review,
    get_captain_reviews,
    recalculate_captain_rating,
)

class TripCreateThrottle(UserRateThrottle):
    rate = "10/min"
    scope = "trip_create"


class CaptainOfferCreateThrottle(UserRateThrottle):
    rate = "30/min"
    scope = "captain_offer_create"


class CaptainOfferAcceptThrottle(UserRateThrottle):
    rate = "20/min"
    scope = "captain_offer_accept"


class CashCollectionConfirmThrottle(UserRateThrottle):
    rate = "10/min"
    scope = "cash_collection_confirm"


class TripReviewCreateThrottle(UserRateThrottle):
    rate = "5/min"
    scope = "trip_review_create"


# ===== Trip Create =====
class TripCreateAPIView(generics.CreateAPIView):
    serializer_class = TripCreateSerializer
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [TripCreateThrottle]

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

        expire_pending_offers_for_trips(trip_ids=[trip.pk])

        return (
            CaptainOffer.objects
            .filter(trip=trip)
            .select_related("captain__user")
            .order_by("-created_at")
        )


# ===== Captain My Offers =====
class CaptainMyOffersAPIView(generics.ListAPIView):
    serializer_class = CaptainMyOfferSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user

        if user.role != "CAPTAIN":
            raise PermissionDenied(
                "Only captains can view their submitted offers."
            )

        try:
            captain = user.captain_profile
        except CaptainProfile.DoesNotExist:
            raise PermissionDenied(
                "Captain profile not found."
            )

        pending_trip_ids = list(
            CaptainOffer.objects
            .filter(
                captain=captain,
                status=CaptainOffer.Status.PENDING,
            )
            .values_list("trip_id", flat=True)
            .distinct()
        )
        expire_pending_offers_for_trips(trip_ids=pending_trip_ids)

        return (
            CaptainOffer.objects
            .filter(captain=captain)
            .select_related("trip")
            .order_by("-created_at")
        )


# ===== Accept Captain Offer =====
class CaptainOfferAcceptAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [CaptainOfferAcceptThrottle]

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


class AvailableTripsAPIView(generics.ListAPIView):
    serializer_class = AvailableTripSerializer
    permission_classes = [permissions.IsAuthenticated]

    MATCHING_RADIUS_KM = Decimal("10")

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

        if (
            captain.current_latitude is None
            or captain.current_longitude is None
            or captain.location_updated_at is None
            or captain.location_updated_at < now - timedelta(minutes=2)
            or captain.location_updated_at > now + timedelta(seconds=30)
        ):
            raise PermissionDenied(
                "A recent captain location is required. "
                "Update your location and try again."
            )

        candidate_trip_ids = Trip.objects.filter(
            status=Trip.Status.REQUESTED,
            captain__isnull=True,
        ).values_list("pk", flat=True)

        for trip_id in candidate_trip_ids.iterator():
            expire_trip_if_needed(trip_id=trip_id)

        trips = Trip.objects.filter(
            status=Trip.Status.REQUESTED,
            captain__isnull=True,
        ).select_related(
            "customer__user",
        )

        nearby_trips = []

        for trip in trips:
            try:
                distance = calculate_distance_km(
                    pickup_latitude=captain.current_latitude,
                    pickup_longitude=captain.current_longitude,
                    destination_latitude=trip.pickup_latitude,
                    destination_longitude=trip.pickup_longitude,
                )
            except ValidationError:
                continue

            if distance <= self.MATCHING_RADIUS_KM:
                nearby_trips.append((distance, trip))

        nearby_trips.sort(
            key=lambda item: (
                item[0],
                item[1].requested_at,
                item[1].pk,
            )
        )

        return [trip for _, trip in nearby_trips]


class CaptainMyTripsAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if request.user.role != "CAPTAIN":
            raise PermissionDenied("Only captains can view their trips.")

        try:
            captain = request.user.captain_profile
        except CaptainProfile.DoesNotExist:
            raise PermissionDenied("Captain profile is required.")

        from django.db.models import Q
        from trips.models import TripFinancialRecord

        active_statuses = [
            Trip.Status.ACCEPTED,
            Trip.Status.ARRIVING,
            Trip.Status.ARRIVED,
            Trip.Status.IN_PROGRESS,
        ]

        trips = (
            Trip.objects.filter(captain=captain)
            .filter(
                Q(status__in=active_statuses)
                | Q(
                    status=Trip.Status.COMPLETED,
                    payment_method=Trip.PaymentMethod.CASH,
                    financial_record__collection_status__in=[
                        TripFinancialRecord.CollectionStatus.PENDING,
                        TripFinancialRecord.CollectionStatus.FAILED,
                    ],
                )
            )
            .select_related("customer__user", "captain__user", "vehicle")
            .order_by("-accepted_at", "-pk")
            .distinct()
        )

        results = TripDetailSerializer(trips, many=True).data

        for item, trip in zip(results, trips):
            try:
                item["collection_status"] = trip.financial_record.collection_status
            except TripFinancialRecord.DoesNotExist:
                item["collection_status"] = None

        return Response(
            results,
            status=status.HTTP_200_OK,
        )
        
        
        

class CustomerMyTripsAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        from rest_framework.exceptions import PermissionDenied
        from accounts.models import CustomerProfile
        from trips.models import Trip

        if request.user.role != "CUSTOMER":
            raise PermissionDenied(
                "هذه الصفحة مخصصة للعملاء فقط."
            )

        try:
            customer = request.user.customer_profile
        except CustomerProfile.DoesNotExist:
            raise PermissionDenied(
                "ملف العميل غير موجود."
            )

        trips = (
            Trip.objects
            .filter(customer=customer)
            .select_related("captain__user", "vehicle")
            .order_by("-requested_at")
        )

        status_labels = {
            "REQUESTED": "بانتظار عروض الكباتن",
            "ACCEPTED": "تم قبول الرحلة",
            "ARRIVING": "الكابتن في الطريق",
            "ARRIVED": "الكابتن وصل",
            "IN_PROGRESS": "الرحلة جارية",
            "COMPLETED": "مكتملة",
            "CANCELLED": "ملغاة",
            "EXPIRED": "انتهت",
        }

        results = []

        for trip in trips:
            captain_name = None

            if trip.captain_id:
                captain_name = (
                    trip.captain.user.get_full_name()
                    or trip.captain.user.username
                )

            results.append({
                "id": trip.pk,
                "status": trip.status,
                "status_label": status_labels.get(
                    trip.status, trip.status
                ),
                "pickup_address": trip.pickup_address,
                "destination_address": trip.destination_address,
                "payment_method": trip.payment_method,
                "estimated_fare": trip.estimated_fare,
                "agreed_fare": trip.agreed_fare,
                "final_fare": trip.final_fare,
                "captain_name": captain_name,
                "requested_at": trip.requested_at,
                "completed_at": trip.completed_at,
                "cancelled_at": trip.cancelled_at,
                "cancellation_reason": trip.cancellation_reason,
            })

        return Response({"results": results})


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
    throttle_classes = [CaptainOfferCreateThrottle]

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
        
        
        
class CashCollectionConfirmAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [CashCollectionConfirmThrottle]

    def post(self, request, pk):
        try:
            record = confirm_cash_collection(
                trip_id=pk,
                confirmed_by=request.user,
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
            {
                "trip_id": record.trip_id,
                "collection_status": record.collection_status,
                "gross_fare": str(record.gross_fare),
            },
            status=status.HTTP_200_OK,
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
        
        
class TripReviewCreateAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [TripReviewCreateThrottle]

    def post(self, request, pk):
        user = request.user

        if user.role != "CUSTOMER":
            return Response(
                {"detail": "Only customers can submit trip reviews."},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            customer = CustomerProfile.objects.get(user_id=user.pk)
        except CustomerProfile.DoesNotExist:
            return Response(
                {"detail": "Customer profile is required."},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            trip = Trip.objects.select_for_update().get(pk=pk)
        except Trip.DoesNotExist:
            return Response(
                {"detail": "Trip not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = TripReviewCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            review = create_trip_review(
                trip=trip,
                customer=customer,
                rating=serializer.validated_data["rating"],
                comment=serializer.validated_data.get("comment", ""),
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
            TripReviewSerializer(review).data,
            status=status.HTTP_201_CREATED,
        )


class CaptainMyReviewsAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user

        if user.role != "CAPTAIN":
            return Response(
                {"detail": "Only captains can view their reviews."},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            captain = user.captain_profile
        except CaptainProfile.DoesNotExist:
            return Response(
                {"detail": "Captain profile not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        reviews = get_captain_reviews(captain=captain)

        return Response(
            TripReviewSerializer(reviews, many=True).data,
            status=status.HTTP_200_OK,
        )


class AddressSearchThrottle(SimpleRateThrottle):
    scope = "address_search"
    rate = "20/min"

    def get_cache_key(self, request, view):
        if not request.user.is_authenticated:
            return None

        return self.cache_format % {
            "scope": self.scope,
            "ident": request.user.pk,
        }


class AddressSearchAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [AddressSearchThrottle]

    GOOGLE_URL = "https://places.googleapis.com/v1/places:searchText"

    def get(self, request):
        query = request.query_params.get("q", "").strip()

        if len(query) < 3 or len(query) > 150:
            return Response(
                {"detail": "Search query must be between 3 and 150 characters."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if request.user.role != "CUSTOMER":
            raise PermissionDenied("Only customers can search addresses.")

        api_key = getattr(settings, "GOOGLE_PLACES_API_KEY", "")

        if not api_key:
            return Response({"features": [], "provider": "unavailable"})

        payload = {
            "textQuery": f"{query}, Suez, Egypt",
            "languageCode": "ar",
            "regionCode": "EG",
            "locationBias": {
                "rectangle": {
                    "low": {
                        "latitude": 29.90,
                        "longitude": 32.45,
                    },
                    "high": {
                        "latitude": 30.10,
                        "longitude": 32.65,
                    },
                }
            },
            "maxResultCount": 10,
        }

        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": (
                "places.id,"
                "places.displayName,"
                "places.formattedAddress,"
                "places.location"
            ),
        }

        try:
            response = requests.post(
                self.GOOGLE_URL,
                json=payload,
                headers=headers,
                timeout=5,
            )
            response.raise_for_status()
            data = response.json()

        except requests.RequestException:
            return Response(
                {"features": [], "provider": "unavailable"},
                status=status.HTTP_200_OK,
            )

        features = []

        for place in data.get("places", []):
            location = place.get("location", {})
            latitude = location.get("latitude")
            longitude = location.get("longitude")

            if latitude is None or longitude is None:
                continue

            name = place.get("displayName", {}).get("text", "")
            address = place.get("formattedAddress", "")

            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [longitude, latitude],
                },
                "properties": {
                    "name": name or address,
                    "street": address,
                    "country": "مصر",
                    "provider": "google",
                },
            })

        return Response({
            "type": "FeatureCollection",
            "features": features,
            "provider": "google",
        })