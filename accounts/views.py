from datetime import date
import json

from django.conf import settings as django_settings
from django.http import JsonResponse
from .forms import CustomerProfileForm

from .forms import CashWalletTopUpForm
from .models import WalletTopUpRequest
from django.utils.crypto import get_random_string

from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import render, redirect
from django.views.decorators.http import require_POST
from django.views.decorators.http import require_http_methods

from .forms import (
    CustomerProfileForm,
    CashWalletTopUpForm,
    CustomerRegistrationForm,
    CaptainRegistrationForm,
    CaptainDocumentForm,
    CustomLoginForm,
)
from .models import User, CaptainProfile, CaptainDocument, CaptainWallet
from .services.notifications import (
    get_user_notifications,
    get_unread_notification_count,
)
from .services.reviews import set_captain_online_status


def home(request):
    if request.user.is_authenticated:
        user = request.user
        if user.is_staff and (
            user.is_superuser or user.role == User.Role.ADMIN
        ):
            return redirect("/admin/")
        return redirect("accounts:dashboard")
    return render(request, "accounts/home.html")


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
    authentication_form=CustomLoginForm,
)

logout_view = auth_views.LogoutView.as_view()




def captain_google_signup(request):
    if request.user.is_authenticated:
        return redirect("accounts:dashboard")

    request.session["google_signup_role"] = "CAPTAIN"
    return redirect("google_login")

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

        wallet = CaptainWallet.objects.filter(
            captain=captain
        ).first()

        from trips.models import CaptainOffer

        my_offers = (
            CaptainOffer.objects
            .filter(captain=captain)
            .select_related("trip")
            .order_by("-created_at")
        )

        return render(
            request,
            "accounts/pending.html",
            {
                "captain": captain,
                "wallet": wallet,
                "my_offers": my_offers,
            },
        )

    if user.role == User.Role.CUSTOMER:
        return render(
            request,
            "accounts/dashboard.html",
            {"carto_api_key": django_settings.CARTO_API_KEY},
        )

    return redirect("accounts:login")



@login_required
@require_http_methods(["GET", "POST"])
def customer_profile(request):
    user = request.user

    if user.role != User.Role.CUSTOMER:
        messages.error(
            request,
            "هذه الصفحة مخصصة لحسابات العملاء فقط.",
        )
        return redirect("accounts:dashboard")

    from .models import CustomerProfile

    profile, _ = CustomerProfile.objects.get_or_create(
        user=user,
    )

    if request.method == "POST":
        form = CustomerProfileForm(
            request.POST,
            user=user,
        )
        if form.is_valid():
            form.save()
            messages.success(
                request,
                "تم حفظ بيانات حسابك بنجاح.",
            )
            return redirect("accounts:customer_profile")
    else:
        form = CustomerProfileForm(
            user=user,
            initial={
                "first_name": user.first_name,
                "last_name": user.last_name,
                "email": user.email,
                "phone_number": profile.phone_number,
            },
        )

    return render(
        request,
        "accounts/profile.html",
        {
            "form": form,
            "profile": profile,
        },
    )


@login_required
@require_POST
def set_operational_status(request):
    user = request.user

    if user.role != User.Role.CAPTAIN:
        if request.headers.get("Accept") == "application/json":
            return JsonResponse(
                {"detail": "Only captain accounts can change operational status."},
                status=403,
            )
        messages.error(
            request,
            "Only captain accounts can change operational status.",
        )
        return redirect("accounts:dashboard")

    try:
        captain = user.captain_profile
    except CaptainProfile.DoesNotExist:
        if request.headers.get("Accept") == "application/json":
            return JsonResponse(
                {"detail": "Captain profile not found."},
                status=404,
            )
        messages.error(request, "Captain profile not found.")
        return redirect("accounts:dashboard")

    if request.content_type == "application/json":
        try:
            payload = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            payload = {}
    else:
        payload = request.POST

    requested_status = payload.get("status")

    if requested_status not in ("ONLINE", "OFFLINE"):
        if request.headers.get("Accept") == "application/json":
            return JsonResponse(
                {"detail": "Invalid operational status."},
                status=400,
            )
        messages.error(request, "Invalid operational status.")
        return redirect("accounts:dashboard")

    try:
        set_captain_online_status(
            captain,
            is_online=(requested_status == "ONLINE"),
        )
    except ValidationError as exc:
        if request.headers.get("Accept") == "application/json":
            return JsonResponse(
                {"detail": " ".join(exc.messages)},
                status=400,
            )
        for error in exc.messages:
            messages.error(request, error)
    else:
        if request.headers.get("Accept") == "application/json":
            return JsonResponse(
                {"message": f"Your status is now {requested_status}."},
                status=200,
            )
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

@login_required
@require_http_methods(["GET", "POST"])
def request_cash_topup(request):
    if request.user.role != User.Role.CAPTAIN:
        messages.error(request, "Only captains can request wallet deposits.")
        return redirect("accounts:dashboard")

    try:
        captain = request.user.captain_profile
    except CaptainProfile.DoesNotExist:
        messages.error(request, "Captain profile not found.")
        return redirect("accounts:dashboard")

    if request.method == "POST":
        form = CashWalletTopUpForm(request.POST)

        if form.is_valid():
            reference = f"CASH-{get_random_string(24).upper()}"

            WalletTopUpRequest.objects.create(
                captain=captain,
                amount=form.cleaned_data["amount"],
                method=WalletTopUpRequest.Method.CASH,
                status=WalletTopUpRequest.Status.PENDING,
                reference=reference,
            )

            messages.success(
                request,
                f"Deposit request submitted. Reference: {reference}. "
                "Your wallet will be credited after admin approval.",
            )
            return redirect("accounts:dashboard")
    else:
        form = CashWalletTopUpForm()

    return render(
        request,
        "accounts/cash_topup_request.html",
        {"form": form},
    )




