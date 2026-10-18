from decimal import Decimal
from django.urls import reverse

from django.test import TestCase, Client
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.contrib.auth import get_user_model

from accounts.models import (
    CaptainProfile,
    CaptainWallet,
    WalletTopUpRequest,
)
from accounts.services.wallet_topups import (
    approve_cash_topup,
    reject_cash_topup,
)
from trips.models import FinancialTransaction


User = get_user_model()


class CashWalletTopUpTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="topup_admin",
            password="test-password",
            is_staff=True,
            is_superuser=True,
        )
        self.captain_user = User.objects.create_user(
            username="topup_captain",
            password="test-password",
            role=User.Role.CAPTAIN,
        )
        self.captain = CaptainProfile.objects.create(
            user=self.captain_user,
        )
        self.wallet = CaptainWallet.objects.create(
            captain=self.captain,
            balance=Decimal("100.00"),
        )
        self.topup = WalletTopUpRequest.objects.create(
            captain=self.captain,
            amount=Decimal("50.00"),
            method=WalletTopUpRequest.Method.CASH,
            reference="cash-test-001",
        )

    def test_approved_cash_deposit_credits_wallet_once(self):
        wallet = approve_cash_topup(
            request_id=self.topup.pk,
            admin_user=self.admin,
        )

        wallet.refresh_from_db()
        self.topup.refresh_from_db()

        self.assertEqual(wallet.balance, Decimal("150.00"))
        self.assertEqual(
            self.topup.status,
            WalletTopUpRequest.Status.COMPLETED,
        )
        self.assertEqual(self.topup.reviewed_by, self.admin)
        self.assertTrue(
            FinancialTransaction.objects.filter(
                reference="wallet-topup:cash-test-001",
                transaction_type=(
                    FinancialTransaction.TransactionType.WALLET_TOPUP
                ),
                amount=Decimal("50.00"),
            ).exists()
        )

    def test_repeated_approval_is_rejected_without_extra_credit(self):
        approve_cash_topup(
            request_id=self.topup.pk,
            admin_user=self.admin,
        )

        with self.assertRaises(ValidationError):
            approve_cash_topup(
                request_id=self.topup.pk,
                admin_user=self.admin,
            )

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("150.00"))

    def test_paymob_request_cannot_use_cash_approval(self):
        self.topup.method = WalletTopUpRequest.Method.PAYMOB
        self.topup.save(update_fields=["method"])

        with self.assertRaises(ValidationError):
            approve_cash_topup(
                request_id=self.topup.pk,
                admin_user=self.admin,
            )

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("100.00"))

    def test_non_staff_user_cannot_approve_cash_deposit(self):
        customer = User.objects.create_user(
            username="topup_customer",
            password="test-password",
            is_staff=False,
        )

        with self.assertRaises(PermissionDenied):
            approve_cash_topup(
                request_id=self.topup.pk,
                admin_user=customer,
            )

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("100.00"))

    # ===== Rejection Tests =====

    def test_rejection_records_reason_without_crediting_wallet(self):
        rejected = reject_cash_topup(
            request_id=self.topup.pk,
            admin_user=self.admin,
            reason="Deposit receipt could not be verified.",
        )

        self.wallet.refresh_from_db()
        self.topup.refresh_from_db()

        self.assertEqual(self.wallet.balance, Decimal("100.00"))
        self.assertEqual(
            self.topup.status,
            WalletTopUpRequest.Status.REJECTED,
        )
        self.assertEqual(self.topup.reviewed_by, self.admin)
        self.assertIsNotNone(self.topup.reviewed_at)
        self.assertEqual(
            self.topup.rejection_reason,
            "Deposit receipt could not be verified.",
        )
        self.assertEqual(rejected.pk, self.topup.pk)
        self.assertFalse(
            FinancialTransaction.objects.filter(
                reference="wallet-topup:cash-test-001",
            ).exists()
        )

    def test_repeated_rejection_is_rejected(self):
        reject_cash_topup(
            request_id=self.topup.pk,
            admin_user=self.admin,
            reason="Receipt is invalid.",
        )

        with self.assertRaises(ValidationError):
            reject_cash_topup(
                request_id=self.topup.pk,
                admin_user=self.admin,
                reason="Receipt is invalid.",
            )

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("100.00"))

    def test_paymob_request_cannot_use_cash_rejection(self):
        self.topup.method = WalletTopUpRequest.Method.PAYMOB
        self.topup.save(update_fields=["method"])

        with self.assertRaises(ValidationError):
            reject_cash_topup(
                request_id=self.topup.pk,
                admin_user=self.admin,
                reason="Payment is unverified.",
            )

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("100.00"))

    def test_non_staff_user_cannot_reject_cash_deposit(self):
        customer = User.objects.create_user(
            username="topup_reject_customer",
            password="test-password",
            is_staff=False,
        )

        with self.assertRaises(PermissionDenied):
            reject_cash_topup(
                request_id=self.topup.pk,
                admin_user=customer,
                reason="Receipt is invalid.",
            )

        self.topup.refresh_from_db()
        self.assertEqual(
            self.topup.status,
            WalletTopUpRequest.Status.PENDING,
        )

    def test_rejection_requires_a_valid_reason(self):
        with self.assertRaises(ValidationError):
            reject_cash_topup(
                request_id=self.topup.pk,
                admin_user=self.admin,
                reason="   ",
            )

        self.topup.refresh_from_db()
        self.assertEqual(
            self.topup.status,
            WalletTopUpRequest.Status.PENDING,
        )



