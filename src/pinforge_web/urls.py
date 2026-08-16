from django.urls import path

from pinforge_web import views

urlpatterns = [
    path("signup/", views.signup, name="signup"),
    path("connections/etsy/", views.etsy_connection, name="etsy-connection"),
    path(
        "connections/etsy/start/",
        views.etsy_connection_start,
        name="etsy-connection-start",
    ),
    path(
        "connections/etsy/callback/",
        views.etsy_connection_callback,
        name="etsy-connection-callback",
    ),
    path(
        "connections/etsy/<uuid:connection_id>/disconnect/",
        views.etsy_connection_disconnect,
        name="etsy-connection-disconnect",
    ),
    path(
        "connections/etsy/<uuid:connection_id>/sync/",
        views.etsy_connection_sync,
        name="etsy-connection-sync",
    ),
    path(
        "connections/pinterest/",
        views.pinterest_connection,
        name="pinterest-connection",
    ),
    path(
        "connections/pinterest/start/",
        views.pinterest_connection_start,
        name="pinterest-connection-start",
    ),
    path(
        "connections/pinterest/callback/",
        views.pinterest_connection_callback,
        name="pinterest-connection-callback",
    ),
    path(
        "connections/pinterest/<uuid:connection_id>/sync/",
        views.pinterest_connection_sync,
        name="pinterest-connection-sync",
    ),
    path(
        "connections/pinterest/<uuid:connection_id>/disconnect/",
        views.pinterest_connection_disconnect,
        name="pinterest-connection-disconnect",
    ),
    path("listings/", views.listing_list, name="listing-list"),
    path("listings/new/", views.listing_create, name="listing-create"),
    path(
        "listings/<uuid:listing_id>/",
        views.listing_detail,
        name="listing-detail",
    ),
    path(
        "listings/<uuid:listing_id>/creatives/",
        views.creative_create,
        name="creative-create",
    ),
    path(
        "creatives/<uuid:creative_id>/",
        views.creative_detail,
        name="creative-detail",
    ),
    path(
        "creatives/<uuid:creative_id>/download/",
        views.creative_download,
        name="creative-download",
    ),
    path(
        "creatives/<uuid:creative_id>/publish/",
        views.creative_publish,
        name="creative-publish",
    ),
    path("brand/", views.brand_kit, name="brand-kit"),
    path("", views.dashboard, name="dashboard"),
]
