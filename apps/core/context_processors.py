from django.http import HttpRequest

from apps.core.roles import roles_of


def navigation(request: HttpRequest) -> dict[str, bool]:
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {"nav_is_broker": False, "nav_is_ops": False}
    broker = getattr(user, "broker_profile", None)
    return {
        "nav_is_broker": broker is not None and broker.is_active,
        "nav_is_ops": bool(roles_of(user)),
    }
