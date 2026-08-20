import pytest


@pytest.fixture(autouse=True)
def _plain_static_storage(settings):
    """Tests never depend on collectstatic: use the plain staticfiles backend
    regardless of the DEBUG/deployment storage configuration."""
    settings.STORAGES = {
        **settings.STORAGES,
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
