from datetime import date

from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import render, redirect
from django.views.decorators.http import require_POST
from django.views.decorators.http import require_http_methods

from .forms import (
    CustomerRegistrationForm,
    CaptainRegistrationForm,
    CaptainDocumentForm,
)
from .models import User, CaptainProfile, CaptainDocument
from .services.reviews import set_captain_online_status


def register_customer(request):
    if request.method == "POST":
        form = CustomerRegistrationForm(request.POST)

        if form.is_valid():
            form.save()
            messages.success(
                request,
                "Your customer account has been created successfully.",
            )
            return redirect("accounts:login")
    else:
        form = CustomerRegistrationForm()

    return render(
        request,
        "accounts/register.html",
        {"form": form, "account_type": "Customer"},
    )


def register_captain(request):
    if request.method == "POST":
        form = CaptainRegistrationForm(request.POST)

        if form.is_valid():
            form.save()
            messages.success(
                request,
                "Your captain account was created and is awaiting approval.",
            )
            return redirect("accounts:login")
    else:
        form = CaptainRegistrationForm()

    return render(
        request,
        "accounts/register.html",
        {"form": form, "account_type": "Captain"},
    )


login_view = auth_views.LoginView.as_view(
    template_name="accounts/login.html",
)

logout_view = auth_views.LogoutView.as_view()


@login_required
def dashboard(request):
    user = request.user

    if user.is_staff and (
        user.is_superuser or user.role == User.Role.ADMIN
    ):
        return redirect("/admin/")

    if user.role == User.Role.CAPTAIN:
        try:
            captain = user.captain_profile
        except CaptainProfile.DoesNotExist:
            return render(
                request,
                "accounts/pending.html",
                {"message": "Captain profile not found."},
            )

        return render(
            request,
            "accounts/pending.html",
            {"captain": captain},
        )

    if user.role == User.Role.CUSTOMER:
        return render(request, "accounts/dashboard.html")

    return redirect("accounts:login")


@login_required
@require_POST
def set_operational_status(request):
    user = request.user

    if user.role != User.Role.CAPTAIN:
        messages.error(
            request,
            "Only captain accounts can change operational status.",
        )
        return redirect("accounts:dashboard")

    try:
        captain = user.captain_profile
    except CaptainProfile.DoesNotExist:
        messages.error(request, "Captain profile not found.")
        return redirect("accounts:dashboard")

    requested_status = request.POST.get("status")

    if requested_status not in ("ONLINE", "OFFLINE"):
        messages.error(request, "Invalid operational status.")
        return redirect("accounts:dashboard")

    try:
        set_captain_online_status(
            captain,
            is_online=(requested_status == "ONLINE"),
        )
    except ValidationError as exc:
        for error in exc.messages:
            messages.error(request, error)
    else:
        messages.success(
            request,
            f"Your status is now {requested_status}.",
        )

    return redirect("accounts:dashboard")


@login_required
@require_http_methods(["GET", "POST"])
def captain_documents(request):
    user = request.user

    if user.role != User.Role.CAPTAIN:
        messages.error(
            request,
            "Only captain accounts can manage captain documents.",
        )
        return redirect("accounts:dashboard")

    try:
        captain = user.captain_profile
    except CaptainProfile.DoesNotExist:
        messages.error(request, "Captain profile not found.")
        return redirect("accounts:dashboard")

    if request.method == "POST":
        form = CaptainDocumentForm(
            request.POST,
            request.FILES,
        )

        if form.is_valid():
            document_type = form.cleaned_data["document_type"]
            existing_documents = CaptainDocument.objects.filter(
                captain=captain,
                document_type=document_type,
            )

            blocked = False

            for document in existing_documents:
                if (
                    document.review_status
                    == CaptainDocument.ReviewStatus.PENDING
                ):
                    messages.error(
                        request,
                        "This document is already awaiting review.",
                    )
                    blocked = True
                    break

                if (
                    document.review_status
                    == CaptainDocument.ReviewStatus.APPROVED
                    and (
                        document.expires_at is None
                        or document.expires_at >= date.today()
                    )
                ):
                    messages.error(
                        request,
                        "This document is already approved and valid.",
                    )
                    blocked = True
                    break

            if not blocked:
                document = form.save(commit=False)
                document.captain = captain
                document.review_status = (
                    CaptainDocument.ReviewStatus.PENDING
                )
                document.save()

                messages.success(
                    request,
                    "Document uploaded successfully and is awaiting review.",
                )
                return redirect("accounts:captain_documents")
    else:
        form = CaptainDocumentForm()

    documents = CaptainDocument.objects.filter(
        captain=captain,
    ).order_by("-created_at")

    return render(
        request,
        "accounts/documents.html",
        {
            "form": form,
            "documents": documents,
            "captain": captain,
        },
    )