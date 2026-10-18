
from django.urls import path

from . import views

app_name = "vehicles"

urlpatterns = [
    path("my/", views.my_vehicles, name="my_vehicles"),
]
