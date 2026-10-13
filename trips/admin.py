from django.contrib import admin

from .models import (
    CommissionConfig,
    TripFinancialRecord,
    TripPricingConfig,
)


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