@login_required
@require_http_methods(["GET"])
def captain_wallet(request):
    user = request.user

    if user.role != User.Role.CAPTAIN:
        messages.error(
            request,
            "This page is only available for captain accounts.",
        )
        return redirect("accounts:dashboard")

    try:
        captain = user.captain_profile
    except CaptainProfile.DoesNotExist:
        messages.error(request, "Captain profile not found.")
        return redirect("accounts:dashboard")

    from django.db.models import Sum

    from trips.models import (
        FinancialTransaction,
        TripFinancialRecord,
    )

    transaction_labels = {
        FinancialTransaction.TransactionType.WALLET_TOPUP: "شحن محفظة",
        FinancialTransaction.TransactionType.COMMISSION_DEDUCTION: "عمولة رحلة",
        FinancialTransaction.TransactionType.COMMISSION_DUE: "عمولة مستحقة",
        FinancialTransaction.TransactionType.COMMISSION_PAYMENT: "سداد عمولة",
        FinancialTransaction.TransactionType.CAPTAIN_PAYOUT: "تحويل أرباح",
        FinancialTransaction.TransactionType.COMMISSION_RESERVE: "حجز عمولة",
        FinancialTransaction.TransactionType.COMMISSION_RELEASE: "تحرير حجز عمولة",
    }

    topup_status_labels = {
        WalletTopUpRequest.Status.PENDING: "قيد الانتظار",
        WalletTopUpRequest.Status.COMPLETED: "مكتمل",
        WalletTopUpRequest.Status.REJECTED: "مرفوض",
    }

    topup_method_labels = {
        WalletTopUpRequest.Method.PAYMOB: "دفع إلكتروني",
        WalletTopUpRequest.Method.CASH: "إيداع نقدي",
    }

    wallet = (
        CaptainWallet.objects
        .filter(captain=captain)
        .first()
    )

    financial_totals = (
        TripFinancialRecord.objects
        .filter(trip__captain=captain)
        .aggregate(
            total_gross_fare=Sum("gross_fare"),
            total_commission=Sum("commission_amount"),
            total_captain_earnings=Sum("captain_earnings"),
        )
    )

    transaction_rows = (
        FinancialTransaction.objects
        .filter(captain=captain)
        .select_related("financial_record", "financial_record__trip")
        .order_by("-created_at", "-pk")[:100]
    )

    transactions = [
        {
            "type_label": transaction_labels.get(
                transaction.transaction_type,
                transaction.get_transaction_type_display(),
            ),
            "trip_id": (
                transaction.financial_record.trip_id
                if transaction.financial_record_id
                else None
            ),
            "description": transaction.description,
            "amount": transaction.amount,
            "created_at": transaction.created_at,
        }
        for transaction in transaction_rows
    ]

    topup_rows = (
        WalletTopUpRequest.objects
        .filter(captain=captain)
        .order_by("-created_at", "-pk")
    )

    topups = [
        {
            "method_label": topup_method_labels.get(
                topup.method, topup.get_method_display()
            ),
            "status_label": topup_status_labels.get(
                topup.status, topup.get_status_display()
            ),
            "status": topup.status,
            "reference": topup.reference,
            "amount": topup.amount,
            "created_at": topup.created_at,
        }
        for topup in topup_rows
    ]

    return render(
        request,
        "accounts/wallet.html",
        {
            "captain": captain,
            "wallet": wallet,
            "financial_totals": financial_totals,
            "transactions": transactions,
            "topups": topups,
        },
    )


@login_required
def notification_page(request):
    notifications = get_user_notifications(
        user=request.user,
        unread_only=False,
        limit=100,
    )
    unread_count = get_unread_notification_count(user=request.user)

    return render(
        request,
        "accounts/notifications.html",
        {
            "notifications": notifications,
            "unread_count": unread_count,
        },
    )


def social_login_cancelled(request):
    request.session.pop("google_signup_role", None)
    return redirect("accounts:login")


def social_login_error(request):
    request.session.pop("google_signup_role", None)
    messages.error(
        request,
        "Google sign-in could not be completed. Please try again.",
    )
    return redirect("accounts:login")






@login_required
def customer_trips(request):
    if request.user.role != User.Role.CUSTOMER:
        messages.error(
            request,
            "هذه الصفحة مخصصة لحسابات العملاء فقط.",
        )
        return redirect("accounts:dashboard")

    from trips.models import Trip, CaptainOffer

    trips = list(
        Trip.objects
        .filter(customer__user=request.user)
        .select_related("captain__user", "vehicle")
        .order_by("-requested_at")
    )

    trip_ids = [trip.pk for trip in trips]

    offers = (
        CaptainOffer.objects
        .filter(trip_id__in=trip_ids)
        .select_related("captain__user")
        .order_by("amount", "-created_at")
    )

    offers_by_trip = {}

    for offer in offers:
        offers_by_trip.setdefault(offer.trip_id, []).append(offer)

    for trip in trips:
        trip.customer_offers = offers_by_trip.get(trip.pk, [])

    return render(
        request,
        "accounts/my_trips.html",
        {"trips": trips},
    )
