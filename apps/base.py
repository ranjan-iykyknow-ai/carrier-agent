"""Abstract model bases implementing the shared database conventions (spec §2)."""

import uuid

from django.db import models


class UUIDModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class TimeStampedModel(UUIDModel):
    """Mutable business record: UUID key plus created/updated timestamps."""

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class AppendOnlyModel(UUIDModel):
    """Append-only record: UUID key plus creation timestamp only.

    Append-only is a convention, not a database rule. The sanctioned mutable
    columns are supersession pointers such as ``is_current`` and resolution
    markers such as ``resolved_at``; everything else on these rows is written
    once and never updated.
    """

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True
