from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.utils.html import format_html

from .models import Vehicle
from accounts.services.reviews import approve_vehicle, reject_vehicle


class VehicleRejectionForm(forms.Form):
    reason = forms.CharField(
        label="Rejection reason",
        widget=forms.Textarea,
        min_length=5,
        max_length=2000,
    )


@admin.register(Vehicle)
class VehicleAdmin(admin.ModelAdmin):
    list_display = (
        "brand",
        "model_name",
        "manufacture_year",
        "plate_number",
        "captain",
        "approval_status",
        "reviewed_at",
        "reviewed_by",
        "rejection_action",
    )
    list_filter = ("vehicle_type", "approval_status", "manufacture_year")
    search_fields = (
        "brand",
        "model_name",
        "plate_number",
        "chassis_number",
        "captain__user__username",
    )
    readonly_fields = (
        "approval_status",
        "rejection_reason",
        "created_at",
        "updated_at",
        "reviewed_at",
        "reviewed_by",
        "front_preview",
        "rear_preview",
        "side_preview",
        "plate_preview",
    )
    actions = ("approve_vehicles",)

    fields = (
        "captain",
        "vehicle_type",
        "brand",
        "model_name",
        "manufacture_year",
        "plate_number",
        "chassis_number",
        "front_photo",
        "front_preview",
        "rear_photo",
        "rear_preview",
        "side_photo",
        "side_preview",
        "plate_photo",
        "plate_preview",
        "approval_status",
        "rejection_reason",
        "reviewed_at",
        "reviewed_by",
        "created_at",
        "updated_at",
    )

    @admin.display(description="Front photo")
    def front_preview(self, obj):
        return self._image_preview(obj.front_photo)

    @admin.display(description="Rear photo")
    def rear_preview(self, obj):
        return self._image_preview(obj.rear_photo)

    @admin.display(description="Side photo")
    def side_preview(self, obj):
        return self._image_preview(obj.side_photo)

    @admin.display(description="Plate photo")
    def plate_preview(self, obj):
        return self._image_preview(obj.plate_photo)

    @staticmethod
    def _image_preview(field):
        if not field:
            return "No image"

        return format_html(
            '<a href="{}" target="_blank" rel="noopener">'
            '<img src="{}" style="max-height:160px;max-width:240px;" />'
            "</a>",
            field.url,
            field.url,
        )

    @admin.display(description="Review action")
    def rejection_action(self, obj):
        if obj.approval_status == Vehicle.ApprovalStatus.REJECTED:
            return "Rejected"

        url = reverse(
            "admin:vehicles_vehicle_reject",
            args=[obj.pk],
        )
        return format_html('<a href="{}">Reject vehicle</a>', url)

    @admin.action(description="Approve selected vehicles")
    def approve_vehicles(self, request, queryset):
        approved = 0

        for vehicle in queryset.select_related("captain__user"):
            try:
                approve_vehicle(vehicle, request.user)
                approved += 1
            except ValidationError as exc:
                self.message_user(
                    request,
                    f"{vehicle.plate_number}: {'; '.join(exc.messages)}",
                    messages.ERROR,
                )

        if approved:
            self.message_user(
                request,
                f"{approved} vehicle(s) approved.",
                messages.SUCCESS,
            )

    def get_urls(self):
        custom_urls = [
            path(
                "<path:object_id>/reject/",
                self.admin_site.admin_view(self.reject_view),
                name="vehicles_vehicle_reject",
            ),
        ]
        return custom_urls + super().get_urls()

   
    def reject_view(self, request, object_id):
        from django.core.exceptions import PermissionDenied

        if not self.has_change_permission(request):
            raise PermissionDenied

        vehicle = get_object_or_404(
            Vehicle.objects.select_related("captain__user"),
            pk=object_id,
        )

        if request.method == "POST":
            form = VehicleRejectionForm(request.POST)

            if form.is_valid():
                try:
                    reject_vehicle(
                        vehicle,
                        request.user,
                        form.cleaned_data["reason"],
                    )
                    self.message_user(
                        request,
                        "Vehicle rejected. The decision was recorded.",
                        messages.WARNING,
                    )
                    return redirect(
                        reverse("admin:vehicles_vehicle_changelist")
                    )
                except ValidationError as exc:
                    form.add_error(None, "; ".join(exc.messages))
        else:
            form = VehicleRejectionForm()

        return render(
            request,
            "admin/vehicles/vehicle/reject.html",
            {
                "form": form,
                "vehicle": vehicle,
                "opts": self.model._meta,
                "title": "Reject vehicle",
            },
        )
