from django.contrib import admin
from django.urls import include, path

from pinforge_web import views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/live", views.health_live, name="health-live"),
    path("health/ready", views.health_ready, name="health-ready"),
    path("accounts/", include("django.contrib.auth.urls")),
    path("", include("pinforge_web.urls")),
]
