from django.shortcuts import render

# Create your views here.

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.views.decorators.http import require_http_methods

from accounts.models import User
from .forms import VehicleRegistrationForm
from .models import Vehicle


@login_required
@require_http_methods(["GET", "POST"])
def my_vehicles(request):
    if request.user.role != User.Role.CAPTAIN:
        messages.error(request, "Only captains can manage vehicles.")
        return redirect("accounts:dashboard")

    try:
        captain = request.user.captain_profile
    except Exception:
        messages.error(request, "Captain profile not found.")
        return redirect("accounts:dashboard")

    if request.method == "POST":
        form = VehicleRegistrationForm(request.POST, request.FILES)

        if form.is_valid():
            vehicle = form.save(commit=False)
            vehicle.captain = captain
            vehicle.approval_status = Vehicle.ApprovalStatus.PENDING
            vehicle.save()

            messages.success(
                request,
                "Vehicle registered successfully and is awaiting review.",
            )
            return redirect("vehicles:my_vehicles")
    else:
        form = VehicleRegistrationForm()

    vehicles = Vehicle.objects.filter(captain=captain).order_by("-created_at")

    return render(
        request,
        "vehicles/my_vehicles.html",
        {
            "form": form,
            "vehicles": vehicles,
        },
    )
