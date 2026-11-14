from collections.abc import Callable
from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.http import Http404, HttpRequest, HttpResponse
from django.urls import reverse


def superuser_required(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
    """Super admin only. Anonymous users go to the password login; others get a 404 so the
    page's existence is not revealed."""

    @wraps(view)
    def wrapped(request: HttpRequest, *args: object, **kwargs: object) -> HttpResponse:
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path(), reverse("admin:login"))
        if not request.user.is_superuser:
            raise Http404
        return view(request, *args, **kwargs)

    return wrapped
