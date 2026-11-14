from django.apps import AppConfig
from django.db.models.signals import post_migrate


class CoreConfig(AppConfig):
    name = "apps.core"
    label = "core"

    def ready(self) -> None:
        from .roles import ensure_role_groups

        post_migrate.connect(ensure_role_groups, sender=self, dispatch_uid="core.role_groups")
