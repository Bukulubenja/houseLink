import hashlib
import json

from django.db import models
from django.utils import timezone

from apps.accounts.models import User

from .models import AuditEvent


def canonical_hash(payload: dict) -> str:
    """Stable SHA-256 of a JSON-able dict (sorted keys, no whitespace)."""
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


def record(
    *,
    actor: User | None,
    action: str,
    target: models.Model,
    evidence: dict | None = None,
) -> AuditEvent:
    """Write one audit event. Call inside the same transaction as the change it describes."""
    evidence = dict(evidence or {})
    created_at = timezone.now()
    target_type = target._meta.label_lower
    target_id = str(target.pk)
    evidence_hash = canonical_hash(
        {
            "actor": str(actor.pk) if actor else None,
            "action": action,
            "target_type": target_type,
            "target_id": target_id,
            "evidence": evidence,
            "created_at": created_at.isoformat(),
        }
    )
    return AuditEvent.objects.create(
        actor=actor,
        action=action,
        target_type=target_type,
        target_id=target_id,
        evidence=evidence,
        evidence_hash=evidence_hash,
        created_at=created_at,
    )


def verify(event: AuditEvent) -> bool:
    """True if the stored hash still matches the stored content."""
    expected = canonical_hash(
        {
            "actor": str(event.actor_id) if event.actor_id else None,
            "action": event.action,
            "target_type": event.target_type,
            "target_id": event.target_id,
            "evidence": event.evidence,
            "created_at": event.created_at.isoformat(),
        }
    )
    return expected == event.evidence_hash
