from unittest.mock import Mock

from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory, TestCase
from django.urls import reverse

from allauth.account.models import EmailAddress
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.models import SocialAccount, SocialLogin

from accounts.adapters import SniperSocialAccountAdapter
from accounts.models import CustomerProfile, CaptainProfile


User = get_user_model()


class SniperSocialAccountAdapterTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.request = self.factory.get("/accounts/google/login/")
        self.request.session = {}
        self.request._messages = FallbackStorage(self.request)
        self.adapter = SniperSocialAccountAdapter()
        
        
    
    def test_captain_google_signup_disables_auto_signup(self):
        self.request.session["google_signup_role"] = "CAPTAIN"

        sociallogin = self.make_sociallogin("captain-new@example.com")

        self.assertFalse(
            self.adapter.is_auto_signup_allowed(self.request, sociallogin)
        )

    def test_customer_google_signup_keeps_default_auto_signup_setting(self):
        sociallogin = self.make_sociallogin("customer-new@example.com")

        self.assertEqual(
            self.adapter.is_auto_signup_allowed(self.request, sociallogin),
            super(SniperSocialAccountAdapter, self.adapter).is_auto_signup_allowed(
                self.request, sociallogin
            ),
        )
        
        
        
    
    def test_social_signup_view_uses_captain_form_for_captain_intent(self):
        from accounts.social_views import SniperSocialSignupView
        from accounts.social_forms import CaptainSocialSignupForm

        view = SniperSocialSignupView()
        view.request = self.request
        self.request.session["google_signup_role"] = "CAPTAIN"

        self.assertIsInstance(
            view.get_form_class(),
            type(CaptainSocialSignupForm),
        )
        self.assertEqual(
            view.get_form_class(),
            CaptainSocialSignupForm,
        )

    def test_social_signup_view_keeps_default_form_for_customer(self):
        from accounts.social_views import SniperSocialSignupView
        from allauth.socialaccount.forms import SignupForm

        view = SniperSocialSignupView()
        view.request = self.request
        self.request.session.pop("google_signup_role", None)

        self.assertEqual(view.get_form_class(), SignupForm)



    def make_sociallogin(self, email, verified=True, existing=False):
        user = User(
            username="google_test_user",
            email=email,
            role=User.Role.CUSTOMER,
        )

        sociallogin = SocialLogin(
            user=user,
            account=SocialAccount(
                provider="google",
                uid=f"test-{email}",
                extra_data={},
            ),
            email_addresses=[
                EmailAddress(
                    email=email,
                    verified=verified,
                    primary=True,
                )
            ],
            provider=Mock(id="google"),
        )

        if existing:
            sociallogin.account = Mock()
            sociallogin.account.pk = 1
            sociallogin.account.user = user

        return sociallogin

    def test_rejects_unverified_google_email(self):
        sociallogin = self.make_sociallogin(
            "unverified@example.com",
            verified=False,
        )

        with self.assertRaises(ImmediateHttpResponse):
            self.adapter.pre_social_login(self.request, sociallogin)

        self.assertFalse(
            User.objects.filter(email__iexact="unverified@example.com").exists()
        )

    def test_rejects_email_already_used_by_local_account(self):
        existing_user = User.objects.create_user(
            username="existing_local_user",
            email="existing@example.com",
            password="TestPassword123!",
            role=User.Role.CAPTAIN,
        )
        CaptainProfile.objects.create(user=existing_user)

        sociallogin = self.make_sociallogin("existing@example.com")

        with self.assertRaises(ImmediateHttpResponse):
            self.adapter.pre_social_login(self.request, sociallogin)

        existing_user.refresh_from_db()
        self.assertEqual(existing_user.role, User.Role.CAPTAIN)
        self.assertEqual(User.objects.filter(email__iexact="existing@example.com").count(), 1)

    def test_existing_google_account_is_allowed_without_changing_role(self):
        captain = User.objects.create_user(
            username="linked_google_captain",
            email="captain@example.com",
            password="TestPassword123!",
            role=User.Role.CAPTAIN,
        )
        CaptainProfile.objects.create(user=captain)

        sociallogin = self.make_sociallogin(
            "captain@example.com",
            existing=True,
        )
        sociallogin.user = captain
        
        self.request.session["google_signup_role"] = "CAPTAIN"

        self.adapter.pre_social_login(self.request, sociallogin)

        captain.refresh_from_db()
        self.assertEqual(captain.role, User.Role.CAPTAIN)
        self.assertNotIn("google_signup_role", self.request.session)
        
        
    def test_new_google_signup_creates_customer_and_profile(self):
        sociallogin = self.make_sociallogin(
            "newcustomer@example.com",
            verified=True,
        )
        sociallogin.user.username = "new_google_customer"
        sociallogin.user.set_unusable_password()

        saved_user = self.adapter.save_user(
            self.request,
            sociallogin,
        )

        saved_user.refresh_from_db()

        self.assertEqual(saved_user.email, "newcustomer@example.com")
        self.assertEqual(saved_user.role, User.Role.CUSTOMER)
        self.assertTrue(
            CustomerProfile.objects.filter(user=saved_user).exists()
        )
        
        
        
        
        
    
    def test_new_google_signup_creates_captain_and_profile(self):
        from accounts.social_forms import CaptainSocialSignupForm

        self.request.session["google_signup_role"] = "CAPTAIN"

        sociallogin = self.make_sociallogin(
            "newcaptain@example.com",
            verified=True,
        )
        sociallogin.user.username = "new_google_captain"
        sociallogin.user.set_unusable_password()

        form = CaptainSocialSignupForm(
            data={
                "username": "new_google_captain",
                "email": "newcaptain@example.com",
                "phone_number": "01012345678",
            },
            sociallogin=sociallogin,
        )

        self.assertTrue(form.is_valid(), form.errors)

        saved_user = form.save(self.request)
        saved_user.refresh_from_db()

        self.assertEqual(saved_user.role, User.Role.CAPTAIN)
        self.assertEqual(saved_user.email, "newcaptain@example.com")

        captain_profile = CaptainProfile.objects.get(user=saved_user)
        self.assertEqual(captain_profile.phone_number, "01012345678")
        self.assertEqual(
            captain_profile.approval_status,
            CaptainProfile.ApprovalStatus.PENDING,
        )
        self.assertEqual(
            captain_profile.operational_status,
            CaptainProfile.OperationalStatus.OFFLINE,
        )

        self.assertFalse(
            CustomerProfile.objects.filter(user=saved_user).exists()
        )
        self.assertNotIn("google_signup_role", self.request.session)

    
    def test_captain_google_signup_requires_phone_before_saving(self):
        from accounts.social_forms import CaptainSocialSignupForm

        sociallogin = self.make_sociallogin(
            "newcaptain@example.com",
            verified=True,
        )
        sociallogin.user.username = "new_google_captain"
        sociallogin.user.set_unusable_password()

        form = CaptainSocialSignupForm(
            data={
                "username": "new_google_captain",
                "email": "newcaptain@example.com",
                "phone_number": "",
            },
            sociallogin=sociallogin,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("phone_number", form.errors)
        self.assertFalse(
            User.objects.filter(
                email__iexact="newcaptain@example.com"
            ).exists()
        )


    
    def test_social_login_cancelled_clears_captain_signup_intent(self):
        from accounts.views import social_login_cancelled

        self.request.session["google_signup_role"] = "CAPTAIN"

        response = social_login_cancelled(self.request)

        self.assertNotIn("google_signup_role", self.request.session)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("accounts:login"))

    def test_social_login_error_clears_captain_signup_intent(self):
        from accounts.views import social_login_error

        self.request.session["google_signup_role"] = "CAPTAIN"

        response = social_login_error(self.request)

        self.assertNotIn("google_signup_role", self.request.session)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("accounts:login"))
