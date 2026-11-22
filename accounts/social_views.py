
from allauth.socialaccount import app_settings
from allauth.socialaccount.views import SignupView as DefaultSocialSignupView


class SniperSocialSignupView(DefaultSocialSignupView):
    def get_form_class(self):
        from accounts.social_forms import CaptainSocialSignupForm

        if self.request.session.get("google_signup_role") == "CAPTAIN":
            return CaptainSocialSignupForm

        return super().get_form_class()
