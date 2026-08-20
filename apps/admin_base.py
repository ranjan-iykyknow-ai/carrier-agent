"""Shared admin building blocks (spec 3J).

Django Admin is an authenticated inspection surface, never an alternate broker
workflow or database editor: every project model registers read-only, delete
included. Broker corrections stay in Inquiry Review so invariants, audit
actions, reconciliation, and reassessment remain intact.
"""

import json

from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

admin.site.site_header = "Goodlane Carrier Agent — Inspection"
admin.site.site_title = "Goodlane Inspection"
admin.site.index_title = "Read-only data inspection"


class ReadOnlyAdmin(admin.ModelAdmin):
    list_per_page = 50

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


def pretty_json(value):
    """Escaped, formatted JSON for detail pages (format_html escapes the dump)."""
    if value in (None, "", {}, []):
        return "—"
    return format_html(
        '<pre style="white-space:pre-wrap;max-width:70em">{}</pre>',
        json.dumps(value, indent=2, ensure_ascii=False, default=str),
    )


def change_link(obj, label=None):
    """Link to a related record's admin detail page."""
    if obj is None:
        return "—"
    url = reverse(f"admin:{obj._meta.app_label}_{obj._meta.model_name}_change", args=[obj.pk])
    return format_html('<a href="{}">{}</a>', url, label or str(obj))
