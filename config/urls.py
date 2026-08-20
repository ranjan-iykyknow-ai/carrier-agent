from django.contrib import admin
from django.urls import include, path

from apps.comms.views import inbox
from apps.dashboard.views import dashboard
from config.views import healthz

urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz", healthz),
    path("accounts/", include("apps.accounts.urls")),
    path("", dashboard, name="dashboard"),
    path("inbox/", inbox, name="inbox"),
]
