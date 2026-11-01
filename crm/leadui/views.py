"""Thin views for the CRM V2 lead pages: authorize, call selectors, render.

Business rules live in crm.leads / crm.followcycles / crm.intake; nothing here
decides who owns what. A user who may not use a page gets 404, not 403: the
sidebar hides it, and a 403 would confirm the route exists.
"""

from __future__ import annotations

from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.http import Http404
from django.shortcuts import render

from crm.core.permissions import can_use_lead_workspace


def lead_page(predicate):
    """Anonymous: redirect to login. Authenticated but failing `predicate`: 404."""

    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            if not predicate(request.user):
                raise Http404
            return view(request, *args, **kwargs)

        return wrapped

    return decorator


@lead_page(can_use_lead_workspace)
def my_work(request):
    return render(request, "leadui/my_work.html")


def _not_built(request, *args, **kwargs):
    raise Http404


global_unassigned = team_unassigned = stopped_review = _not_built
lead_detail = customer_detail = customer_history = _not_built
