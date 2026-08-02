from django.urls import path

from pinforge_web import views

urlpatterns = [
    path("signup/", views.signup, name="signup"),
    path("", views.dashboard, name="dashboard"),
]
