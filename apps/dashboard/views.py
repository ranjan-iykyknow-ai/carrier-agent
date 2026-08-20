from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from apps.dashboard.selectors import dashboard_data


@login_required
def dashboard(request):
    return render(request, "dashboard/dashboard.html", dashboard_data())
