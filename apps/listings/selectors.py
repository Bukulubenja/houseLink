"""Read-side queries for public pages. Search lives here so a later move to PostGIS or a
search index changes this module only."""

from dataclasses import dataclass

from django.db.models import Prefetch, QuerySet

from .models import Property, PropertyPhoto


@dataclass(frozen=True)
class SearchFilters:
    area: str = ""
    min_rent: int | None = None
    max_rent: int | None = None
    bedrooms: int | None = None
    kind: str = ""


def search(filters: SearchFilters) -> QuerySet[Property]:
    qs = Property.objects.bookable().prefetch_related(
        Prefetch("photos", queryset=PropertyPhoto.objects.order_by("position", "created_at"))
    )
    if filters.area:
        qs = qs.filter(area=filters.area)
    if filters.min_rent is not None:
        qs = qs.filter(rent_ugx__gte=filters.min_rent)
    if filters.max_rent is not None:
        qs = qs.filter(rent_ugx__lte=filters.max_rent)
    if filters.bedrooms is not None:
        qs = qs.filter(bedrooms__gte=filters.bedrooms)
    if filters.kind:
        qs = qs.filter(kind=filters.kind)
    return qs.order_by("-last_verified_at")
