"""Staff roles from the operating plan, held as Django groups.

Staff sign in with a password (SMS sign-in is refused for staff). A person may hold several
roles, but services enforce separation of duties per record: whoever sources or verifies a
listing cannot approve it, and whoever asks for a refund cannot pay it out.
Brokers are not staff; a `BrokerProfile` makes someone a broker.
"""

from collections.abc import Callable
from enum import StrEnum
from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpRequest, HttpResponse
from django.urls import reverse


class Role(StrEnum):
    ACQUISITION = "acquisition"  # sources properties and records the owner's authority
    VERIFICATION = "verification"  # inspects properties against the checklist
    APPROVAL = "listing_approval"  # approves verified listings for publication
    BOOKINGS = "bookings"  # confirms slots, assigns brokers, supports customers
    FINANCE = "finance"  # records payments, reconciles, pays refunds


ROLE_LABELS = {
    Role.ACQUISITION: "Property acquisition",
    Role.VERIFICATION: "Verification",
    Role.APPROVAL: "Listing approval",
    Role.BOOKINGS: "Bookings and support",
    Role.FINANCE: "Finance",
}


def has_role(user: object, role: Role) -> bool:
    if not getattr(user, "is_authenticated", False) or not user.is_active or not user.is_staff:
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(name=role.value).exists()


def roles_of(user: object) -> set[Role]:
    return {role for role in Role if has_role(user, role)}


def require_role(user: object, role: Role) -> None:
    if not has_role(user, role):
        raise PermissionDenied(f"This needs the {ROLE_LABELS[role]} role.")


def role_required(
    *roles: Role,
) -> Callable[[Callable[..., HttpResponse]], Callable[..., HttpResponse]]:
    """Any one of `roles`. Anonymous users go to the password login; others get a 404."""

    def decorator(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
        @wraps(view)
        def wrapped(request: HttpRequest, *args: object, **kwargs: object) -> HttpResponse:
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path(), reverse("admin:login"))
            if not any(has_role(request.user, role) for role in roles):
                raise Http404
            return view(request, *args, **kwargs)

        return wrapped

    return decorator


def ensure_role_groups(**_: object) -> None:
    """post_migrate hook: create one group per role (idempotent)."""
    from django.contrib.auth.models import Group

    for role in Role:
        Group.objects.get_or_create(name=role.value)
