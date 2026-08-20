from django.contrib import admin
from django.urls import path

from config.views import healthz

urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz", healthz),
]
