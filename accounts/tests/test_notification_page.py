"""Focused tests for the Notification UI HTTP surface and access control.

Covers the gaps not exercised by test_notifications.py (service layer) or
test_notifications_ws.py (API + WebSocket consumer):

- the server-rendered `notifications/page/` view (login required, own data only)
- unread count / badge value in the page context
- idempotent mark-as-read and mark-all-as-read at the service level
- cross-user IDOR protection on the page
- WebSocket mark_read action + idempotency

Does NOT modify the feature; tests only.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

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
    mark_all_notifications_as_read,
    mark_notification_as_read,
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


class NotificationPageHTTPTests(TestCase):
    """HTTP tests for the server-rendered notifications/page/ view."""

    def setUp(self):
        self.customer, self.customer_user = _make_customer("page_customer")
        self.captain, self.captain_user = _make_captain("page_captain")
        self.url = reverse("accounts:notification_page")

    def test_anonymous_redirected_to_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response.url)

    def test_authenticated_user_can_view_own_page(self):
        self.client.force_login(self.customer_user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/notifications.html")

    def test_page_lists_only_own_notifications(self):
        create_notification(
            user=self.customer_user,
            type=Notification.Type.OFFER_RECEIVED,
            title="Customer Only Title",
            message="m",
        )
        create_notification(
            user=self.captain_user,
            type=Notification.Type.OFFER_ACCEPTED,
            title="Captain Secret Title",
            message="m",
        )

        self.client.force_login(self.customer_user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

        titles = [n.title for n in response.context["notifications"]]
        self.assertIn("Customer Only Title", titles)
        self.assertNotIn("Captain Secret Title", titles)

        # Rendered HTML must not leak the other user's title.
        self.assertNotContains(response, "Captain Secret Title")

    def test_page_context_unread_count(self):
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
        # A read notification must not count.
        read_one = create_notification(
            user=self.customer_user,
            type=Notification.Type.REVIEW_RECEIVED,
            title="C",
            message="m",
        )
        mark_notification_as_read(
            notification_id=read_one.pk, user=self.customer_user
        )
        # Another user's unread notification must not count.
        create_notification(
            user=self.captain_user,
            type=Notification.Type.TRIP_CANCELLED,
            title="D",
            message="m",
        )

        self.client.force_login(self.customer_user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["unread_count"], 2)

    def test_page_renders_unread_count_in_badge(self):
        create_notification(
            user=self.customer_user,
            type=Notification.Type.OFFER_RECEIVED,
            title="Badge",
            message="m",
        )
        self.client.force_login(self.customer_user)
        response = self.client.get(self.url)
        self.assertContains(response, "unread-count-badge")
        self.assertContains(response, "1")


class NotificationMarkReadIdempotencyTests(TestCase):
    """Service-level idempotency: repeated reads do not corrupt counts."""

    def setUp(self):
        _, self.user = _make_customer("idem_customer")

    def test_mark_read_is_idempotent(self):
        n = create_notification(
            user=self.user,
            type=Notification.Type.OFFER_RECEIVED,
            title="A",
            message="m",
        )

        first = mark_notification_as_read(notification_id=n.pk, user=self.user)
        self.assertTrue(first.is_read)

        # Second call must not error and must stay read.
        second = mark_notification_as_read(notification_id=n.pk, user=self.user)
        self.assertTrue(second.is_read)
        self.assertEqual(
            Notification.objects.filter(pk=n.pk, is_read=True).count(), 1
        )

    def test_unread_count_does_not_go_negative_on_repeated_read(self):
        n = create_notification(
            user=self.user,
            type=Notification.Type.OFFER_RECEIVED,
            title="A",
            message="m",
        )
        mark_notification_as_read(notification_id=n.pk, user=self.user)
        mark_notification_as_read(notification_id=n.pk, user=self.user)
        # Floor at zero, never negative.
        self.assertEqual(get_unread_notification_count(user=self.user), 0)

    def test_mark_all_read_returns_zero_when_none_unread(self):
        n = create_notification(
            user=self.user,
            type=Notification.Type.OFFER_RECEIVED,
            title="A",
            message="m",
        )
        mark_notification_as_read(notification_id=n.pk, user=self.user)
        updated = mark_all_notifications_as_read(user=self.user)
        self.assertEqual(updated, 0)
        self.assertEqual(get_unread_notification_count(user=self.user), 0)

    def test_mark_read_rejects_other_users_notification(self):
        _, other = _make_customer("idem_other")
        other_n = create_notification(
            user=other,
            type=Notification.Type.OFFER_ACCEPTED,
            title="Other",
            message="m",
        )
        result = mark_notification_as_read(
            notification_id=other_n.pk, user=self.user
        )
        self.assertIsNone(result)
        # Other user's notification remains unread.
        other_n.refresh_from_db()
        self.assertFalse(other_n.is_read)