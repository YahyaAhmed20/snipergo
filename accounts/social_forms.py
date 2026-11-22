
from allauth.socialaccount.forms import SignupForm
from django import forms


class CaptainSocialSignupForm(SignupForm):
    phone_number = forms.CharField(
        label="Phone number",
        min_length=8,
        max_length=20,
        required=True,
        widget=forms.TextInput(
            attrs={
                "autocomplete": "tel",
                "placeholder": "Enter your phone number",
            }
        ),
    )

    def clean_phone_number(self):
        phone_number = self.cleaned_data["phone_number"].strip()

        if not phone_number:
            raise forms.ValidationError("Phone number is required.")

        return phone_number
