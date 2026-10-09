from django import forms
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils.html import format_html

from accounts.services.reviews import (
    approve_captain,
    reject_captain,
    approve_document,
    reject_document,
)
from .models import (
    User,
    CustomerProfile,
    CaptainProfile,
    CaptainDocument,
)


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    list_display = (
        "username", "email", "role", "is_active", "is_staff",
    )
    list_filter = ("role", "is_active", "is_staff")
    search_fields = ("username", "email")


@admin.register(CustomerProfile)
class CustomerProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "phone_number", "is_phone_verified")
    search_fields = ("user__username", "phone_number")


class CaptainRejectionForm(forms.Form):
    reason = forms.CharField(
        label="Rejection reason",
        widget=forms.Textarea,
        min_length=5,
        max_length=2000,
    )


class DocumentRejectionForm(forms.Form):
    reason = forms.CharField(
        label="Rejection reason",
        widget=forms.Textarea,
        required=True,
        min_length=5,
        max_length=2000,
    )


@admin.register(CaptainProfile)
class CaptainProfileAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "phone_number",
        "approval_status",
        "operational_status",
        "average_rating",
        "total_trips",
        "reviewed_at",
        "reviewed_by",
        "rejection_action",
    )
    list_filter = ("approval_status", "operational_status")
    search_fields = ("user__username", "phone_number")

    readonly_fields = (
        "approval_status",
        "operational_status",
        "rejection_reason",
        "reviewed_at",
        "reviewed_by",
    )

    actions = ("approve_captains",)

    @admin.action(description="Approve selected captains")
    def approve_captains(self, request, queryset):
        approved = 0

        for captain in queryset:
            try:
                approve_captain(captain, request.user)
                approved += 1
            except ValidationError as exc:
                self.message_user(
                    request,
                    f"{captain.user.username}: {'; '.join(exc.messages)}",
                    messages.ERROR,
                )

        if approved:
            self.message_user(
                request,
                f"{approved} captain(s) approved.",
                messages.SUCCESS,
            )

    @admin.display(description="Review action")
    def rejection_action(self, obj):
        if obj.approval_status == CaptainProfile.ApprovalStatus.REJECTED:
            return "Rejected"

        url = reverse(
            "admin:accounts_captainprofile_reject",
            args=[obj.pk],
        )
        return format_html('<a href="{}">Reject captain</a>', url)

    def get_urls(self):
        from django.urls import path

        return [
            path(
                "<path:object_id>/reject/",
                self.admin_site.admin_view(self.reject_view),
                name="accounts_captainprofile_reject",
            ),
            *super().get_urls(),
        ]

    def reject_view(self, request, object_id):
        from django.contrib.admin.views.decorators import staff_member_required
        from django.core.exceptions import PermissionDenied
        from django.shortcuts import get_object_or_404, redirect, render
        from django.urls import reverse

        if not self.has_change_permission(request):
            raise PermissionDenied

        captain = get_object_or_404(
            CaptainProfile.objects.select_related("user"),
            pk=object_id,
        )

        if request.method == "POST":
            form = CaptainRejectionForm(request.POST)

            if form.is_valid():
                try:
                    reject_captain(
                        captain,
                        request.user,
                        form.cleaned_data["reason"],
                    )
                    self.message_user(
                        request,
                        "Captain rejected and taken offline.",
                        messages.WARNING,
                    )
                    return redirect(
                        reverse("admin:accounts_captainprofile_changelist")
                    )
                except ValidationError as exc:
                    form.add_error(None, "; ".join(exc.messages))
        else:
            form = CaptainRejectionForm()

        return render(
            request,
            "admin/accounts/captainprofile/reject.html",
            {
                "form": form,
                "captain": captain,
                "opts": self.model._meta,
                "title": "Reject captain",
            },
        )


