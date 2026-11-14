from django.conf import settings
from django.http import HttpRequest


def get_client_ip(request: HttpRequest) -> str:
    """Client IP, trusting X-Forwarded-For only for the NUM_PROXIES hops we control.

    Each proxy appends the address it received the request from, so the real client is
    NUM_PROXIES entries from the right. Anything further left is client-supplied and spoofable.
    """
    num_proxies = settings.NUM_PROXIES
    if num_proxies > 0:
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
        if len(hops) >= num_proxies:
            return hops[-num_proxies]
    return request.META.get("REMOTE_ADDR", "")
