from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse


class ForcePasswordChangeMiddleware:
    """Redirects any request from a logged-in user with
    must_change_password=True to the password-change form, except for the
    form itself, logout, and static/media. See docs/DECISIONS.md — every
    seeded/admin-issued account starts with this flag set.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def _exempt_paths(self) -> set[str]:
        return {
            reverse("accounts:password_change"),
            reverse("accounts:logout"),
            settings.STATIC_URL,
            settings.MEDIA_URL,
        }

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user and user.is_authenticated and getattr(user, "must_change_password", False):
            exempt = self._exempt_paths()
            if not any(request.path.startswith(p) for p in exempt):
                return redirect(reverse("accounts:password_change"))
        return self.get_response(request)
