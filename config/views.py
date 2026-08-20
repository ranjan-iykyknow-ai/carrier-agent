from django.db import connection
from django.http import JsonResponse


def healthz(request):
    """Liveness/readiness probe: verifies the database connection only."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
    return JsonResponse({"status": "ok"})
