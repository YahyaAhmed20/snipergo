from io import BytesIO
from pathlib import Path
from django.contrib.auth.forms import AuthenticationForm
from django import forms
from django.contrib.auth.forms import UserCreationForm
from PIL import Image

from .models import User, CaptainDocument


class CustomLoginForm(AuthenticationForm):
    username = forms.CharField(
        label="اسم المستخدم",
        widget=forms.TextInput(attrs={
            "autofocus": True,
            "placeholder": "أدخل اسم المستخدم",
        }),
    )

    password = forms.CharField(
        label="كلمة المرور",
        strip=False,
        widget=forms.PasswordInput(attrs={
            "placeholder": "أدخل كلمة المرور",
        }),
    )


class BaseRegistrationForm(UserCreationForm):
    email = forms.EmailField(label="البريد الإلكتروني")
    phone_number = forms.CharField(
        label="رقم الهاتف",
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
        labels = {
            "username": "اسم المستخدم",
            "first_name": "الاسم الأول",
            "last_name": "اسم العائلة",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["password1"].label = "كلمة المرور"
        self.fields["password2"].label = "تأكيد كلمة المرور"

        self.fields["password1"].help_text = (
            "يجب أن تحتوي كلمة المرور على 8 أحرف على الأقل، "
            "وألا تكون شائعة أو رقمية بالكامل أو مشابهة لبياناتك الشخصية."
        )

        self.fields["password2"].help_text = (
            "أدخل كلمة المرور نفسها مرة أخرى للتأكيد."
        )

        self.fields["username"].help_text = (
            "150 حرفًا كحد أقصى. يُسمح بالحروف والأرقام "
            "والرموز @ / . + - _"
        )

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()

        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError(
                "هذا البريد الإلكتروني مسجل بالفعل."
            )

        return email






class CustomerProfileForm(forms.Form):
    first_name = forms.CharField(
        label="الاسم الأول",
        max_length=150,
        required=True,
    )
    last_name = forms.CharField(
        label="اسم العائلة",
        max_length=150,
        required=True,
    )
    email = forms.EmailField(
        label="البريد الإلكتروني",
        required=True,
    )
    phone_number = forms.CharField(
        label="رقم الهاتف",
        max_length=20,
        min_length=8,
        required=True,
    )

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        existing = User.objects.filter(
            email__iexact=email
        ).exclude(pk=self.user.pk)

        if existing.exists():
            raise forms.ValidationError(
                "البريد الإلكتروني مستخدم بالفعل."
            )

        return email

    def save(self):
        from .models import CustomerProfile

        user = self.user
        user.first_name = self.cleaned_data["first_name"]
        user.last_name = self.cleaned_data["last_name"]
        user.email = self.cleaned_data["email"]
        user.save(update_fields=[
            "first_name",
            "last_name",
            "email",
        ])

        profile, _ = CustomerProfile.objects.get_or_create(
            user=user,
        )
        profile.phone_number = self.cleaned_data["phone_number"]
        profile.save(update_fields=["phone_number", "updated_at"])

        return profile


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


# ─────────────────────────────────────────────────────────────
# Cash Wallet Top-Up Form
# ─────────────────────────────────────────────────────────────
class CashWalletTopUpForm(forms.Form):
    amount = forms.DecimalField(
        label="المبلغ (جنيه مصري)",
        min_value=1,
        max_digits=10,
        decimal_places=2,
        widget=forms.NumberInput(
            attrs={
                "min": "1.00",
                "step": "0.01",
                "placeholder": "أدخل المبلغ بالجنيه المصري",
            }
        ),
    )