class CashTopUpRequestViewTests(TestCase):
    def setUp(self):
        self.captain_user = User.objects.create_user(
            username="topup_view_captain",
            password="test-password",
            role=User.Role.CAPTAIN,
        )
        self.captain = CaptainProfile.objects.create(
            user=self.captain_user,
        )
        self.wallet = CaptainWallet.objects.create(
            captain=self.captain,
            balance=Decimal("100.00"),
        )
        self.url = reverse("accounts:request_cash_topup")
        self.client = Client()

    def test_captain_can_submit_cash_topup_request(self):
        self.client.force_login(self.captain_user)

        response = self.client.post(
            self.url,
            {"amount": "50.00"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)

        topup = WalletTopUpRequest.objects.get(
            captain=self.captain,
        )
        self.assertEqual(
            topup.status,
            WalletTopUpRequest.Status.PENDING,
        )
        self.assertEqual(
            topup.method,
            WalletTopUpRequest.Method.CASH,
        )
        self.assertEqual(topup.amount, Decimal("50.00"))

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("100.00"))

    def test_non_captain_cannot_submit_cash_topup_request(self):
        customer = User.objects.create_user(
            username="topup_view_customer",
            password="test-password",
            role=User.Role.CUSTOMER,
        )
        self.client.force_login(customer)

        response = self.client.post(
            self.url,
            {"amount": "50.00"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(WalletTopUpRequest.objects.exists())

    def test_invalid_amount_does_not_create_request(self):
        self.client.force_login(self.captain_user)

        response = self.client.post(
            self.url,
            {"amount": "0"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(WalletTopUpRequest.objects.exists())

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("100.00"))





from django.urls import reverse


class WalletTopUpAdminTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            username="topup_admin_view",
            email="topup-admin@example.com",
            password="Strong-Test-Password-123!",
        )
        self.captain_user = User.objects.create_user(
            username="topup_admin_captain",
            password="test-password",
            role=User.Role.CAPTAIN,
        )
        self.captain = CaptainProfile.objects.create(
            user=self.captain_user,
        )
        self.wallet = CaptainWallet.objects.create(
            captain=self.captain,
            balance=Decimal("100.00"),
        )
        self.topup = WalletTopUpRequest.objects.create(
            captain=self.captain,
            amount=Decimal("50.00"),
            method=WalletTopUpRequest.Method.CASH,
            status=WalletTopUpRequest.Status.PENDING,
            reference="CASH-ADMIN-TEST-001",
        )
        self.client.force_login(self.admin_user)
        self.changelist_url = reverse(
            "admin:accounts_wallettopuprequest_changelist"
        )

    def test_admin_can_open_topup_list(self):
        response = self.client.get(self.changelist_url)
        self.assertEqual(response.status_code, 200)

    def test_admin_can_open_cash_rejection_page(self):
        url = reverse(
            "admin:accounts_wallettopuprequest_reject",
            args=[self.topup.pk],
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_admin_can_reject_cash_deposit(self):
        url = reverse(
            "admin:accounts_wallettopuprequest_reject",
            args=[self.topup.pk],
        )
        response = self.client.post(
            url,
            {"reason": "Deposit was not verified."},
        )

        self.assertEqual(response.status_code, 302)
        self.topup.refresh_from_db()
        self.assertEqual(
            self.topup.status,
            WalletTopUpRequest.Status.REJECTED,
        )

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("100.00"))

    def test_admin_cannot_reject_processed_request(self):
        self.topup.status = WalletTopUpRequest.Status.COMPLETED
        self.topup.save(update_fields=["status"])

        url = reverse(
            "admin:accounts_wallettopuprequest_reject",
            args=[self.topup.pk],
        )
        response = self.client.get(url)

        self.assertEqual(response.status_code, 302)




    def test_admin_can_approve_cash_deposit(self):
        response = self.client.post(
            self.changelist_url,
            {
                "action": "approve_cash_deposits",
                "_selected_action": [str(self.topup.pk)],
            },
        )

        self.assertEqual(response.status_code, 302)

        self.topup.refresh_from_db()
        self.assertEqual(
            self.topup.status,
            WalletTopUpRequest.Status.COMPLETED,
        )
        self.assertEqual(self.topup.reviewed_by, self.admin_user)

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("150.00"))

    def test_repeated_approval_does_not_credit_wallet_twice(self):
        for _ in range(2):
            self.client.post(
                self.changelist_url,
                {
                    "action": "approve_cash_deposits",
                    "_selected_action": [str(self.topup.pk)],
                },
            )

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("150.00"))





    def test_non_staff_cannot_open_admin_topup_list(self):
        customer = User.objects.create_user(
            username="topup_regular_user",
            password="test-password",
            role=User.Role.CUSTOMER,
        )
        self.client.force_login(customer)

        response = self.client.get(self.changelist_url)

        self.assertEqual(response.status_code, 302)

    def test_non_staff_cannot_open_cash_rejection_page(self):
        customer = User.objects.create_user(
            username="topup_regular_user_2",
            password="test-password",
            role=User.Role.CUSTOMER,
        )
        self.client.force_login(customer)

        url = reverse(
            "admin:accounts_wallettopuprequest_reject",
            args=[self.topup.pk],
        )
        response = self.client.get(url)

        self.assertEqual(response.status_code, 302)

        self.topup.refresh_from_db()
        self.assertEqual(
            self.topup.status,
            WalletTopUpRequest.Status.PENDING,
        )
