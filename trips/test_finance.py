from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from trips.models import (
    CommissionConfig,
    Trip,
    TripFinancialRecord,
)
from trips.services.finance import (
    calculate_commission,
    confirm_cash_collection,
    create_trip_financial_record,
)


class CommissionCalculationTests(TestCase):
    def test_calculates_commission_and_captain_earnings(self):
        commission, earnings = calculate_commission(
            gross_fare=Decimal("100.00"),
            percentage=Decimal("15.00"),
        )

        self.assertEqual(commission, Decimal("15.00"))
        self.assertEqual(earnings, Decimal("85.00"))

    def test_handles_half_cent_rounding(self):
        commission, earnings = calculate_commission(
            gross_fare=Decimal("0.10"),
            percentage=Decimal("5.00"),
        )

        self.assertEqual(commission, Decimal("0.01"))
        self.assertEqual(earnings, Decimal("0.09"))

    def test_zero_commission(self):
        commission, earnings = calculate_commission(
            gross_fare=Decimal("100.00"),
            percentage=Decimal("0.00"),
        )

        self.assertEqual(commission, Decimal("0.00"))
        self.assertEqual(earnings, Decimal("100.00"))

    def test_full_commission(self):
        commission, earnings = calculate_commission(
            gross_fare=Decimal("100.00"),
            percentage=Decimal("100.00"),
        )

        self.assertEqual(commission, Decimal("100.00"))
        self.assertEqual(earnings, Decimal("0.00"))

    def test_rejects_negative_fare(self):
        with self.assertRaises(ValidationError):
            calculate_commission(
                gross_fare=Decimal("-1.00"),
                percentage=Decimal("15.00"),
            )

    def test_rejects_invalid_percentage(self):
        with self.assertRaises(ValidationError):
            calculate_commission(
                gross_fare=Decimal("100.00"),
                percentage=Decimal("101.00"),
            )


class TripFinancialRecordTests(TestCase):
    def setUp(self):
        from accounts.models import CustomerProfile, User, CaptainProfile
        from vehicles.models import Vehicle

        self.customer_user = User.objects.create_user(
            username="finance_customer",
            password="test-password",
        )
        self.customer = CustomerProfile.objects.create(
            user=self.customer_user,
        )

        self.captain_user = User.objects.create_user(
            username="finance_captain",
            password="test-password",
            role=User.Role.CAPTAIN,
        )
        self.captain = CaptainProfile.objects.create(
            user=self.captain_user,
        )

        self.trip = Trip.objects.create(
            customer=self.customer,
            captain=self.captain,
            pickup_address="Pickup",
            pickup_latitude=Decimal("29.9670"),
            pickup_longitude=Decimal("32.5498"),
            destination_address="Destination",
            destination_latitude=Decimal("29.9800"),
            destination_longitude=Decimal("32.5600"),
            status=Trip.Status.COMPLETED,
            final_fare=Decimal("100.00"),
        )

        CommissionConfig.objects.create(
            name="default",
            percentage=Decimal("10.00"),
            is_active=True,
        )

    def test_creates_financial_record(self):
        record = create_trip_financial_record(
            trip_id=self.trip.pk,
        )

        self.assertEqual(record.gross_fare, Decimal("100.00"))
        self.assertEqual(record.commission_amount, Decimal("10.00"))
        self.assertEqual(record.captain_earnings, Decimal("90.00"))
        self.assertEqual(
            record.settlement_status,
            TripFinancialRecord.SettlementStatus.PENDING,
        )

    def test_does_not_duplicate_existing_record(self):
        first = create_trip_financial_record(
            trip_id=self.trip.pk,
        )
        second = create_trip_financial_record(
            trip_id=self.trip.pk,
        )

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(
            TripFinancialRecord.objects.filter(trip=self.trip).count(),
            1,
        )

    def test_rejects_uncompleted_trip(self):
        self.trip.status = Trip.Status.IN_PROGRESS
        self.trip.save(update_fields=["status"])

        with self.assertRaises(ValidationError):
            create_trip_financial_record(
                trip_id=self.trip.pk,
            )

    def test_rejects_missing_commission_config(self):
        CommissionConfig.objects.all().delete()

        with self.assertRaises(ValidationError):
            create_trip_financial_record(
                trip_id=self.trip.pk,
            )

    def test_database_prevents_duplicate_financial_records(self):
        record = create_trip_financial_record(
            trip_id=self.trip.pk,
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                TripFinancialRecord.objects.create(
                    trip=self.trip,
                    gross_fare=record.gross_fare,
                    commission_percentage=record.commission_percentage,
                    commission_amount=record.commission_amount,
                    captain_earnings=record.captain_earnings,
                )

    def test_financial_record_starts_with_pending_statuses(self):
        record = create_trip_financial_record(
            trip_id=self.trip.pk,
        )

        self.assertEqual(
            record.collection_status,
            TripFinancialRecord.CollectionStatus.PENDING,
        )
        self.assertEqual(
            record.commission_status,
            TripFinancialRecord.CommissionStatus.PENDING,
        )
        self.assertEqual(
            record.settlement_status,
            TripFinancialRecord.SettlementStatus.PENDING,
        )

    def test_financial_record_stores_collection_and_settlement_statuses(self):
        record = create_trip_financial_record(
            trip_id=self.trip.pk,
        )

        record.collection_status = (
            TripFinancialRecord.CollectionStatus.COLLECTED
        )
        record.commission_status = (
            TripFinancialRecord.CommissionStatus.COLLECTED
        )
        record.settlement_status = (
            TripFinancialRecord.SettlementStatus.SETTLED
        )
        record.save(
            update_fields=[
                "collection_status",
                "commission_status",
                "settlement_status",
            ]
        )

        record.refresh_from_db()

        self.assertEqual(
            record.collection_status,
            TripFinancialRecord.CollectionStatus.COLLECTED,
        )
        self.assertEqual(
            record.commission_status,
            TripFinancialRecord.CommissionStatus.COLLECTED,
        )
        self.assertEqual(
            record.settlement_status,
            TripFinancialRecord.SettlementStatus.SETTLED,
        )

    def test_assigned_captain_can_confirm_cash_collection(self):
        record = create_trip_financial_record(
            trip_id=self.trip.pk,
        )

        self.trip.payment_method = Trip.PaymentMethod.CASH
        self.trip.save(update_fields=["payment_method"])

        record = confirm_cash_collection(
            trip_id=self.trip.pk,
            confirmed_by=self.captain_user,
        )

        self.assertEqual(
            record.collection_status,
            TripFinancialRecord.CollectionStatus.COLLECTED,
        )

    def test_customer_cannot_confirm_cash_collection(self):
        create_trip_financial_record(
            trip_id=self.trip.pk,
        )

        self.trip.payment_method = Trip.PaymentMethod.CASH
        self.trip.save(update_fields=["payment_method"])

        with self.assertRaises(ValidationError):
            confirm_cash_collection(
                trip_id=self.trip.pk,
                confirmed_by=self.customer_user,
            )

    def test_cash_collection_rejects_online_trip(self):
        create_trip_financial_record(
            trip_id=self.trip.pk,
        )

        self.trip.payment_method = Trip.PaymentMethod.ONLINE
        self.trip.save(update_fields=["payment_method"])

        with self.assertRaises(ValidationError):
            confirm_cash_collection(
                trip_id=self.trip.pk,
                confirmed_by=self.captain_user,
            )