import json

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings


class NotificationConsumer(AsyncWebsocketConsumer):
    """
    Authenticated WebSocket consumer for real-time notifications.

    Authentication:
      - Relies on AuthMiddlewareStack in project/asgi.py, which populates
        self.scope["user"] from the Django session.
      - Anonymous users are rejected on connect.
      - No query-token authentication is used (session auth only).

    Authorization:
      - Each authenticated user joins a server-controlled per-user group
        named "notifications_<user_id>". Users cannot choose or spoof
        another user's group name.
      - Users only receive notifications sent to their own group.

    Origin validation:
      - WebSocket connections are checked against CSRF_TRUSTED_ORIGINS.
      - Connections from untrusted origins are rejected.
    """

    async def connect(self):
        self.user = self.scope.get("user")

        if not self.user or not self.user.is_authenticated:
            await self.close(code=4001)
            return

        if not self._origin_allowed():
            await self.close(code=4002)
            return

        # Server-controlled per-user group name (not user-controlled).
        self.group_name = f"notifications_{self.user.pk}"

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(
                self.group_name, self.channel_name
            )

    async def receive(self, text_data):
        """
        Handle incoming WebSocket messages.

        Clients may mark a notification as read by sending:
            {"action": "mark_read", "notification_id": <id>}

        The consumer enforces ownership via the notification service.
        """
        try:
            data = json.loads(text_data or "{}")
        except (json.JSONDecodeError, TypeError):
            await self.send_json({"error": "Invalid JSON."})
            return

        action = data.get("action")

        if action == "mark_read":
            notification_id = data.get("notification_id")
            if not notification_id:
                await self.send_json(
                    {"error": "notification_id is required."}
                )
                return

            from accounts.services.notifications import (
                mark_notification_as_read,
            )

            notification = await sync_to_async(mark_notification_as_read)(
                notification_id=notification_id,
                user=self.user,
            )

            if notification is None:
                await self.send_json(
                    {
                        "error": "Notification not found or not owned by you."
                    }
                )
            else:
                await self.send_json(
                    {
                        "action": "marked_read",
                        "notification_id": notification.pk,
                    }
                )
        else:
            await self.send_json({"error": "Unknown action."})

    async def notification_message(self, event):
        """
        Handler for group-sent notification events.

        Receives events from channel_layer.group_send and forwards them
        to the connected WebSocket client.
        """
        await self.send_json(
            {
                "type": "notification",
                "notification": {
                    "id": event.get("notification_id"),
                    "type": event.get("notification_type"),
                    "title": event.get("title"),
                    "message": event.get("message", ""),
                    "payload": event.get("payload", {}),
                    "created_at": event.get("created_at"),
                },
            }
        )

    def _origin_allowed(self):
        """
        Validate the WebSocket origin against CSRF_TRUSTED_ORIGINS.

        Returns True if the origin is allowed, False otherwise.
        """
        request = self.scope.get("headers", {})
        origin = None

        for key, value in request:
            if key == b"origin":
                origin = value.decode("utf-8")
                break

        if not origin:
            # No Origin header: reject browser-originated connections.
            # Non-browser clients must still provide an Origin or be
            # explicitly allowed via CSRF_TRUSTED_ORIGINS.
            return False

        trusted_origins = getattr(settings, "CSRF_TRUSTED_ORIGINS", [])

        # Normalize origins by stripping scheme for comparison.
        def normalize(o):
            if o.startswith("http://") or o.startswith("https://"):
                o = o.split("://", 1)[1]
            return o.rstrip("/").lower()

        normalized_origin = normalize(origin)
        return any(
            normalized_origin == normalize(t)
            for t in trusted_origins
        )

    async def send_json(self, content):
        await self.send(text_data=json.dumps(content))
