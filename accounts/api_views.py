from decimal import Decimal, InvalidOperation

from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import CaptainProfile, Notification
from .services.notifications import (
    get_user_notifications,
    mark_notification_as_read,
    mark_all_notifications_as_read,
    get_unread_notification_count,
)
from .services.paymob import create_paymob_topup


class NotificationListAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        unread_only = request.query_params.get("unread_only", "false").lower(
        ) == "true"
        try:
            limit = int(request.query_params.get("limit", "50"))
        except (TypeError, ValueError):
            limit = 50

        # Hard maximum response bound regardless of query parameter.
        MAX_NOTIFICATION_LIMIT = 100
        if limit > MAX_NOTIFICATION_LIMIT:
            limit = MAX_NOTIFICATION_LIMIT
        if limit < 1:
            limit = 1

        notifications = get_user_notifications(
            user=request.user,
            unread_only=unread_only,
            limit=limit,
        )

        return Response(
            [
                {
                    "id": n.pk,
                    "type": n.type,
                    "title": n.title,
                    "message": n.message,
                    "payload": n.payload,
                    "is_read": n.is_read,
                    "created_at": n.created_at.isoformat(),
                    "updated_at": n.updated_at.isoformat(),
                }
                for n in notifications
            ]
        )


class NotificationUnreadCountAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        count = get_unread_notification_count(user=request.user)
        return Response({"unread_count": count})


class NotificationMarkReadAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, notification_id):
        notification = mark_notification_as_read(
            notification_id=notification_id,
            user=request.user,
        )

        if notification is None:
            return Response(
                {
                    "detail": "Notification not found or not owned by you."
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(
            {
                "id": notification.pk,
                "is_read": notification.is_read,
                "updated_at": notification.updated_at.isoformat(),
            }
        )


class NotificationMarkAllReadAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        count = mark_all_notifications_as_read(user=request.user)
        return Response({"marked_read": count})


class CaptainLocationUpdateAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        if request.user.role != "CAPTAIN":
            return Response(
                {"detail": "Only captains can update their location."},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            captain = request.user.captain_profile
        except CaptainProfile.DoesNotExist:
            return Response(
                {"detail": "Captain profile not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        latitude = request.data.get("latitude")
        longitude = request.data.get("longitude")

        try:
            latitude = Decimal(str(latitude))
            longitude = Decimal(str(longitude))
        except (InvalidOperation, TypeError, ValueError):
            return Response(
                {"detail": "Valid latitude and longitude are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if (
            not latitude.is_finite()
            or not longitude.is_finite()
            or not Decimal("-90") <= latitude <= Decimal("90")
            or not Decimal("-180") <= longitude <= Decimal("180")
        ):
            return Response(
                {"detail": "Coordinates are outside the valid range."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        captain.current_latitude = latitude
        captain.current_longitude = longitude
        captain.location_updated_at = timezone.now()
        captain.save(
            update_fields=[
                "current_latitude",
                "current_longitude",
                "location_updated_at",
                "updated_at",
            ]
        )

        return Response(
            {
                "detail": "Location updated successfully.",
                "latitude": str(captain.current_latitude),
                "longitude": str(captain.current_longitude),
                "location_updated_at": captain.location_updated_at.isoformat(),
            },
            status=status.HTTP_200_OK,
        )


class CaptainWalletPaymobTopUpAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user

        if user.role != "CAPTAIN":
            return Response(
                {"detail": "Only captains can top up their wallets."},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            captain = user.captain_profile
        except CaptainProfile.DoesNotExist:
            return Response(
                {"detail": "Captain profile not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        raw_amount = request.data.get("amount")

        if raw_amount is None:
            return Response(
                {"detail": "Amount is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            amount = Decimal(str(raw_amount))
        except (InvalidOperation, TypeError, ValueError):
            return Response(
                {"detail": "Enter a valid amount."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if (
            not amount.is_finite()
            or amount < Decimal("1.00")
            or amount > Decimal("99999999.99")
        ):
            return Response(
                {"detail": "Amount must be between 1.00 and 99999999.99 EGP."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            topup, checkout_url = create_paymob_topup(
                captain=captain,
                amount=amount,
            )
        except ValidationError as exc:
            return Response(
                {"detail": exc.messages},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except ImproperlyConfigured:
            return Response(
                {"detail": "Payment service is not configured."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response(
            {
                "detail": "Payment session created.",
                "topup_reference": topup.reference,
                "checkout_url": checkout_url,
            },
            status=status.HTTP_201_CREATED,
        )