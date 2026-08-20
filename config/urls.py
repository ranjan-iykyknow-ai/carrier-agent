from django.contrib import admin
from django.urls import include, path

from apps.comms.views import call_audio, inbox
from apps.dashboard.views import dashboard
from apps.freight.views import load_workspace, loads_list
from apps.workspace.views import inquiry_action, inquiry_review
from config.views import healthz

urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz", healthz),
    path("accounts/", include("apps.accounts.urls")),
    path("", dashboard, name="dashboard"),
    path("inbox/", inbox, name="inbox"),
    path("loads/", loads_list, name="loads"),
    path("loads/<str:external_load_id>/", load_workspace, name="load_workspace"),
    path("inquiries/<uuid:pk>/", inquiry_review, name="inquiry_review"),
    path("inquiries/<uuid:pk>/actions/<slug:action>/", inquiry_action, name="inquiry_action"),
    path("calls/<uuid:pk>/audio", call_audio, name="call_audio"),
]
