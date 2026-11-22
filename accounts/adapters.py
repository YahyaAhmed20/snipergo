from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db import transaction
from django.shortcuts import redirect

from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter

from .models import CaptainProfile, CustomerProfile


User = get_user_model()


class SniperSocialAccountAdapter(DefaultSocialAccountAdapter):
    def populate_user(self, request, sociallogin, data):
        user = super().populate_user(request, sociallogin, data)
        user.email = (user.email or "").strip().lower()
        user.role = User.Role.CUSTOMER
        return user

    def is_auto_signup_allowed(self, request, sociallogin):
        # Captain signup must stop at the signup form.
        if request.session.get("google_signup_role") == "CAPTAIN":
            return False

        return super().is_auto_signup_allowed(request, sociallogin)

    def pre_social_login(self, request, sociallogin):
        if sociallogin.is_existing:
            request.session.pop("google_signup_role", None)
            return

        email = (sociallogin.user.email or "").strip().lower()

        if not email:
            messages.error(
                request,
                "Google did not provide an email address. "
                "Please use another sign-in method.",
            )
            raise ImmediateHttpResponse(redirect("accounts:login"))

        verified_emails = {
            (address.email or "").strip().lower()
            for address in sociallogin.email_addresses
            if address.verified
        }

        if email not in verified_emails:
            messages.error(
                request,
                "Your Google email address could not be verified. "
                "Please use a verified Google account.",
            )
            raise ImmediateHttpResponse(redirect("accounts:login"))

        existing_user = User.objects.filter(email__iexact=email).first()

        if existing_user is not None:
            messages.error(
                request,
                "An account with this email already exists. "
                "Please sign in using your existing account.",
            )
            raise ImmediateHttpResponse(redirect("accounts:login"))

    @transaction.atomic
    def save_user(self, request, sociallogin, form=None):
        user = sociallogin.user
        user.email = (user.email or "").strip().lower()

        signup_role = request.session.get("google_signup_role")
        is_captain_signup = signup_role == "CAPTAIN"

        if is_captain_signup:
            if form is None:
                raise ValueError(
                    "Captain Google signup requires the signup form."
                )

            phone_number = (
                form.cleaned_data.get("phone_number")
                or form.cleaned_data.get("phone")
                or ""
            ).strip()

            if not phone_number:
                raise ValueError(
                    "A phone number is required for captain registration."
                )

            user.role = User.Role.CAPTAIN
        else:
            user.role = User.Role.CUSTOMER

        user = super().save_user(request, sociallogin, form=form)

        if is_captain_signup:
            CaptainProfile.objects.create(
                user=user,
                phone_number=phone_number,
                approval_status=CaptainProfile.ApprovalStatus.PENDING,
                operational_status=CaptainProfile.OperationalStatus.OFFLINE,
            )
            request.session.pop("google_signup_role", None)
        else:
            CustomerProfile.objects.get_or_create(user=user)

        return user