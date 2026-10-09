from io import BytesIO
from pathlib import Path

from django import forms
from django.contrib.auth.forms import UserCreationForm
from PIL import Image

from .models import User, CaptainDocument


class BaseRegistrationForm(UserCreationForm):
    email = forms.EmailField(label="Email")
    phone_number = forms.CharField(
        label="Phone number",
        max_length=20,
        min_length=8,
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = (
            "username",
            "first_name",
            "last_name",
            "email",
        )

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()

        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(
                "This email is already registered."
            )

        return email


class CustomerRegistrationForm(BaseRegistrationForm):
    def save(self, commit=True):
        user = super().save(commit=False)
        user.role = User.Role.CUSTOMER

        if commit:
            user.email = self.cleaned_data["email"]
            user.save()
            from .models import CustomerProfile
            CustomerProfile.objects.create(
                user=user,
                phone_number=self.cleaned_data["phone_number"],
            )

        return user


class CaptainRegistrationForm(BaseRegistrationForm):
    def save(self, commit=True):
        user = super().save(commit=False)
        user.role = User.Role.CAPTAIN

        if commit:
            user.email = self.cleaned_data["email"]
            user.save()
            from .models import CaptainProfile
            CaptainProfile.objects.create(
                user=user,
                phone_number=self.cleaned_data["phone_number"],
            )

        return user


class CaptainDocumentForm(forms.ModelForm):
    MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB

    ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}

    class Meta:
        model = CaptainDocument
        fields = ("document_type", "file", "expires_at")
        widgets = {
            "expires_at": forms.DateInput(
                attrs={"type": "date"},
                format="%Y-%m-%d",
            ),
        }

    def clean_file(self):
        uploaded_file = self.cleaned_data.get("file")

        if not uploaded_file:
            return uploaded_file

        extension = Path(uploaded_file.name).suffix.lower()

        if extension not in self.ALLOWED_EXTENSIONS:
            raise forms.ValidationError(
                "Only PDF, JPG, JPEG, and PNG files are allowed."
            )

        if uploaded_file.size == 0:
            raise forms.ValidationError("The uploaded file is empty.")

        if uploaded_file.size > self.MAX_FILE_SIZE:
            raise forms.ValidationError(
                "File size must not exceed 10 MB."
            )

        uploaded_file.seek(0)

        if extension == ".pdf":
            header = uploaded_file.read(5)
            uploaded_file.seek(max(0, uploaded_file.size - 1024))
            trailer = uploaded_file.read()

            if header != b"%PDF-" or b"%%EOF" not in trailer:
                raise forms.ValidationError(
                    "The uploaded file is not a valid PDF document."
                )

        else:
            expected_formats = {
                ".jpg": "JPEG",
                ".jpeg": "JPEG",
                ".png": "PNG",
            }

            try:
                uploaded_file.seek(0)

                with Image.open(uploaded_file) as image:
                    actual_format = image.format
                    image.verify()

                if actual_format != expected_formats[extension]:
                    raise forms.ValidationError(
                        "The file content does not match its extension."
                    )

            except forms.ValidationError:
                raise
            except Exception as exc:
                raise forms.ValidationError(
                    "The uploaded image is invalid or corrupted."
                ) from exc

        uploaded_file.seek(0)
        return uploaded_file

    def clean(self):
        cleaned_data = super().clean()
        document_type = cleaned_data.get("document_type")
        expires_at = cleaned_data.get("expires_at")

        if document_type in {
            CaptainDocument.DocumentType.DRIVING_LICENSE,
            CaptainDocument.DocumentType.VEHICLE_LICENSE,
        }:
            if expires_at and expires_at < __import__("datetime").date.today():
                self.add_error(
                    "expires_at",
                    "Expiration date cannot be in the past.",
                )

        return cleaned_data