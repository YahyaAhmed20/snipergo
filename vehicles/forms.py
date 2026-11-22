
from django import forms
from PIL import Image
from PIL.Image import DecompressionBombError

from .models import Vehicle

MAX_IMAGE_SIZE = 5 * 1024 * 1024  # 5 MB
ALLOWED_IMAGE_FORMATS = {"JPEG", "PNG"}
MIN_IMAGE_DIMENSION = 1
MAX_IMAGE_DIMENSION = 4096


def _validate_vehicle_image(uploaded_file):
    if not uploaded_file:
        return uploaded_file

    if uploaded_file.size == 0:
        raise forms.ValidationError("The uploaded image is empty.")

    if uploaded_file.size > MAX_IMAGE_SIZE:
        raise forms.ValidationError(
            "Image size must not exceed 5 MB."
        )

    uploaded_file.seek(0)

    try:
        with Image.open(uploaded_file) as image:
            image.verify()

        uploaded_file.seek(0)
        with Image.open(uploaded_file) as image:
            actual_format = image.format
            width, height = image.size
    except DecompressionBombError:
        raise forms.ValidationError("The image is too large to process.")
    except forms.ValidationError:
        raise
    except Exception as exc:
        raise forms.ValidationError(
            "The uploaded image is invalid or corrupted."
        ) from exc

    if actual_format not in ALLOWED_IMAGE_FORMATS:
        raise forms.ValidationError(
            "Only JPEG and PNG images are allowed."
        )

    if width < MIN_IMAGE_DIMENSION or height < MIN_IMAGE_DIMENSION:
        raise forms.ValidationError(
            f"Image dimensions must be at least {MIN_IMAGE_DIMENSION}x{MIN_IMAGE_DIMENSION} pixels."
        )

    if width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION:
        raise forms.ValidationError(
            f"Image dimensions must not exceed {MAX_IMAGE_DIMENSION}x{MAX_IMAGE_DIMENSION} pixels."
        )

    uploaded_file.seek(0)
    return uploaded_file


class VehicleRegistrationForm(forms.ModelForm):
    class Meta:
        model = Vehicle
        fields = (
            "vehicle_type",
            "brand",
            "model_name",
            "manufacture_year",
            "plate_number",
            "chassis_number",
            "front_photo",
            "rear_photo",
            "side_photo",
            "plate_photo",
        )
        labels = {
            "vehicle_type": "نوع المركبة",
            "brand": "الشركة المصنعة",
            "model_name": "موديل المركبة",
            "manufacture_year": "سنة الصنع",
            "plate_number": "رقم اللوحة",
            "chassis_number": "رقم الشاسيه",
            "front_photo": "صورة المركبة من الأمام",
            "rear_photo": "صورة المركبة من الخلف",
            "side_photo": "صورة المركبة من الجانب",
            "plate_photo": "صورة لوحة المركبة",
        }
        help_texts = {
            "plate_number": "أدخل رقم اللوحة كما هو مسجل رسميًا.",
            "chassis_number": "أدخل رقم الشاسيه الموجود على المركبة.",
            "front_photo": "صورة JPEG أو PNG، بحد أقصى 5 ميجابايت.",
            "rear_photo": "صورة JPEG أو PNG، بحد أقصى 5 ميجابايت.",
            "side_photo": "صورة JPEG أو PNG، بحد أقصى 5 ميجابايت.",
            "plate_photo": "صورة JPEG أو PNG، بحد أقصى 5 ميجابايت.",
        }
        widgets = {
            "vehicle_type": forms.Select(),
            "manufacture_year": forms.NumberInput(
                attrs={"min": 1950}
            ),
        }

    def clean_plate_number(self):
        return self.cleaned_data["plate_number"].strip().upper()

    def clean_chassis_number(self):
        return self.cleaned_data["chassis_number"].strip().upper()

    def clean_front_photo(self):
        return _validate_vehicle_image(self.cleaned_data.get("front_photo"))

    def clean_rear_photo(self):
        return _validate_vehicle_image(self.cleaned_data.get("rear_photo"))

    def clean_side_photo(self):
        return _validate_vehicle_image(self.cleaned_data.get("side_photo"))

    def clean_plate_photo(self):
        return _validate_vehicle_image(self.cleaned_data.get("plate_photo"))
