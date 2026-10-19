from django import forms
from django.contrib import admin

from .models import (
    CommissionConfig,
    FinancialTransaction,
    TripFinancialRecord,
    TripPricingConfig,
)


class CommissionConfigAdminForm(forms.ModelForm):
    class Meta:
        model = CommissionConfig
        fields = "__all__"

    def clean_is_active(self):
        is_active = self.cleaned_data.get("is_active")

        if is_active:
            active_configs = CommissionConfig.objects.filter(
                is_active=True
            )

            if self.instance.pk:
                active_configs = active_configs.exclude(
                    pk=self.instance.pk
                )

            if active_configs.exists():
                raise forms.ValidationError(
                    "يوجد إعداد عمولة نشط بالفعل. "
                    "عطّل الإعداد الحالي أولًا قبل تفعيل إعداد آخر."
                )

        return is_active


@admin.register(TripPricingConfig)
class TripPricingConfigAdmin(admin.ModelAdmin):
    list_display = (
        "city",
        "base_fare",
        "price_per_km",
        "minimum_fare",
        "maximum_fare",
        "is_active",
    )
    list_filter = ("is_active",)
    search_fields = ("city",)


@admin.register(CommissionConfig)
class CommissionConfigAdmin(admin.ModelAdmin):
    form = CommissionConfigAdminForm

    list_display = (
        "name",
        "percentage",
        "is_active",
        "updated_at",
    )
    list_filter = ("is_active",)
    search_fields = ("name",)


@admin.register(TripFinancialRecord)
class TripFinancialRecordAdmin(admin.ModelAdmin):
    list_display = (
        "trip",
        "gross_fare",
        "commission_amount",
        "captain_earnings",
        "collection_status",
        "commission_status",
        "settlement_status",
        "created_at",
    )
    list_filter = (
        "collection_status",
        "commission_status",
        "settlement_status",
        "trip__payment_method",
        "created_at",
    )
    search_fields = ("trip__id",)
    readonly_fields = (
        "trip",
        "gross_fare",
        "commission_percentage",
        "commission_amount",
        "captain_earnings",
        "created_at",
    )


@admin.register(FinancialTransaction)
class FinancialTransactionAdmin(admin.ModelAdmin):
    list_display = (
        "captain",
        "transaction_type",
        "amount",
        "reference",
        "created_at",
    )
    list_filter = (
        "transaction_type",
        "created_at",
    )
    search_fields = (
        "captain__user__username",
        "reference",
        "description",
    )
    ordering = ("-created_at",)

    readonly_fields = (
        "captain",
        "wallet",
        "financial_record",
        "transaction_type",
        "amount",
        "reference",
        "description",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
