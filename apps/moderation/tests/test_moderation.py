import pytest

from apps.audit.models import AuditEvent
from apps.moderation.models import BlockedIdentity
from apps.moderation.services import block, is_blocked

pytestmark = pytest.mark.django_db

Kind = BlockedIdentity.Kind


def test_nothing_is_blocked_by_default():
    assert not is_blocked(nin_hash="abc", phone="+256772123456")
    assert not is_blocked()


def test_block_is_idempotent_and_audited_once():
    first = block(kind=Kind.PHONE, value="+256772123456", reason="fraud", actor=None)
    second = block(kind=Kind.PHONE, value="+256772123456", reason="again", actor=None)
    assert first == second
    assert AuditEvent.objects.filter(action="moderation.identity_blocked").count() == 1
    assert is_blocked(phone="+256772123456")
    assert not is_blocked(nin_hash="+256772123456")  # kinds don't cross-match


def test_str_hides_most_of_a_nin_hash():
    blocked = BlockedIdentity(kind=Kind.NIN_HASH, value="a" * 64)
    assert str(blocked).endswith("aaaaaaaa…")
    assert "+256" in str(BlockedIdentity(kind=Kind.PHONE, value="+256772123456"))
