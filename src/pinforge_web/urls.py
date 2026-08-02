from django.urls import path

from pinforge_web import views

urlpatterns = [
    path("signup/", views.signup, name="signup"),
    path("listings/", views.listing_list, name="listing-list"),
    path("listings/new/", views.listing_create, name="listing-create"),
    path(
        "listings/<uuid:listing_id>/",
        views.listing_detail,
        name="listing-detail",
    ),
    path("brand/", views.brand_kit, name="brand-kit"),
    path("", views.dashboard, name="dashboard"),
]
