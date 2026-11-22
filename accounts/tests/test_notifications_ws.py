import json
import time
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework import status

from channels.testing import WebsocketCommunicator
from project.asgi import application

from accounts.models import (
    CaptainProfile,
    CaptainWallet,
    CustomerProfile,
    Notification,
    User,
)
from accounts.services.notifications import (
    create_notification,
    get_unread_notification_count,
)

User = get_user_model()


def _make_user(username, role, password="test-password"):
    return User.objects.create_user(
        username=username,
        password=password,
        role=role,
    )


def _make_customer(username="cust"):
    user = _make_user(username, User.Role.CUSTOMER)
    return CustomerProfile.objects.create(user=user), user


def _make_captain(username="capt"):
    user = _make_user(username, User.Role.CAPTAIN)
    captain = CaptainProfile.objects.create(user=user)
    CaptainWallet.objects.create(captain=captain, balance=Decimal("500.00"))
    return captain, user


class NotificationAPITests(TestCase):
    """API endpoint tests: list, unread count, mark read, mark all read."""

    def setUp(self):
        self.customer, self.customer_user = _make_customer("api_customer")
        self.captain, self.captain_user = _make_captain("api_captain")
        self.client = APIClient()

    def _auth_client(self, user):
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    def test_notification_list_returns_only_own(self):
        create_notification(
            user=self.customer_user,
            type=Notification.Type.OFFER_RECEIVED,
            title="Customer Notification",
            message="m",
        )
        create_notification(
            user=self.captain_user,
            type=Notification.Type.OFFER_ACCEPTED,
            title="Captain Notification",
            message="m",
        )

        client = self._auth_client(self.customer_user)
        url = reverse("accounts:notification_list")
        response = client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["title"], "Customer Notification")

    def test_notification_list_unauthenticated_rejected(self):
        url = reverse("accounts:notification_list")
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_notification_list_unread_only_filter(self):
        n1 = create_notification(
            user=self.customer_user,
            type=Notification.Type.OFFER_RECEIVED,
            title="Unread",
            message="m",
        )
        n2 = create_notification(
            user=self.customer_user,
            type=Notification.Type.OFFER_ACCEPTED,
            title="Read",
            message="m",
        )
        from accounts.services.notifications import mark_notification_as_read
        mark_notification_as_read(notification_id=n2.pk, user=self.customer_user)

        client = self._auth_client(self.customer_user)
        url = reverse("accounts:notification_list") + "?unread_only=true"
        response = client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["title"], "Unread")

    def test_notification_unread_count(self):
        self.assertEqual(
            get_unread_notification_count(user=self.customer_user), 0
        )

        create_notification(
            user=self.customer_user,
            type=Notification.Type.OFFER_RECEIVED,
            title="A",
            message="m",
        )
        create_notification(
            user=self.customer_user,
            type=Notification.Type.OFFER_ACCEPTED,
            title="B",
            message="m",
        )
        create_notification(
            user=self.captain_user,
            type=Notification.Type.REVIEW_RECEIVED,
            title="C",
            message="m",
        )

        client = self._auth_client(self.customer_user)
        url = reverse("accounts:notification_unread_count")
        response = client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["unread_count"], 2)

        client2 = self._auth_client(self.captain_user)
        response2 = client2.get(url)
        self.assertEqual(response2.data["unread_count"], 1)

    def test_notification_mark_read_enforces_ownership(self):
        notification = create_notification(
            user=self.customer_user,
            type=Notification.Type.OFFER_RECEIVED,
            title="A",
            message="m",
        )

        # Captain tries to mark customer's notification as read -> 404
        client = self._auth_client(self.captain_user)
        url = reverse(
            "accounts:notification_mark_read",
            kwargs={"notification_id": notification.pk},
        )
        response = client.post(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        # Owner marks as read -> 200
        client2 = self._auth_client(self.customer_user)
        response2 = client2.post(url)
        self.assertEqual(response2.status_code, status.HTTP_200_OK)
        self.assertTrue(response2.data["is_read"])

    def test_notification_mark_read_nonexistent(self):
        client = self._auth_client(self.customer_user)
        url = reverse(
            "accounts:notification_mark_read",
            kwargs={"notification_id": 99999},
        )
        response = client.post(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_notification_mark_all_read(self):
        create_notification(
            user=self.customer_user,
            type=Notification.Type.OFFER_RECEIVED,
            title="A",
            message="m",
        )
        create_notification(
            user=self.customer_user,
            type=Notification.Type.OFFER_ACCEPTED,
            title="B",
            message="m",
        )
        create_notification(
            user=self.captain_user,
            type=Notification.Type.REVIEW_RECEIVED,
            title="C",
            message="m",
        )

        client = self._auth_client(self.customer_user)
        url = reverse("accounts:notification_mark_all_read")
        response = client.post(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["marked_read"], 2)
        self.assertEqual(
            get_unread_notification_count(user=self.customer_user), 0
        )
        self.assertEqual(
            get_unread_notification_count(user=self.captain_user), 1
        )


class NotificationWebSocketTests(TransactionTestCase):
    """WebSocket consumer tests: authentication, per-user groups.

    Uses TransactionTestCase (not TestCase) because the consumer performs
    database writes via sync_to_async in a separate thread; a TestCase
    transaction is not visible to that thread and would lock SQLite.
    """

    def setUp(self):
        self.customer, self.customer_user = _make_customer("ws_customer")
        self.captain, self.captain_user = _make_captain("ws_captain")

    def test_websocket_authenticated_user_can_connect(self):
        import asyncio

        async def run():
            communicator = WebsocketCommunicator(
                application,
                "/ws/notifications/",
                headers=[(b"origin", b"https://snipergo-production.up.railway.app")],
            )
            communicator.scope["user"] = self.customer_user
            connected, _ = await communicator.connect()
            self.assertTrue(connected)
            await communicator.disconnect()

        asyncio.run(run())

    def test_websocket_anonymous_user_rejected(self):
        import asyncio

        from django.contrib.auth.models import AnonymousUser

        async def run():
            communicator = WebsocketCommunicator(
                application,
                "/ws/notifications/",
            )
            communicator.scope["user"] = AnonymousUser()
            connected, _ = await communicator.connect()
            self.assertFalse(connected)

        asyncio.run(run())

    def test_websocket_per_user_group_isolation(self):
        import asyncio

        async def run():
            # Customer connects
            customer_comm = WebsocketCommunicator(
                application,
                "/ws/notifications/",
                headers=[(b"origin", b"https://snipergo-production.up.railway.app")],
            )
            customer_comm.scope["user"] = self.customer_user
            customer_connected, _ = await customer_comm.connect()
            self.assertTrue(customer_connected)

            # Captain connects
            captain_comm = WebsocketCommunicator(
                application,
                "/ws/notifications/",
                headers=[(b"origin", b"https://snipergo-production.up.railway.app")],
            )
            captain_comm.scope["user"] = self.captain_user
            captain_connected, _ = await captain_comm.connect()
            self.assertTrue(captain_connected)

            # Send a notification event to customer's group only
            from channels.layers import get_channel_layer
            channel_layer = get_channel_layer()

            await channel_layer.group_send(
                f"notifications_{self.customer_user.pk}",
                {
                    "type": "notification.message",
                    "notification_id": 1,
                    "notification_type": "OFFER_RECEIVED",
                    "title": "Customer Only",
                    "message": "m",
                    "payload": {},
                    "created_at": "2026-01-01T00:00:00Z",
                },
            )

            # Customer should receive it
            customer_response = await customer_comm.receive_json_from()
            self.assertEqual(
                customer_response["notification"]["title"], "Customer Only"
            )

            # Captain should NOT receive it (timeout on empty queue)
            captain_has_message = False
            try:
                await asyncio.wait_for(
                    captain_comm.receive_json_from(), timeout=0.5
                )
                captain_has_message = True
            except asyncio.TimeoutError:
                captain_has_message = False
            self.assertFalse(captain_has_message)

            await customer_comm.disconnect()
            await captain_comm.disconnect()

        asyncio.run(run())

    def test_websocket_mark_read_action_and_idempotency(self):
        import asyncio

        notification = create_notification(
            user=self.customer_user,
            type=Notification.Type.OFFER_RECEIVED,
            title="WS Read",
            message="m",
        )

        async def run():
            communicator = WebsocketCommunicator(
                application,
                "/ws/notifications/",
                headers=[(b"origin", b"https://snipergo-production.up.railway.app")],
            )
            communicator.scope["user"] = self.customer_user
            connected, _ = await communicator.connect()
            self.assertTrue(connected)

            # Owner marks own notification as read via WebSocket.
            await communicator.send_json_to(
                {"action": "mark_read", "notification_id": notification.pk}
            )
            response = await communicator.receive_json_from()
            self.assertEqual(response["action"], "marked_read")
            self.assertEqual(response["notification_id"], notification.pk)

            # Repeated mark_read stays consistent (no error, still owned).
            await communicator.send_json_to(
                {"action": "mark_read", "notification_id": notification.pk}
            )
            response2 = await communicator.receive_json_from()
            self.assertEqual(response2["action"], "marked_read")

            await communicator.disconnect()

        asyncio.run(run())

        notification.refresh_from_db()
        self.assertTrue(notification.is_read)
        # Only one notification exists; marking twice did not duplicate it.
        self.assertEqual(
            Notification.objects.filter(pk=notification.pk).count(), 1
        )

    def test_websocket_mark_read_rejects_other_users_notification(self):
        import asyncio

        other_customer, other_user = _make_customer("ws_idor_other")
        other_notification = create_notification(
            user=other_user,
            type=Notification.Type.OFFER_RECEIVED,
            title="Not Yours",
            message="m",
        )

        async def run():
            communicator = WebsocketCommunicator(
                application,
                "/ws/notifications/",
                headers=[(b"origin", b"https://snipergo-production.up.railway.app")],
            )
            communicator.scope["user"] = self.captain_user
            connected, _ = await communicator.connect()
            self.assertTrue(connected)

            # Captain tries to mark another user's notification as read.
            await communicator.send_json_to(
                {"action": "mark_read", "notification_id": other_notification.pk}
            )
            response = await communicator.receive_json_from()
            self.assertIn("error", response)

            await communicator.disconnect()

        asyncio.run(run())

        other_notification.refresh_from_db()
        self.assertFalse(other_notification.is_read)

    def test_websocket_rejects_invalid_origin(self):
        import asyncio

        async def run():
            communicator = WebsocketCommunicator(
                application,
                "/ws/notifications/",
                headers=[(b"origin", b"https://evil.example.com")],
            )
            communicator.scope["user"] = self.customer_user
            connected, _ = await communicator.connect()
            self.assertFalse(connected)

        asyncio.run(run())

    def test_websocket_allows_trusted_origin(self):
        import asyncio
        from django.test import override_settings

        async def run():
            with override_settings(
                CSRF_TRUSTED_ORIGINS=[
                    "https://snipergo-production.up.railway.app"
                ]
            ):
                communicator = WebsocketCommunicator(
                    application,
                    "/ws/notifications/",
                    headers=[
                        (b"origin", b"https://snipergo-production.up.railway.app")
                    ],
                )
                communicator.scope["user"] = self.customer_user
                connected, _ = await communicator.connect()
                self.assertTrue(connected)
                await communicator.disconnect()

        asyncio.run(run())

