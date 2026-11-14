from django.core.files.storage import Storage, storages


def private_storage() -> Storage:
    """Storage for ID scans and ownership proof. Never exposed by a public URL."""
    return storages["private"]
