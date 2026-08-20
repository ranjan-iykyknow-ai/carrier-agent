from django.contrib import admin
from django.urls import include, path

from apps.comms.lab_views import (
    audio_submit,
    email_preview,
    email_submit,
    job_detail,
    job_retry,
    job_status,
    lab,
)
from apps.comms.views import call_audio, inbox
from apps.dashboard.views import dashboard
from apps.freight.views import carrier_profile, load_workspace, loads_list
from apps.workspace.views import (
    assistant_page,
    assistant_send,
    draft_action,
    draft_generate,
    inquiry_action,
    inquiry_review,
)
from config.views import healthz

urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz", healthz),
    path("accounts/", include("apps.accounts.urls")),
    path("", dashboard, name="dashboard"),
    path("inbox/", inbox, name="inbox"),
    path("loads/", loads_list, name="loads"),
    path("loads/<str:external_load_id>/", load_workspace, name="load_workspace"),
    path("carriers/<uuid:pk>/", carrier_profile, name="carrier_profile"),
    path("inquiries/<uuid:pk>/", inquiry_review, name="inquiry_review"),
    path("inquiries/<uuid:pk>/actions/<slug:action>/", inquiry_action, name="inquiry_action"),
    path("inquiries/<uuid:pk>/drafts/", draft_generate, name="draft_generate"),
    path("drafts/<uuid:pk>/<slug:action>/", draft_action, name="draft_action"),
    path("assistant/", assistant_page, name="assistant"),
    path("assistant/send/", assistant_send, name="assistant_send"),
    path("calls/<uuid:pk>/audio", call_audio, name="call_audio"),
    path("manual-ingestion/", lab, name="lab"),
    path("manual-ingestion/email/preview/", email_preview, name="lab_email_preview"),
    path("manual-ingestion/email/submit/", email_submit, name="lab_email_submit"),
    path("manual-ingestion/audio/submit/", audio_submit, name="lab_audio_submit"),
    path("manual-ingestion/jobs/<uuid:pk>/", job_detail, name="lab_job"),
    path("manual-ingestion/jobs/<uuid:pk>/status/", job_status, name="lab_job_status"),
    path("manual-ingestion/jobs/<uuid:pk>/retry/", job_retry, name="lab_job_retry"),
]
