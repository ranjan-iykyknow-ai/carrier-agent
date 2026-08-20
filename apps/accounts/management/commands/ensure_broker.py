"""Idempotent local broker login: replaces manual createsuperuser for the demo.

Creates (or promotes) the demo broker account with staff and superuser access so
one login works for both the product and Django admin. The generated password is
written to a git-ignored file, never printed; an existing password is preserved
unless --rotate is passed.
"""

import secrets
from pathlib import Path

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand

DEFAULT_EMAIL = "broker@goodlanelogistics.com"
DEFAULT_PASSWORD_FILE = "var/dev-password.txt"


class Command(BaseCommand):
    help = "Ensure the demo broker login exists with product and admin access."

    def add_arguments(self, parser):
        parser.add_argument("--email", default=DEFAULT_EMAIL)
        parser.add_argument("--password-file", default=DEFAULT_PASSWORD_FILE)
        parser.add_argument(
            "--rotate",
            action="store_true",
            help="Issue a fresh password even when the account already exists.",
        )

    def handle(self, *, email, password_file, rotate, **options):
        path = Path(password_file)
        user, created = User.objects.get_or_create(
            username=email, defaults={"email": email}
        )
        changed = []
        if created or rotate:
            password = secrets.token_urlsafe(12)
            user.set_password(password)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(password + "\n")
            changed.append("password written to " + str(path))
        if not user.email:
            user.email = email
        if not (user.is_staff and user.is_superuser):
            user.is_staff = True
            user.is_superuser = True
            changed.append("promoted to staff + superuser")
        user.save()
        state = "created" if created else "exists"
        detail = f" ({'; '.join(changed)})" if changed else ""
        self.stdout.write(f"Broker login {email}: {state}{detail}")
