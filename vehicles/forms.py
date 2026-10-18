
from django import forms

from .models import Vehicle


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
