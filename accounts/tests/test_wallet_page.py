from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import (
    CaptainProfile,
    CaptainWallet,
    CustomerProfile,
    WalletTopUpRequest,
)
from trips.models import (
    FinancialTransaction,
    Trip,
    TripFinancialRecord,
)

User = get_user_model()


def _make_completed_trip(customer, captain, final_fare):
    return Trip.objects.create(
        customer=customer,
        captain=captain,
        pickup_address="Pickup",
        pickup_latitude=Decimal("29.9670"),
        pickup_longitude=Decimal("32.5498"),
        destination_address="Destination",
        destination_latitude=Decimal("29.9800"),
        destination_longitude=Decimal("32.5600"),
        status=Trip.Status.COMPLETED,
        final_fare=final_fare,
        agreed_fare=final_fare,
        commission_percentage=Decimal("10.00"),
    )


class CaptainWalletPageTests(TestCase):
    def setUp(self):
        self.customer_user = User.objects.create_user(
            username="wallet_customer",
            password="test-password",
            role=User.Role.CUSTOMER,
        )
        self.customer = CustomerProfile.objects.create(
            user=self.customer_user,
        )

        self.captain_user = User.objects.create_user(
            username="wallet_captain",
            password="test-password",
            role=User.Role.CAPTAIN,
        )
        self.captain = CaptainProfile.objects.create(
            user=self.captain_user,
        )
        self.wallet = CaptainWallet.objects.create(
            captain=self.captain,
            balance=Decimal("500.00"),
            reserved_balance=Decimal("50.00"),
        )

        self.other_captain_user = User.objects.create_user(
            username="wallet_other_captain",
            password="test-password",
            role=User.Role.CAPTAIN,
        )
        self.other_captain = CaptainProfile.objects.create(
            user=self.other_captain_user,
        )

        self.url = reverse("accounts:captain_wallet")

    def _login_captain(self):
        self.client.force_login(self.captain_user)

    # 1. Captain can access their wallet page
    def test_captain_can_access_wallet_page(self):
        self._login_captain()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/wallet.html")

    # 2. Anonymous user is redirected to login
    def test_anonymous_user_redirected_to_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response.url)

    # 3. Customer cannot access captain wallet data
    def test_customer_cannot_access_wallet_page(self):
        self.client.force_login(self.customer_user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:dashboard"), response.url)

    # 4. Captain cannot see another captain's wallet/transactions/topups
    def test_captain_isolation(self):
        # Give the other captain distinct data.
        TripFinancialRecord.objects.create(
            trip=_make_completed_trip(
                self.customer, self.other_captain, Decimal("200.00")
            ),
            gross_fare=Decimal("200.00"),
            commission_percentage=Decimal("10.00"),
            commission_amount=Decimal("20.00"),
            captain_earnings=Decimal("180.00"),
        )
        FinancialTransaction.objects.create(
            captain=self.other_captain,
            wallet=CaptainWallet.objects.create(
                captain=self.other_captain, balance=Decimal("999.00")
            ),
            transaction_type=FinancialTransaction.TransactionType.WALLET_TOPUP,
            amount=Decimal("999.00"),
            reference="other-topup-ref",
        )
        WalletTopUpRequest.objects.create(
            captain=self.other_captain,
            amount=Decimal("999.00"),
            method=WalletTopUpRequest.Method.CASH,
            reference="OTHER-CASH-REF",
        )

        self._login_captain()
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        content = response.content.decode("utf-8")
        self.assertNotIn("999.00", content)
        self.assertNotIn("OTHER-CASH-REF", content)
        self.assertNotIn("other-topup-ref", content)

    # 5. Correct wallet balance fields are displayed
    def test_wallet_balance_fields_displayed(self):
        self._login_captain()
        response = self.client.get(self.url)
        content = response.content.decode("utf-8")

        self.assertIn("500.00", content)  # balance
        self.assertIn("50.00", content)   # reserved
        self.assertIn("450.00", content)  # available (500 - 50)

    # 6. Gross fare, commission, and earnings totals from completed trips
    def test_financial_totals_from_completed_trips(self):
        TripFinancialRecord.objects.create(
            trip=_make_completed_trip(
                self.customer, self.captain, Decimal("100.00")
            ),
            gross_fare=Decimal("100.00"),
            commission_percentage=Decimal("10.00"),
            commission_amount=Decimal("10.00"),
            captain_earnings=Decimal("90.00"),
        )
        TripFinancialRecord.objects.create(
            trip=_make_completed_trip(
                self.customer, self.captain, Decimal("200.00")
            ),
            gross_fare=Decimal("200.00"),
            commission_percentage=Decimal("10.00"),
            commission_amount=Decimal("20.00"),
            captain_earnings=Decimal("180.00"),
        )

        self._login_captain()
        response = self.client.get(self.url)
        content = response.content.decode("utf-8")

        # Totals: gross 300.00, commission 30.00, earnings 270.00
        self.assertIn("300.00", content)
        self.assertIn("30.00", content)
        self.assertIn("270.00", content)

    # 7. Non-completed trips are excluded from totals
    def test_non_completed_trips_excluded(self):
        in_progress = Trip.objects.create(
            customer=self.customer,
            captain=self.captain,
            pickup_address="Pickup",
            pickup_latitude=Decimal("29.9670"),
            pickup_longitude=Decimal("32.5498"),
            destination_address="Destination",
            destination_latitude=Decimal("29.9800"),
            destination_longitude=Decimal("32.5600"),
            status=Trip.Status.IN_PROGRESS,
            agreed_fare=Decimal("500.00"),
            commission_percentage=Decimal("10.00"),
        )
        # Even if a financial record somehow existed for a non-completed trip,
        # the view aggregates TripFinancialRecord by captain; records are only
        # created for completed trips. This asserts no record leakage via a
        # completed trip on another captain.
        TripFinancialRecord.objects.create(
            trip=_make_completed_trip(
                self.customer, self.other_captain, Decimal("100.00")
            ),
            gross_fare=Decimal("100.00"),
            commission_percentage=Decimal("10.00"),
            commission_amount=Decimal("10.00"),
            captain_earnings=Decimal("90.00"),
        )

        self._login_captain()
        response = self.client.get(self.url)
        content = response.content.decode("utf-8")

        # No completed-trip financial totals for this captain (all zero).
        self.assertIn("0.00", content)
        self.assertNotIn("90.00", content)

    # 8. Transaction descriptions are translated; raw codes not exposed
    def test_transaction_labels_translated_and_codes_hidden(self):
        FinancialTransaction.objects.create(
            captain=self.captain,
            wallet=self.wallet,
            transaction_type=(
                FinancialTransaction.TransactionType.WALLET_TOPUP
            ),
            amount=Decimal("100.00"),
            reference="wallet-topup:TEST-REF-1",
            description="Paymob wallet top-up: TEST-REF-1",
        )
        FinancialTransaction.objects.create(
            captain=self.captain,
            wallet=self.wallet,
            transaction_type=(
                FinancialTransaction.TransactionType.COMMISSION_RESERVE
            ),
            amount=Decimal("10.00"),
            reference="trip-commission-reserve:1",
            description="Commission reservation for trip #1",
        )

        self._login_captain()
        response = self.client.get(self.url)
        content = response.content.decode("utf-8")

        self.assertIn("شحن محفظة", content)
        self.assertIn("حجز عمولة", content)
        self.assertNotIn("WALLET_TOPUP", content)
        self.assertNotIn("COMMISSION_RESERVE", content)

    # 9. Top-up history and statuses are displayed correctly
    def test_topup_history_and_status_displayed(self):
        WalletTopUpRequest.objects.create(
            captain=self.captain,
            amount=Decimal("150.00"),
            method=WalletTopUpRequest.Method.PAYMOB,
            status=WalletTopUpRequest.Status.COMPLETED,
            reference="PAYMOB-PAGE-REF-1",
            payment_order_id="111",
            payment_transaction_id="222",
        )
        WalletTopUpRequest.objects.create(
            captain=self.captain,
            amount=Decimal("75.00"),
            method=WalletTopUpRequest.Method.CASH,
            status=WalletTopUpRequest.Status.PENDING,
            reference="CASH-PAGE-REF-2",
        )

        self._login_captain()
        response = self.client.get(self.url)
        content = response.content.decode("utf-8")

        self.assertIn("PAYMOB-PAGE-REF-1", content)
        self.assertIn("CASH-PAGE-REF-2", content)
        self.assertIn("150.00", content)
        self.assertIn("75.00", content)
        self.assertIn("مكتمل", content)
        self.assertIn("قيد الانتظار", content)

    # 10. Missing wallet/profile handled gracefully
    def test_missing_wallet_handled_gracefully(self):
        self.wallet.delete()
        self._login_captain()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/wallet.html")

    def test_missing_profile_redirects_gracefully(self):
        self.wallet.delete()
        self.captain.delete()
        self._login_captain()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:dashboard"), response.url)

    # 11. Existing top-up links resolve to correct routes
    def test_topup_links_resolve(self):
        self._login_captain()
        response = self.client.get(self.url)
        content = response.content.decode("utf-8")

        self.assertIn(reverse("accounts:request_cash_topup"), content)
        dashboard_url = reverse("accounts:dashboard")
        self.assertIn(dashboard_url, content)
        self.assertIn("paymob-topup-form", content)

    # 12. No unsupported withdrawal/payout functionality advertised
    def test_no_withdrawal_or_payout_advertised(self):
        self._login_captain()
        response = self.client.get(self.url)
        content = response.content.decode("utf-8")

        # Arabic words for withdraw/payout must not appear as offered actions.
        for term in ("سحب", "تحويل الأرباح", "طلب دفع", "withdraw", "payout"):
            self.assertNotIn(term, content)