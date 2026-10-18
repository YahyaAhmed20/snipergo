from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase

from accounts.models import CaptainProfile, WalletTopUpRequest


class WalletTopUpConstraintTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user = get_user_model().objects.create_user(
            username="constraint_test_captain",
            password="local-test-password",
            role="CAPTAIN",
        )
        cls.captain = CaptainProfile.objects.create(
            user=user,
            phone_number="+201000000001",
        )

    def create_topup(self, reference, **kwargs):
        defaults = {
            "captain": self.captain,
            "amount": Decimal("100.00"),
            "method": WalletTopUpRequest.Method.PAYMOB,
            "reference": reference,
        }
        defaults.update(kwargs)
        return WalletTopUpRequest.objects.create(**defaults)

    def test_duplicate_nonempty_payment_order_id_is_rejected(self):
        self.create_topup("ORDER-UNIQUE-1", payment_order_id="order-123")

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.create_topup("ORDER-UNIQUE-2", payment_order_id="order-123")

    def test_duplicate_nonempty_payment_transaction_id_is_rejected(self):
        self.create_topup(
            "TRANSACTION-UNIQUE-1",
            payment_transaction_id="transaction-123",
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.create_topup(
                    "TRANSACTION-UNIQUE-2",
                    payment_transaction_id="transaction-123",
                )

    def test_multiple_empty_payment_order_ids_are_allowed(self):
        self.create_topup("EMPTY-ORDER-1", payment_order_id="")
        self.create_topup("EMPTY-ORDER-2", payment_order_id="")

        self.assertEqual(
            WalletTopUpRequest.objects.filter(payment_order_id="").count(),
            2,
        )

    def test_multiple_empty_payment_transaction_ids_are_allowed(self):
        self.create_topup("EMPTY-TRANSACTION-1", payment_transaction_id="")
        self.create_topup("EMPTY-TRANSACTION-2", payment_transaction_id="")

        self.assertEqual(
            WalletTopUpRequest.objects.filter(payment_transaction_id="").count(),
            2,
        )
