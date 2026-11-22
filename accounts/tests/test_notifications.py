from decimal import Decimal
import time

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import (
    CaptainProfile,
    CaptainWallet,
    CustomerProfile,
    Notification,
    User,
)
from accounts.services.notifications import (
    create_notification,
    get_user_notifications,
    mark_all_notifications_as_read,
    mark_notification_as_read,
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


class NotificationServiceTests(TestCase):
    def setUp(self):
        self.customer, self.customer_user = _make_customer("notif_customer")
        self.captain, self.captain_user = _make_captain("notif_captain")

    def test_create_notification_for_customer(self):
        notification = create_notification(
            user=self.customer_user,
            type=Notification.Type.OFFER_RECEIVED,
            title="عرض جديد",
            message="لديك عرض جديد للرحلة",
            payload={"trip_id": 1, "captain_id": self.captain.pk},
        )
        self.assertEqual(notification.user, self.customer_user)
        self.assertEqual(notification.type, Notification.Type.OFFER_RECEIVED)
        self.assertEqual(notification.title, "عرض جديد")
        self.assertEqual(notification.message, "لديك عرض جديد للرحلة")
        self.assertFalse(notification.is_read)
        self.assertEqual(
            notification.payload,
            {"trip_id": 1, "captain_id": self.captain.pk},
        )
        self.assertIsNotNone(notification.created_at)

    def test_create_notification_defaults(self):
        notification = create_notification(
            user=self.customer_user,
            type=Notification.Type.REVIEW_RECEIVED,
            title="تقييم جديد",
        )
        self.assertEqual(notification.message, "")
        self.assertEqual(notification.payload, {})
        self.assertFalse(notification.is_read)

    def test_get_user_notifications_returns_only_own(self):
        create_notification(
            user=self.customer_user,
            type=Notification.Type.OFFER_RECEIVED,
            title="A",
            message="a",
        )
        create_notification(
            user=self.captain_user,
            type=Notification.Type.OFFER_ACCEPTED,
            title="B",
            message="b",
        )

        customer_notifications = get_user_notifications(user=self.customer_user)
        self.assertEqual(len(customer_notifications), 1)
        self.assertEqual(customer_notifications[0].title, "A")
        self.assertEqual(customer_notifications[0].user, self.customer_user)

    def test_unread_notification_count(self):
        self.assertEqual(
            get_unread_notification_count(user=self.customer_user), 0
        )

        create_notification(
            user=self.customer_user,
            type=Notification.Type.OFFER_RECEIVED,
            title="A",
            message="a",
        )
        create_notification(
            user=self.customer_user,
            type=Notification.Type.OFFER_ACCEPTED,
            title="B",
            message="b",
        )
        create_notification(
            user=self.captain_user,
            type=Notification.Type.REVIEW_RECEIVED,
            title="C",
            message="c",
        )

        self.assertEqual(
            get_unread_notification_count(user=self.customer_user), 2
        )
        self.assertEqual(
            get_unread_notification_count(user=self.captain_user), 1
        )

    def test_mark_notification_as_read_by_owner(self):
        notification = create_notification(
            user=self.customer_user,
            type=Notification.Type.OFFER_RECEIVED,
            title="A",
            message="a",
        )

        result = mark_notification_as_read(
            notification_id=notification.pk,
            user=self.customer_user,
        )
        self.assertIsNotNone(result)
        self.assertTrue(result.is_read)

        notification.refresh_from_db()
        self.assertTrue(notification.is_read)

    def test_mark_notification_as_read_enforces_ownership(self):
        notification = create_notification(
            user=self.customer_user,
            type=Notification.Type.OFFER_RECEIVED,
            title="A",
            message="a",
        )

        result = mark_notification_as_read(
            notification_id=notification.pk,
            user=self.captain_user,
        )
        self.assertIsNone(result)

        notification.refresh_from_db()
        self.assertFalse(notification.is_read)

    def test_mark_nonexistent_notification_as_read(self):
        result = mark_notification_as_read(
            notification_id=99999,
            user=self.customer_user,
        )
        self.assertIsNone(result)

    def test_mark_all_notifications_as_read(self):
        create_notification(
            user=self.customer_user,
            type=Notification.Type.OFFER_RECEIVED,
            title="A",
            message="a",
        )
        create_notification(
            user=self.customer_user,
            type=Notification.Type.OFFER_ACCEPTED,
            title="B",
            message="b",
        )
        create_notification(
            user=self.captain_user,
            type=Notification.Type.REVIEW_RECEIVED,
            title="C",
            message="c",
        )

        count = mark_all_notifications_as_read(user=self.customer_user)
        self.assertEqual(count, 2)

        self.assertEqual(
            get_unread_notification_count(user=self.customer_user), 0
        )
        self.assertEqual(
            get_unread_notification_count(user=self.captain_user), 1
        )

    def test_get_user_notifications_unread_only(self):
        n1 = create_notification(
            user=self.customer_user,
            type=Notification.Type.OFFER_RECEIVED,
            title="A",
            message="a",
        )
        n2 = create_notification(
            user=self.customer_user,
            type=Notification.Type.OFFER_ACCEPTED,
            title="B",
            message="b",
        )

        mark_notification_as_read(notification_id=n1.pk, user=self.customer_user)

        unread = get_user_notifications(
            user=self.customer_user, unread_only=True
        )
        self.assertEqual(len(unread), 1)
        self.assertEqual(unread[0].title, "B")

    def test_get_user_notifications_limit(self):
        for i in range(5):
            create_notification(
                user=self.customer_user,
                type=Notification.Type.OFFER_RECEIVED,
                title=f"N{i}",
                message=f"m{i}",
            )

        limited = get_user_notifications(user=self.customer_user, limit=3)
        self.assertEqual(len(limited), 3)

    def test_get_user_notifications_ordering_newest_first(self):
        n1 = create_notification(
            user=self.customer_user,
            type=Notification.Type.OFFER_RECEIVED,
            title="First",
            message="m",
        )
        time.sleep(0.1)
        n2 = create_notification(
            user=self.customer_user,
            type=Notification.Type.OFFER_ACCEPTED,
            title="Second",
            message="m",
        )

        notifications = get_user_notifications(user=self.customer_user)
        self.assertEqual(len(notifications), 2)
        self.assertEqual(notifications[0].title, "Second")
        self.assertEqual(notifications[1].title, "First")

    def test_all_notification_types_accepted(self):
        for type_value, _label in Notification.Type.choices:
            notification = create_notification(
                user=self.customer_user,
                type=type_value,
                title=f"Test {type_value}",
                message="m",
            )
            self.assertEqual(notification.type, type_value)

    def test_notification_persists_in_database(self):
        create_notification(
            user=self.customer_user,
            type=Notification.Type.OFFER_RECEIVED,
            title="Persistent",
            message="m",
        )

        self.assertEqual(
            Notification.objects.filter(user=self.customer_user).count(), 1
        )
        self.assertTrue(
            Notification.objects.filter(
                user=self.customer_user,
                title="Persistent",
            ).exists()
        )
