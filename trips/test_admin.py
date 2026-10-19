
from django.contrib import admin
from django.test import RequestFactory, TestCase
from django.db import IntegrityError, transaction
from accounts.models import User
from trips.admin import CommissionConfigAdmin
from trips.models import CommissionConfig
from trips.admin import FinancialTransactionAdmin
from trips.models import FinancialTransaction


class CommissionConfigAdminTests(TestCase):
    def setUp(self):
        self.request = RequestFactory().post(
            "/admin/trips/commissionconfig/"
        )
        self.user = User.objects.create_superuser(
            username="commission_admin_test",
            email="commission-admin@example.com",
            password="test-password",
        )
        self.request.user = self.user

        self.model_admin = CommissionConfigAdmin(
            CommissionConfig,
            admin.site,
        )

    def test_cannot_activate_second_commission_config(self):
        CommissionConfig.objects.create(
            name="first",
            percentage="10.00",
            is_active=True,
        )

        second = CommissionConfig(
            name="second",
            percentage="15.00",
            is_active=True,
        )

        form = self.model_admin.get_form(self.request)(
            data={
                "name": "second",
                "percentage": "15.00",
                "is_active": True,
            },
            instance=second,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("is_active", form.errors)
        self.assertFalse(
            CommissionConfig.objects.filter(name="second").exists()
        )

    def test_can_update_existing_active_config(self):
        config = CommissionConfig.objects.create(
            name="default",
            percentage="10.00",
            is_active=True,
        )

        form = self.model_admin.get_form(self.request)(
            data={
                "name": "default",
                "percentage": "12.00",
                "is_active": True,
            },
            instance=config,
        )

        self.assertTrue(form.is_valid(), form.errors)

        updated_config = form.save()
        updated_config.refresh_from_db()

        self.assertEqual(str(updated_config.percentage), "12.00")
        self.assertTrue(updated_config.is_active)



    def test_database_rejects_second_active_commission_config(self):
        CommissionConfig.objects.create(
            name="first",
            percentage="10.00",
            is_active=True,
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                CommissionConfig.objects.create(
                    name="second",
                    percentage="15.00",
                    is_active=True,
                )

        self.assertEqual(
            CommissionConfig.objects.filter(is_active=True).count(),
            1,
        )

class FinancialTransactionAdminTests(TestCase):
    def setUp(self):
        self.model_admin = FinancialTransactionAdmin(
            FinancialTransaction,
            admin.site,
        )

    def test_financial_transaction_is_registered(self):
        self.assertTrue(
            admin.site.is_registered(FinancialTransaction)
        )
        self.assertIsInstance(
            admin.site._registry[FinancialTransaction],
            FinancialTransactionAdmin,
        )

    def test_adding_financial_transactions_is_disabled(self):
        self.assertFalse(
            self.model_admin.has_add_permission(None)
        )

    def test_deleting_financial_transactions_is_disabled(self):
        self.assertFalse(
            self.model_admin.has_delete_permission(None)
        )

    def test_all_financial_transaction_fields_are_read_only(self):
        expected_fields = {
            "captain",
            "wallet",
            "financial_record",
            "transaction_type",
            "amount",
            "reference",
            "description",
            "created_at",
        }

        actual_fields = set(
            self.model_admin.get_readonly_fields(None)
        )

        self.assertEqual(actual_fields, expected_fields)
