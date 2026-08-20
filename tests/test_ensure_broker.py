"""ensure_broker: durable local login bootstrap; no manual createsuperuser."""

from io import StringIO

import pytest
from django.contrib.auth.models import User
from django.core.management import call_command

pytestmark = pytest.mark.django_db


def run(*args, tmp_path):
    out = StringIO()
    call_command("ensure_broker", *args, "--password-file", str(tmp_path / "pw.txt"), stdout=out)
    return out.getvalue()


class TestEnsureBroker:
    def test_creates_admin_capable_broker_and_writes_password(self, tmp_path):
        run(tmp_path=tmp_path)

        user = User.objects.get(username="broker@goodlanelogistics.com")
        assert user.is_staff and user.is_superuser
        assert user.email == "broker@goodlanelogistics.com"
        password = (tmp_path / "pw.txt").read_text().strip()
        assert password
        assert user.check_password(password)

    def test_second_run_is_idempotent_and_keeps_password(self, tmp_path):
        run(tmp_path=tmp_path)
        first = (tmp_path / "pw.txt").read_text()

        output = run(tmp_path=tmp_path)

        assert User.objects.count() == 1
        assert (tmp_path / "pw.txt").read_text() == first
        assert "exists" in output

    def test_promotes_existing_plain_user(self, tmp_path):
        User.objects.create_user("broker@goodlanelogistics.com", password="old")

        run(tmp_path=tmp_path)

        user = User.objects.get(username="broker@goodlanelogistics.com")
        assert user.is_staff and user.is_superuser
        # An existing password is never silently rotated.
        assert user.check_password("old")

    def test_rotate_flag_issues_new_password(self, tmp_path):
        run(tmp_path=tmp_path)
        first = (tmp_path / "pw.txt").read_text().strip()

        run("--rotate", tmp_path=tmp_path)

        user = User.objects.get(username="broker@goodlanelogistics.com")
        second = (tmp_path / "pw.txt").read_text().strip()
        assert second != first
        assert user.check_password(second)


class TestEnvPassword:
    """BROKER_PASSWORD env var is authoritative when present (deploy flow)."""

    def test_env_password_used_on_create_and_no_file_written(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BROKER_PASSWORD", "chosen-by-operator")

        output = run(tmp_path=tmp_path)

        user = User.objects.get(username="broker@goodlanelogistics.com")
        assert user.check_password("chosen-by-operator")
        assert user.is_staff and user.is_superuser
        assert not (tmp_path / "pw.txt").exists()
        assert "environment" in output

    def test_env_password_realigns_existing_mismatch(self, tmp_path, monkeypatch):
        User.objects.create_user("broker@goodlanelogistics.com", password="old")
        monkeypatch.setenv("BROKER_PASSWORD", "rotated-in-dashboard")

        run(tmp_path=tmp_path)

        user = User.objects.get(username="broker@goodlanelogistics.com")
        assert user.check_password("rotated-in-dashboard")
        assert not user.check_password("old")

    def test_env_password_is_idempotent(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BROKER_PASSWORD", "steady")
        run(tmp_path=tmp_path)

        output = run(tmp_path=tmp_path)

        assert User.objects.count() == 1
        assert User.objects.get().check_password("steady")
        assert "up to date" in output

    def test_demo_user_password_alias_is_accepted(self, tmp_path, monkeypatch):
        monkeypatch.delenv("BROKER_PASSWORD", raising=False)
        monkeypatch.setenv("DEMO_USER_PASSWORD", "dashboard-choice")

        run(tmp_path=tmp_path)

        user = User.objects.get(username="broker@goodlanelogistics.com")
        assert user.check_password("dashboard-choice")
        assert not (tmp_path / "pw.txt").exists()
