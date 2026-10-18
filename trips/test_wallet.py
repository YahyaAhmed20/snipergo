from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from accounts.models import User, CaptainProfile, CaptainWallet
from trips.models import (
    CommissionConfig,
    FinancialTransaction,
    Trip,
    TripFinancialRecord,
)
from trips.services.finance import create_trip_financial_record
from trips.services.wallet import (
    credit_wallet,
    deduct_trip_commission,
    release_trip_commission_reservation,
    reserve_trip_commission,
)
from accounts.models import CustomerProfile


class WalletServiceTests(TestCase):
    def setUp(self):
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
            balance=Decimal("100.00"),
        )

        customer_user = User.objects.create_user(
            username="wallet_customer",
            password="test-password",
        )
        customer = CustomerProfile.objects.create(user=customer_user)

        self.trip = Trip.objects.create(
            customer=customer,
            captain=self.captain,
            pickup_address="Pickup",
            pickup_latitude=Decimal("29.9670"),
            pickup_longitude="32.5498",
            destination_address="Destination",
            destination_latitude=Decimal("29.9800"),
            destination_longitude="32.5600",
            status=Trip.Status.COMPLETED,
            final_fare=Decimal("100.00"),
            commission_percentage=Decimal("10.00"),
        )

        CommissionConfig.objects.create(
            name="default",
            percentage=Decimal("10.00"),
            is_active=True,
        )
        self.record = create_trip_financial_record(
            trip_id=self.trip.pk,
        )

    def test_credit_wallet_adds_balance_and_transaction(self):
        wallet = credit_wallet(
            captain=self.captain,
            amount="50.00",
            reference="test-topup-001",
        )
        wallet.refresh_from_db()

        self.assertEqual(wallet.balance, Decimal("150.00"))
        self.assertTrue(
            FinancialTransaction.objects.filter(
                reference="test-topup-001",
                transaction_type=(
                    FinancialTransaction.TransactionType.WALLET_TOPUP
                ),
            ).exists()
        )

    def test_repeated_topup_reference_does_not_add_balance_twice(self):
        credit_wallet(
            captain=self.captain,
            amount="50.00",
            reference="test-topup-002",
        )
        credit_wallet(
            captain=self.captain,
            amount="50.00",
            reference="test-topup-002",
        )

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("150.00"))

    def test_invalid_topup_amount_is_rejected(self):
        with self.assertRaises(ValidationError):
            credit_wallet(
                captain=self.captain,
                amount="-10.00",
                reference="test-topup-invalid",
            )

    def test_credit_wallet_rejects_amount_that_rounds_to_zero(self):
        with self.assertRaises(ValidationError):
            credit_wallet(
                captain=self.captain,
                amount=Decimal("0.001"),
                reference="tiny-topup-test",
            )


    def test_credit_wallet_rejects_amount_above_transaction_limit(self):
        with self.assertRaises(ValidationError):
            credit_wallet(
                captain=self.captain,
                amount="100000000.00",
                reference="topup-over-limit",
            )

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("100.00"))
        self.assertFalse(
            FinancialTransaction.objects.filter(
                reference="topup-over-limit"
            ).exists()
        )

    def test_credit_wallet_rejects_extremely_large_amount(self):
        with self.assertRaises(ValidationError):
            credit_wallet(
                captain=self.captain,
                amount="1E+100000",
                reference="topup-extreme",
            )

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("100.00"))
        self.assertFalse(
            FinancialTransaction.objects.filter(
                reference="topup-extreme"
            ).exists()
        )


    def test_deducts_commission_once(self):
        wallet = deduct_trip_commission(
            trip_financial_record_id=self.record.pk,
        )
        self.assertEqual(wallet.balance, Decimal("90.00"))

        wallet = deduct_trip_commission(
            trip_financial_record_id=self.record.pk,
        )
        wallet.refresh_from_db()

        self.assertEqual(wallet.balance, Decimal("90.00"))
        self.assertEqual(
            FinancialTransaction.objects.filter(
                reference=f"trip-commission:{self.record.pk}",
            ).count(),
            1,
        )

    def test_insufficient_balance_does_not_create_deduction(self):
        self.wallet.balance = Decimal("9.00")
        self.wallet.save(update_fields=["balance"])

        self.record.refresh_from_db()

        with self.assertRaises(ValidationError):
            deduct_trip_commission(
                trip_financial_record_id=self.record.pk,
            )

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("9.00"))
        self.assertFalse(
            FinancialTransaction.objects.filter(
                reference=f"trip-commission:{self.record.pk}",
            ).exists()
        )

    def test_available_balance_excludes_reserved_balance(self):
        self.wallet.balance = Decimal("100.00")
        self.wallet.reserved_balance = Decimal("30.00")
        self.wallet.save(
            update_fields=["balance", "reserved_balance"]
        )

        self.wallet.refresh_from_db()

        self.assertEqual(
            self.wallet.available_balance,
            Decimal("70.00"),
        )

    def test_reserve_trip_commission_uses_available_balance(self):
        self.trip.status = Trip.Status.ACCEPTED
        self.trip.estimated_fare = Decimal("100.00")
        self.trip.save(update_fields=["status", "estimated_fare"])

        self.wallet.balance = Decimal("100.00")
        self.wallet.reserved_balance = Decimal("0.00")
        self.wallet.save(
            update_fields=["balance", "reserved_balance"]
        )

        wallet = reserve_trip_commission(trip_id=self.trip.pk)
        wallet.refresh_from_db()

        self.assertEqual(wallet.balance, Decimal("100.00"))
        self.assertEqual(wallet.reserved_balance, Decimal("10.00"))
        self.assertEqual(wallet.available_balance, Decimal("90.00"))



    def test_zero_percent_commission_reserves_zero_without_creating_invalid_transaction(self):
        CommissionConfig.objects.filter(is_active=True).update(
            percentage=Decimal("0.00")
        )

        self.trip.status = Trip.Status.ACCEPTED
        self.trip.estimated_fare = Decimal("100.00")
        self.trip.commission_percentage = Decimal("0.00")
        self.trip.save(
            update_fields=[
                "status",
                "estimated_fare",
                "commission_percentage",
            ]
        )

        wallet = reserve_trip_commission(trip_id=self.trip.pk)
        wallet.refresh_from_db()

        self.assertEqual(wallet.balance, Decimal("100.00"))
        self.assertEqual(wallet.reserved_balance, Decimal("0.00"))
        self.assertFalse(
            FinancialTransaction.objects.filter(
                reference=f"trip-commission-reserve:{self.trip.pk}"
            ).exists()
        )


    def test_reserve_trip_commission_does_not_reserve_twice(self):
        self.trip.status = Trip.Status.ACCEPTED
        self.trip.estimated_fare = Decimal("100.00")
        self.trip.save(update_fields=["status", "estimated_fare"])

        reserve_trip_commission(trip_id=self.trip.pk)
        reserve_trip_commission(trip_id=self.trip.pk)

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.reserved_balance, Decimal("10.00"))

    def test_reserve_trip_commission_rejects_insufficient_balance(self):
        self.trip.status = Trip.Status.ACCEPTED
        self.trip.estimated_fare = Decimal("100.00")
        self.trip.save(update_fields=["status", "estimated_fare"])

        self.wallet.balance = Decimal("9.00")
        self.wallet.reserved_balance = Decimal("0.00")
        self.wallet.save(
            update_fields=["balance", "reserved_balance"]
        )

        with self.assertRaises(ValidationError):
            reserve_trip_commission(trip_id=self.trip.pk)

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("9.00"))
        self.assertEqual(self.wallet.reserved_balance, Decimal("0.00"))

    def test_cancelled_trip_releases_commission_reservation(self):
        self.trip.status = Trip.Status.ACCEPTED
        self.trip.estimated_fare = Decimal("100.00")
        self.trip.save(update_fields=["status", "estimated_fare"])

        reserve_trip_commission(trip_id=self.trip.pk)

        self.trip.status = Trip.Status.CANCELLED
        self.trip.save(update_fields=["status"])

        release_trip_commission_reservation(trip_id=self.trip.pk)
        release_trip_commission_reservation(trip_id=self.trip.pk)

        self.wallet.refresh_from_db()

        self.assertEqual(self.wallet.balance, Decimal("100.00"))
        self.assertEqual(self.wallet.reserved_balance, Decimal("0.00"))
        self.assertEqual(self.wallet.available_balance, Decimal("100.00"))

        self.assertEqual(
            FinancialTransaction.objects.filter(
                reference=f"trip-commission-release:{self.trip.pk}",
            ).count(),
            1,
        )

    def test_reusing_topup_reference_does_not_credit_twice(self):
        reference = "duplicate-reference-test"

        credit_wallet(
            captain=self.captain,
            amount=Decimal("25.00"),
            reference=reference,
        )

        wallet = CaptainWallet.objects.get(captain=self.captain)
        balance_after_first_credit = wallet.balance

        credit_wallet(
            captain=self.captain,
            amount=Decimal("25.00"),
            reference=reference,
        )

        wallet.refresh_from_db()

        self.assertEqual(wallet.balance, balance_after_first_credit)
        self.assertEqual(
            FinancialTransaction.objects.filter(
                reference=reference
            ).count(),
            1,
        )

    def test_reusing_topup_reference_with_different_amount_is_rejected(self):
        reference = "topup-different-amount"

        credit_wallet(
            captain=self.captain,
            amount=Decimal("25.00"),
            reference=reference,
        )

        with self.assertRaises(ValidationError):
            credit_wallet(
                captain=self.captain,
                amount=Decimal("40.00"),
                reference=reference,
            )

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("125.00"))

        self.assertEqual(
            FinancialTransaction.objects.filter(
                reference=reference
            ).count(),
            1,
        )

    def test_cancelled_trip_cannot_reserve_commission_again(self):
        self.trip.status = Trip.Status.ACCEPTED
        self.trip.estimated_fare = Decimal("100.00")
        self.trip.save(update_fields=["status", "estimated_fare"])

        reserve_trip_commission(trip_id=self.trip.pk)

        self.trip.status = Trip.Status.CANCELLED
        self.trip.save(update_fields=["status"])

        release_trip_commission_reservation(trip_id=self.trip.pk)

        with self.assertRaises(ValidationError):
            reserve_trip_commission(trip_id=self.trip.pk)

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.reserved_balance, Decimal("0.00"))

    def test_commission_cannot_be_deducted_after_reservation_release(self):
        self.trip.status = Trip.Status.ACCEPTED
        self.trip.estimated_fare = Decimal("100.00")
        self.trip.save(update_fields=["status", "estimated_fare"])

        reserve_trip_commission(trip_id=self.trip.pk)

        self.trip.status = Trip.Status.CANCELLED
        self.trip.save(update_fields=["status"])

        release_trip_commission_reservation(trip_id=self.trip.pk)

        self.trip.status = Trip.Status.COMPLETED
        self.trip.final_fare = Decimal("100.00")
        self.trip.save(update_fields=["status", "final_fare"])

        with self.assertRaises(ValidationError):
            deduct_trip_commission(
                trip_financial_record_id=self.record.pk
            )

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("100.00"))
        self.assertEqual(self.wallet.reserved_balance, Decimal("0.00"))

    def test_repeated_release_does_not_change_wallet_twice(self):
        self.trip.status = Trip.Status.ACCEPTED
        self.trip.estimated_fare = Decimal("100.00")
        self.trip.save(update_fields=["status", "estimated_fare"])

        reserve_trip_commission(trip_id=self.trip.pk)

        self.trip.status = Trip.Status.CANCELLED
        self.trip.save(update_fields=["status"])

        release_trip_commission_reservation(trip_id=self.trip.pk)
        release_trip_commission_reservation(trip_id=self.trip.pk)

        self.wallet.refresh_from_db()

        self.assertEqual(self.wallet.balance, Decimal("100.00"))
        self.assertEqual(self.wallet.reserved_balance, Decimal("0.00"))

        release = FinancialTransaction.objects.get(
            reference=f"trip-commission-release:{self.trip.pk}"
        )
        self.assertEqual(release.amount, Decimal("10.00"))

    def test_release_rejects_amount_different_from_reservation(self):
        self.trip.status = Trip.Status.ACCEPTED
        self.trip.estimated_fare = Decimal("100.00")
        self.trip.save(update_fields=["status", "estimated_fare"])

        reserve_trip_commission(trip_id=self.trip.pk)

        self.trip.status = Trip.Status.CANCELLED
        self.trip.save(update_fields=["status"])

        release_trip_commission_reservation(trip_id=self.trip.pk)

        release = FinancialTransaction.objects.get(
            reference=f"trip-commission-release:{self.trip.pk}"
        )
        release.amount = Decimal("15.00")
        release.save(update_fields=["amount"])

        with self.assertRaises(ValidationError):
            release_trip_commission_reservation(trip_id=self.trip.pk)

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.reserved_balance, Decimal("0.00"))

    def test_existing_commission_deduction_with_release_is_rejected(self):
        self.trip.status = Trip.Status.ACCEPTED
        self.trip.estimated_fare = Decimal("100.00")
        self.trip.save(update_fields=["status", "estimated_fare"])

        reserve_trip_commission(trip_id=self.trip.pk)

        self.trip.status = Trip.Status.CANCELLED
        self.trip.save(update_fields=["status"])
        release_trip_commission_reservation(trip_id=self.trip.pk)

        self.trip.status = Trip.Status.COMPLETED
        self.trip.save(update_fields=["status"])

        FinancialTransaction.objects.create(
            captain=self.captain,
            wallet=self.wallet,
            financial_record=self.record,
            transaction_type=FinancialTransaction.TransactionType.COMMISSION_DEDUCTION,
            amount=self.record.commission_amount,
            reference=f"trip-commission:{self.record.pk}",
            description="Test deduction",
        )

        with self.assertRaises(ValidationError):
            deduct_trip_commission(
                trip_financial_record_id=self.record.pk
            )

    def test_existing_commission_deduction_for_another_captain_is_rejected(self):
        other_user = User.objects.create_user(
            username="other_wallet_captain",
            password="test-password",
            role=User.Role.CAPTAIN,
        )
        other_captain = CaptainProfile.objects.create(user=other_user)

        FinancialTransaction.objects.create(
            captain=other_captain,
            wallet=self.wallet,
            financial_record=self.record,
            transaction_type=FinancialTransaction.TransactionType.COMMISSION_DEDUCTION,
            amount=self.record.commission_amount,
            reference=f"trip-commission:{self.record.pk}",
            description="Inconsistent test deduction",
        )

        with self.assertRaises(ValidationError):
            deduct_trip_commission(
                trip_financial_record_id=self.record.pk
            )

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("100.00"))

    def test_existing_commission_deduction_for_wrong_financial_record_is_rejected(self):
        other_trip = Trip.objects.create(
            customer=self.trip.customer,
            captain=self.captain,
            pickup_address="Other Pickup",
            pickup_latitude=Decimal("29.9670"),
            pickup_longitude=Decimal("32.5498"),
            destination_address="Other Destination",
            destination_latitude=Decimal("29.9800"),
            destination_longitude=Decimal("32.5600"),
            status=Trip.Status.COMPLETED,
            final_fare=Decimal("100.00"),
            commission_percentage=Decimal("10.00"),
        )
        other_record = create_trip_financial_record(
            trip_id=other_trip.pk
        )

        FinancialTransaction.objects.create(
            captain=self.captain,
            wallet=self.wallet,
            financial_record=other_record,
            transaction_type=(
                FinancialTransaction.TransactionType.COMMISSION_DEDUCTION
            ),
            amount=self.record.commission_amount,
            reference=f"trip-commission:{self.record.pk}",
            description="Inconsistent financial record test",
        )

        with self.assertRaises(ValidationError):
            deduct_trip_commission(
                trip_financial_record_id=self.record.pk
            )

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("100.00"))

    def test_cancelled_trip_cannot_reuse_existing_reservation(self):
        self.trip.status = Trip.Status.ACCEPTED
        self.trip.estimated_fare = Decimal("100.00")
        self.trip.save(update_fields=["status", "estimated_fare"])

        reserve_trip_commission(trip_id=self.trip.pk)

        self.trip.status = Trip.Status.CANCELLED
        self.trip.save(update_fields=["status"])
        release_trip_commission_reservation(trip_id=self.trip.pk)

        self.trip.status = Trip.Status.ACCEPTED
        self.trip.save(update_fields=["status"])

        with self.assertRaises(ValidationError):
            reserve_trip_commission(trip_id=self.trip.pk)

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.reserved_balance, Decimal("0.00"))

    def test_existing_reservation_for_another_captain_is_rejected(self):
        self.trip.status = Trip.Status.ACCEPTED
        self.trip.estimated_fare = Decimal("100.00")
        self.trip.save(update_fields=["status", "estimated_fare"])

        other_user = User.objects.create_user(
            username="other_reservation_captain",
            password="test-password",
            role=User.Role.CAPTAIN,
        )
        other_captain = CaptainProfile.objects.create(user=other_user)

        FinancialTransaction.objects.create(
            captain=other_captain,
            wallet=self.wallet,
            transaction_type=FinancialTransaction.TransactionType.COMMISSION_RESERVE,
            amount=Decimal("10.00"),
            reference=f"trip-commission-reserve:{self.trip.pk}",
            description="Inconsistent test reservation",
        )

        with self.assertRaises(ValidationError):
            reserve_trip_commission(trip_id=self.trip.pk)

    def test_release_is_rejected_after_commission_deduction(self):
        self.trip.status = Trip.Status.ACCEPTED
        self.trip.estimated_fare = Decimal("100.00")
        self.trip.save(update_fields=["status", "estimated_fare"])

        reserve_trip_commission(trip_id=self.trip.pk)

        self.trip.status = Trip.Status.CANCELLED
        self.trip.save(update_fields=["status"])

        FinancialTransaction.objects.create(
            captain=self.captain,
            wallet=self.wallet,
            financial_record=self.record,
            transaction_type=FinancialTransaction.TransactionType.COMMISSION_DEDUCTION,
            amount=self.record.commission_amount,
            reference=f"trip-commission:{self.record.pk}",
            description="Test commission deduction",
        )

        with self.assertRaises(ValidationError):
            release_trip_commission_reservation(trip_id=self.trip.pk)

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.reserved_balance, Decimal("10.00"))


    def test_release_detects_commission_deduction_when_record_pk_differs(self):
        # Create a completed trip and financial record to advance the record ID.
        dummy_trip = Trip.objects.create(
            customer=self.trip.customer,
            captain=self.captain,
            pickup_address="Dummy Pickup",
            pickup_latitude=Decimal("29.9670"),
            pickup_longitude=Decimal("32.5498"),
            destination_address="Dummy Destination",
            destination_latitude=Decimal("29.9800"),
            destination_longitude=Decimal("32.5600"),
            status=Trip.Status.COMPLETED,
            final_fare=Decimal("100.00"),
            commission_percentage=Decimal("10.00"),
        )
        create_trip_financial_record(trip_id=dummy_trip.pk)

        # Advance the trip ID without creating a financial record.
        Trip.objects.create(
            customer=self.trip.customer,
            pickup_address="Extra Pickup",
            pickup_latitude=Decimal("29.9670"),
            pickup_longitude=Decimal("32.5498"),
            destination_address="Extra Destination",
            destination_latitude=Decimal("29.9800"),
            destination_longitude=Decimal("32.5600"),
            status=Trip.Status.REQUESTED,
        )

        # Create the target trip and its financial record.
        target_trip = Trip.objects.create(
            customer=self.trip.customer,
            captain=self.captain,
            pickup_address="Target Pickup",
            pickup_latitude=Decimal("29.9670"),
            pickup_longitude=Decimal("32.5498"),
            destination_address="Target Destination",
            destination_latitude=Decimal("29.9800"),
            destination_longitude=Decimal("32.5600"),
            status=Trip.Status.COMPLETED,
            final_fare=Decimal("100.00"),
            commission_percentage=Decimal("10.00"),
        )
        target_record = create_trip_financial_record(trip_id=target_trip.pk)

        # Verify the IDs really differ for this same trip.
        self.assertNotEqual(target_trip.pk, target_record.pk)

        target_trip.status = Trip.Status.ACCEPTED
        target_trip.estimated_fare = Decimal("100.00")
        target_trip.save(update_fields=["status", "estimated_fare"])

        reserve_trip_commission(trip_id=target_trip.pk)

        target_trip.status = Trip.Status.CANCELLED
        target_trip.save(update_fields=["status"])

        FinancialTransaction.objects.create(
            captain=self.captain,
            wallet=self.wallet,
            financial_record=target_record,
            transaction_type=FinancialTransaction.TransactionType.COMMISSION_DEDUCTION,
            amount=target_record.commission_amount,
            reference=f"trip-commission:{target_record.pk}",
            description="Test commission deduction",
        )

        with self.assertRaises(ValidationError):
            release_trip_commission_reservation(trip_id=target_trip.pk)

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.reserved_balance, Decimal("10.00"))




    def test_reserve_detects_commission_deduction_when_record_pk_differs(self):
        # Create a completed trip and its financial record.
        dummy_trip = Trip.objects.create(
            customer=self.trip.customer,
            captain=self.captain,
            pickup_address="Dummy Pickup",
            pickup_latitude=Decimal("29.9670"),
            pickup_longitude=Decimal("32.5498"),
            destination_address="Dummy Destination",
            destination_latitude=Decimal("29.9800"),
            destination_longitude=Decimal("32.5600"),
            status=Trip.Status.COMPLETED,
            final_fare=Decimal("100.00"),
            commission_percentage=Decimal("10.00"),
        )
        create_trip_financial_record(trip_id=dummy_trip.pk)

        # Advance trip IDs without creating financial records.
        for i in range(2):
            Trip.objects.create(
                customer=self.trip.customer,
                pickup_address=f"Extra Pickup {i}",
                pickup_latitude=Decimal("29.9670"),
                pickup_longitude=Decimal("32.5498"),
                destination_address=f"Extra Destination {i}",
                destination_latitude=Decimal("29.9800"),
                destination_longitude=Decimal("32.5600"),
                status=Trip.Status.REQUESTED,
            )

        target_trip = Trip.objects.create(
            customer=self.trip.customer,
            captain=self.captain,
            pickup_address="Target Pickup",
            pickup_latitude=Decimal("29.9670"),
            pickup_longitude=Decimal("32.5498"),
            destination_address="Target Destination",
            destination_latitude=Decimal("29.9800"),
            destination_longitude=Decimal("32.5600"),
            status=Trip.Status.COMPLETED,
            final_fare=Decimal("100.00"),
            commission_percentage=Decimal("10.00"),
        )
        target_record = create_trip_financial_record(trip_id=target_trip.pk)

        self.assertNotEqual(target_trip.pk, target_record.pk)

        # Create a previous commission deduction for this trip.
        FinancialTransaction.objects.create(
            captain=self.captain,
            wallet=self.wallet,
            financial_record=target_record,
            transaction_type=FinancialTransaction.TransactionType.COMMISSION_DEDUCTION,
            amount=target_record.commission_amount,
            reference=f"trip-commission:{target_record.pk}",
            description="Test commission deduction",
        )

        target_trip.status = Trip.Status.ACCEPTED
        target_trip.estimated_fare = Decimal("100.00")
        target_trip.save(update_fields=["status", "estimated_fare"])

        with self.assertRaises(ValidationError):
            reserve_trip_commission(trip_id=target_trip.pk)

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.reserved_balance, Decimal("0.00"))




    def test_credit_wallet_rejects_reference_used_by_another_captain(self):
        reference = "topup-reference-other-captain"

        credit_wallet(
            captain=self.captain,
            amount=Decimal("25.00"),
            reference=reference,
        )

        other_user = User.objects.create_user(
            username="other_topup_captain",
            password="test-password",
            role=User.Role.CAPTAIN,
        )
        other_captain = CaptainProfile.objects.create(user=other_user)

        with self.assertRaises(ValidationError):
            credit_wallet(
                captain=other_captain,
                amount=Decimal("25.00"),
                reference=reference,
            )

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("125.00"))

        self.assertFalse(
            CaptainWallet.objects.filter(captain=other_captain).exists()
        )

        self.assertEqual(
            FinancialTransaction.objects.filter(reference=reference).count(),
            1,
        )



    def test_release_rejects_existing_release_with_wrong_captain(self):
        self.trip.status = Trip.Status.ACCEPTED
        self.trip.estimated_fare = Decimal("100.00")
        self.trip.save(update_fields=["status", "estimated_fare"])

        reserve_trip_commission(trip_id=self.trip.pk)

        self.trip.status = Trip.Status.CANCELLED
        self.trip.save(update_fields=["status"])

        FinancialTransaction.objects.create(
            captain=self.captain,
            wallet=self.wallet,
            transaction_type=FinancialTransaction.TransactionType.COMMISSION_RELEASE,
            amount=Decimal("10.00"),
            reference=f"trip-commission-release:{self.trip.pk}",
            description="Inconsistent release test",
        )

        other_user = User.objects.create_user(
            username="release_other_captain",
            password="test-password",
            role=User.Role.CAPTAIN,
        )
        other_captain = CaptainProfile.objects.create(user=other_user)

        release = FinancialTransaction.objects.get(
            reference=f"trip-commission-release:{self.trip.pk}"
        )
        release.captain = other_captain
        release.save(update_fields=["captain"])

        with self.assertRaises(ValidationError):
            release_trip_commission_reservation(trip_id=self.trip.pk)

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.reserved_balance, Decimal("10.00"))




    def test_final_commission_above_reservation_uses_available_balance(self):
        self.trip.status = Trip.Status.ACCEPTED
        self.trip.estimated_fare = Decimal("100.00")
        self.trip.save(update_fields=["status", "estimated_fare"])

        reserve_trip_commission(trip_id=self.trip.pk)

        self.trip.status = Trip.Status.COMPLETED
        self.trip.final_fare = Decimal("150.00")
        self.trip.save(update_fields=["status", "final_fare"])

        self.record.refresh_from_db()
        self.record.gross_fare = Decimal("150.00")
        self.record.commission_amount = Decimal("15.00")
        self.record.captain_earnings = Decimal("135.00")
        self.record.save(
            update_fields=[
                "gross_fare",
                "commission_amount",
                "captain_earnings",
            ]
        )

        wallet = deduct_trip_commission(
            trip_financial_record_id=self.record.pk,
        )
        wallet.refresh_from_db()

        self.assertEqual(wallet.balance, Decimal("85.00"))
        self.assertEqual(wallet.reserved_balance, Decimal("0.00"))
        self.assertEqual(wallet.available_balance, Decimal("85.00"))

        self.record.refresh_from_db()
        self.assertEqual(
            self.record.commission_status,
            TripFinancialRecord.CommissionStatus.COLLECTED,
        )





    def test_final_commission_above_reservation_rejects_insufficient_available_balance(self):
        self.trip.status = Trip.Status.ACCEPTED
        self.trip.estimated_fare = Decimal("100.00")
        self.trip.save(update_fields=["status", "estimated_fare"])

        reserve_trip_commission(trip_id=self.trip.pk)

        self.wallet.refresh_from_db()
        self.wallet.balance = Decimal("12.00")
        self.wallet.save(update_fields=["balance", "updated_at"])

        self.trip.status = Trip.Status.COMPLETED
        self.trip.final_fare = Decimal("150.00")
        self.trip.save(update_fields=["status", "final_fare"])

        self.record.refresh_from_db()
        self.record.gross_fare = Decimal("150.00")
        self.record.commission_amount = Decimal("15.00")
        self.record.captain_earnings = Decimal("135.00")
        self.record.save(
            update_fields=[
                "gross_fare",
                "commission_amount",
                "captain_earnings",
            ]
        )

        with self.assertRaisesMessage(
            ValidationError,
            "Insufficient available wallet balance to settle commission.",
        ):
            deduct_trip_commission(
                trip_financial_record_id=self.record.pk,
            )

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("12.00"))
        self.assertEqual(self.wallet.reserved_balance, Decimal("10.00"))
        self.assertEqual(self.wallet.available_balance, Decimal("2.00"))

        self.assertFalse(
            FinancialTransaction.objects.filter(
                reference=f"trip-commission:{self.record.pk}",
            ).exists()
        )

        self.record.refresh_from_db()
        self.assertEqual(
            self.record.commission_status,
            TripFinancialRecord.CommissionStatus.PENDING,
        )



    def test_final_commission_below_reservation_releases_excess(self):
        self.trip.status = Trip.Status.ACCEPTED
        self.trip.estimated_fare = Decimal("100.00")
        self.trip.save(update_fields=["status", "estimated_fare"])

        reserve_trip_commission(trip_id=self.trip.pk)

        self.trip.status = Trip.Status.COMPLETED
        self.trip.final_fare = Decimal("50.00")
        self.trip.save(update_fields=["status", "final_fare"])

        self.record.refresh_from_db()
        self.record.gross_fare = Decimal("50.00")
        self.record.commission_amount = Decimal("5.00")
        self.record.captain_earnings = Decimal("45.00")
        self.record.save(
            update_fields=[
                "gross_fare",
                "commission_amount",
                "captain_earnings",
            ]
        )

        wallet = deduct_trip_commission(
            trip_financial_record_id=self.record.pk,
        )
        wallet.refresh_from_db()

        self.assertEqual(wallet.balance, Decimal("95.00"))
        self.assertEqual(wallet.reserved_balance, Decimal("0.00"))
        self.assertEqual(wallet.available_balance, Decimal("95.00"))


    def test_final_commission_below_reservation_records_release(self):
        self.trip.status = Trip.Status.ACCEPTED
        self.trip.estimated_fare = Decimal("100.00")
        self.trip.save(update_fields=["status", "estimated_fare"])

        reserve_trip_commission(trip_id=self.trip.pk)

        self.trip.status = Trip.Status.COMPLETED
        self.trip.final_fare = Decimal("50.00")
        self.trip.save(update_fields=["status", "final_fare"])

        self.record.refresh_from_db()
        self.record.gross_fare = Decimal("50.00")
        self.record.commission_amount = Decimal("5.00")
        self.record.captain_earnings = Decimal("45.00")
        self.record.save(
            update_fields=[
                "gross_fare",
                "commission_amount",
                "captain_earnings",
            ]
        )

        deduct_trip_commission(
            trip_financial_record_id=self.record.pk,
        )

        release = FinancialTransaction.objects.filter(
            reference=f"trip-commission-release-excess:{self.trip.pk}",
            transaction_type=FinancialTransaction.TransactionType.COMMISSION_RELEASE,
        ).first()

        self.assertIsNotNone(
            release,
            "Excess reservation should be recorded as a release transaction.",
        )
        self.assertEqual(release.amount, Decimal("5.00"))

        self.wallet.refresh_from_db()
        balance_after_first_deduction = self.wallet.balance

        deduct_trip_commission(
            trip_financial_record_id=self.record.pk,
        )

        self.wallet.refresh_from_db()

        self.assertEqual(
            self.wallet.balance,
            balance_after_first_deduction,
            "Repeated commission deduction must not deduct twice.",
        )




    def test_deduction_rejects_reservation_linked_to_wrong_captain(self):
        self.trip.status = Trip.Status.ACCEPTED
        self.trip.estimated_fare = Decimal("100.00")
        self.trip.save(update_fields=["status", "estimated_fare"])

        reserve_trip_commission(trip_id=self.trip.pk)

        reservation = FinancialTransaction.objects.get(
            reference=f"trip-commission-reserve:{self.trip.pk}"
        )

        other_user = User.objects.create_user(
            username="wrong_reservation_captain",
            password="test-password",
            role=User.Role.CAPTAIN,
        )
        other_captain = CaptainProfile.objects.create(user=other_user)

        reservation.captain = other_captain
        reservation.save(update_fields=["captain"])

        self.trip.status = Trip.Status.COMPLETED
        self.trip.final_fare = Decimal("100.00")
        self.trip.save(update_fields=["status", "final_fare"])

        with self.assertRaises(ValidationError):
            deduct_trip_commission(
                trip_financial_record_id=self.record.pk
            )

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("100.00"))
        self.assertEqual(self.wallet.reserved_balance, Decimal("10.00"))
        self.assertFalse(
            FinancialTransaction.objects.filter(
                reference=f"trip-commission:{self.record.pk}"
            ).exists()
        )





    def test_release_rejects_reservation_linked_to_wrong_captain(self):
        self.trip.status = Trip.Status.ACCEPTED
        self.trip.estimated_fare = Decimal("100.00")
        self.trip.save(update_fields=["status", "estimated_fare"])

        reserve_trip_commission(trip_id=self.trip.pk)

        reservation = FinancialTransaction.objects.get(
            reference=f"trip-commission-reserve:{self.trip.pk}"
        )

        other_user = User.objects.create_user(
            username="wrong_release_reservation_captain",
            password="test-password",
            role=User.Role.CAPTAIN,
        )
        other_captain = CaptainProfile.objects.create(user=other_user)

        reservation.captain = other_captain
        reservation.save(update_fields=["captain"])

        self.trip.status = Trip.Status.CANCELLED
        self.trip.save(update_fields=["status"])

        with self.assertRaises(ValidationError):
            release_trip_commission_reservation(trip_id=self.trip.pk)

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("100.00"))
        self.assertEqual(self.wallet.reserved_balance, Decimal("10.00"))

        self.assertFalse(
            FinancialTransaction.objects.filter(
                reference=f"trip-commission-release:{self.trip.pk}"
            ).exists()
        )




    def test_repeated_zero_commission_settlement_releases_reservation_once(self):
        self.trip.status = Trip.Status.ACCEPTED
        self.trip.estimated_fare = Decimal("100.00")
        self.trip.save(update_fields=["status", "estimated_fare"])

        reserve_trip_commission(trip_id=self.trip.pk)

        self.trip.status = Trip.Status.COMPLETED
        self.trip.final_fare = Decimal("0.00")
        self.trip.save(update_fields=["status", "final_fare"])

        self.record.refresh_from_db()
        self.record.gross_fare = Decimal("0.00")
        self.record.commission_amount = Decimal("0.00")
        self.record.captain_earnings = Decimal("0.00")
        self.record.save(
            update_fields=[
                "gross_fare",
                "commission_amount",
                "captain_earnings",
            ]
        )

        for _ in range(2):
            deduct_trip_commission(
                trip_financial_record_id=self.record.pk
            )

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("100.00"))
        self.assertEqual(self.wallet.reserved_balance, Decimal("0.00"))

        self.assertEqual(
            FinancialTransaction.objects.filter(
                reference=f"trip-commission-release-zero:{self.trip.pk}"
            ).count(),
            1,
        )
        self.assertFalse(
            FinancialTransaction.objects.filter(
                reference=f"trip-commission:{self.record.pk}"
            ).exists()
        )



    def test_zero_final_commission_releases_existing_reservation(self):
        self.trip.status = Trip.Status.ACCEPTED
        self.trip.estimated_fare = Decimal("100.00")
        self.trip.save(update_fields=["status", "estimated_fare"])

        reserve_trip_commission(trip_id=self.trip.pk)

        self.trip.status = Trip.Status.COMPLETED
        self.trip.final_fare = Decimal("0.00")
        self.trip.save(update_fields=["status", "final_fare"])

        self.record.refresh_from_db()
        self.record.gross_fare = Decimal("0.00")
        self.record.commission_amount = Decimal("0.00")
        self.record.captain_earnings = Decimal("0.00")
        self.record.save(
            update_fields=[
                "gross_fare",
                "commission_amount",
                "captain_earnings",
            ]
        )

        wallet = deduct_trip_commission(
            trip_financial_record_id=self.record.pk
        )
        wallet.refresh_from_db()

        self.assertEqual(wallet.balance, Decimal("100.00"))
        self.assertEqual(wallet.reserved_balance, Decimal("0.00"))
        self.assertEqual(wallet.available_balance, Decimal("100.00"))
        self.assertFalse(
            FinancialTransaction.objects.filter(
                reference=f"trip-commission:{self.record.pk}"
            ).exists()
        )




    def test_zero_percent_commission_completes_settlement_without_zero_transaction(self):
        CommissionConfig.objects.filter(is_active=True).update(
            percentage=Decimal("0.00")
        )

        self.trip.status = Trip.Status.COMPLETED
        self.trip.final_fare = Decimal("100.00")
        self.trip.save(update_fields=["status", "final_fare"])

        self.record.refresh_from_db()
        self.record.commission_amount = Decimal("0.00")
        self.record.captain_earnings = Decimal("100.00")
        self.record.save(
            update_fields=["commission_amount", "captain_earnings"]
        )

        wallet = deduct_trip_commission(
            trip_financial_record_id=self.record.pk
        )
        wallet.refresh_from_db()

        self.assertEqual(wallet.balance, Decimal("100.00"))
        self.assertEqual(wallet.reserved_balance, Decimal("0.00"))
        self.assertFalse(
            FinancialTransaction.objects.filter(
                reference=f"trip-commission:{self.record.pk}"
            ).exists()
        )

        self.record.refresh_from_db()
        self.assertEqual(
            self.record.commission_status,
            TripFinancialRecord.CommissionStatus.COLLECTED,
        )