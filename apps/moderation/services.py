from django.db.models import Q

from apps.accounts.models import User
from apps.audit.services import record

from .models import BlockedIdentity


def is_blocked(*, nin_hash: str | None = None, phone: str | None = None) -> bool:
    query = Q()
    if nin_hash:
        query |= Q(kind=BlockedIdentity.Kind.NIN_HASH, value=nin_hash)
    if phone:
        query |= Q(kind=BlockedIdentity.Kind.PHONE, value=phone)
    return bool(query) and BlockedIdentity.objects.filter(query).exists()


def block(*, kind: str, value: str, reason: str, actor: User | None) -> BlockedIdentity:
    """Block an identifier (idempotent) and record who did it and why."""
    blocked, created = BlockedIdentity.objects.get_or_create(
        kind=kind, value=value, defaults={"reason": reason, "blocked_by": actor}
    )
    if created:
        record(
            actor=actor,
            action="moderation.identity_blocked",
            target=blocked,
            evidence={"kind": kind, "reason": reason},
        )
    return blocked