@admin.register(CaptainDocument)
class CaptainDocumentAdmin(admin.ModelAdmin):
    list_display = (
        "captain",
        "document_type",
        "review_status",
        "expires_at",
        "reviewed_at",
        "reviewed_by",
        "created_at",
        "review_actions",
    )
    list_filter = ("document_type", "review_status")
    search_fields = ("captain__user__username",)

    readonly_fields = (
        "review_status",
        "rejection_reason",
        "reviewed_at",
        "reviewed_by",
        "created_at",
    )

    @admin.display(description="Review action")
    def review_actions(self, obj):
        from django.urls import reverse
        from django.utils.html import format_html

        if obj.review_status == CaptainDocument.ReviewStatus.APPROVED:
            return "Approved"

        if obj.review_status == CaptainDocument.ReviewStatus.REJECTED:
            return "Rejected"

        url = reverse(
            "admin:accounts_captaindocument_review",
            args=[obj.pk],
        )
        return format_html(
            '<a href="{}">Review document</a>',
            url,
        )

    def get_urls(self):
        from django.urls import path

        custom_urls = [
            path(
                "<path:object_id>/review/",
                self.admin_site.admin_view(self.review_view),
                name="accounts_captaindocument_review",
            ),
            path(
                "<path:object_id>/file/",
                self.admin_site.admin_view(self.document_file_view),
                name="accounts_captaindocument_file",
            ),
        ]
        return custom_urls + super().get_urls()

    def review_view(self, request, object_id):
        from django.core.exceptions import PermissionDenied
        from django.shortcuts import get_object_or_404, redirect, render

        document = get_object_or_404(
            CaptainDocument.objects.select_related("captain__user"),
            pk=object_id,
        )

        if not self.has_change_permission(request, document):
            raise PermissionDenied

        if document.review_status != CaptainDocument.ReviewStatus.PENDING:
            self.message_user(
                request,
                "This document has already been reviewed.",
                messages.WARNING,
            )
            return redirect("admin:accounts_captaindocument_changelist")

        form = DocumentRejectionForm()

        if request.method == "POST":
            action = request.POST.get("action")

            if action == "approve":
                try:
                    approve_document(document, request.user)
                    self.message_user(
                        request,
                        "Document approved successfully.",
                        messages.SUCCESS,
                    )
                    return redirect(
                        "admin:accounts_captaindocument_changelist"
                    )
                except ValidationError as exc:
                    form.add_error(None, "; ".join(exc.messages))

            elif action == "reject":
                form = DocumentRejectionForm(request.POST)

                if form.is_valid():
                    try:
                        reject_document(
                            document,
                            request.user,
                            form.cleaned_data["reason"],
                        )
                        self.message_user(
                            request,
                            "Document rejected successfully.",
                            messages.WARNING,
                        )
                        return redirect(
                            "admin:accounts_captaindocument_changelist"
                        )
                    except ValidationError as exc:
                        form.add_error(None, "; ".join(exc.messages))
            else:
                form.add_error(None, "Invalid review action.")

        return render(
            request,
            "admin/accounts/captaindocument/review.html",
            {
                "document": document,
                "form": form,
                "opts": self.model._meta,
                "title": "Review captain document",
            },
        )

    def document_file_view(self, request, object_id):
        import mimetypes
        from django.core.exceptions import PermissionDenied
        from django.http import FileResponse
        from django.shortcuts import get_object_or_404

        document = get_object_or_404(
            CaptainDocument,
            pk=object_id,
        )

        if not self.has_change_permission(request, document):
            raise PermissionDenied

        if not document.file:
            from django.http import Http404
            raise Http404("Document file not found.")

        content_type, _ = mimetypes.guess_type(document.file.name)

        try:
            file_handle = document.file.open("rb")
        except (FileNotFoundError, OSError):
            from django.http import Http404
            raise Http404("Document file not found.")

        response = FileResponse(
            file_handle,
            as_attachment=True,
            filename=document.file.name.rsplit("/", 1)[-1],
            content_type=content_type or "application/octet-stream",
        )
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"

        return response