from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction

from accounts.models import Notification, User


def create_notification(*, user, type, title, message="", payload=None):
    """
    Create and persist a notification for a user.

    The notification is written to the database within the caller's
    transaction. WebSocket dispatch (if any) must be scheduled via
    transaction.on_commit() by the caller, after this function returns.

    Returns the created Notification instance.
    """
    if not isinstance(user, User):
        user = User.objects.get(pk=user)

    return Notification.objects.create(
        user=user,
        type=type,
        title=title,
        message=message,
        payload=payload or {},
    )


async def dispatch_notification_websocket(notification):
    """
    Dispatch a persisted notification via WebSocket to the user's group.

    Intended to be called from transaction.on_commit() via async_to_sync()
    after the notification has been committed to the database. Safe to call
    after rollback — the callback simply will not fire.
    """
    channel_layer = get_channel_layer()
    group_name = f"notifications_{notification.user.pk}"
    await channel_layer.group_send(
        group_name,
        {
            "type": "notification.message",
            "notification_id": notification.pk,
            "notification_type": notification.type,
            "title": notification.title,
            "message": notification.message,
            "payload": notification.payload,
            "created_at": notification.created_at.isoformat(),
        },
    )


def get_user_notifications(*, user, unread_only=False, limit=50):
    """
    Return notifications for a user, newest first.

    Enforces strict per-user access — only the authenticated user's
    notifications are returned. No cross-user leakage.
    """
    if not isinstance(user, User):
        user = User.objects.get(pk=user)

    qs = (
        Notification.objects
        .filter(user=user)
        .order_by("-created_at")
    )

    if unread_only:
        qs = qs.filter(is_read=False)

    return list(qs[:limit])


def mark_notification_as_read(*, notification_id, user):
    """
    Mark a notification as read, enforcing ownership.

    Returns the updated Notification instance, or None if not found
    or not owned by the user.
    """
    if not isinstance(user, User):
        user = User.objects.get(pk=user)

    try:
        notification = Notification.objects.get(
            pk=notification_id,
            user=user,
        )
    except Notification.DoesNotExist:
        return None

    if not notification.is_read:
        notification.is_read = True
        notification.save(update_fields=["is_read", "updated_at"])

    return notification


def mark_all_notifications_as_read(*, user):
    """
    Mark all of a user's unread notifications as read.

    Returns the number of notifications updated.
    """
    if not isinstance(user, User):
        user = User.objects.get(pk=user)

    return Notification.objects.filter(
        user=user,
        is_read=False,
    ).update(is_read=True)


def get_unread_notification_count(*, user):
    """
    Return the count of unread notifications for a user.
    """
    if not isinstance(user, User):
        user = User.objects.get(pk=user)

    return Notification.objects.filter(
        user=user,
        is_read=False,
    ).count()


def notify_user(*, user, type, title, message="", payload=None):
    """
    Convenience wrapper: create a notification for a user.

    Intended to be called from business services (offers, lifecycle,
    reviews) within their atomic transaction blocks. WebSocket dispatch
    is handled separately via transaction.on_commit() in stage 2.

    Returns the created Notification instance.
    """
    return create_notification(
        user=user,
        type=type,
        title=title,
        message=message,
        payload=payload,
    )
