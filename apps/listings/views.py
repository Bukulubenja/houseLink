"""Public listing pages. Only bookable listings are shown; contact details and directions
never appear here (the assigned broker gets them once a viewing is paid)."""

from django.core.paginator import Paginator
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_GET

from . import selectors
from .forms import SearchForm
from .models import Property

PAGE_SIZE = 12


@require_GET
def property_list(request: HttpRequest) -> HttpResponse:
    form = SearchForm(request.GET or None)
    filters = selectors.SearchFilters()
    if form.is_bound and form.is_valid():
        data = form.cleaned_data
        filters = selectors.SearchFilters(
            area=data["area"],
            min_rent=data["min_rent"],
            max_rent=data["max_rent"],
            bedrooms=data["bedrooms"],
            kind=data["kind"],
        )
    page = Paginator(selectors.search(filters), PAGE_SIZE).get_page(request.GET.get("page"))
    query = request.GET.copy()
    query.pop("page", None)
    return render(
        request,
        "listings/list.html",
        {"form": form, "page": page, "query": query.urlencode()},
    )


@require_GET
def property_detail(request: HttpRequest, pk: str) -> HttpResponse:
    prop = get_object_or_404(Property.objects.prefetch_related("photos"), pk=pk)
    if not prop.is_bookable:
        raise Http404
    return render(
        request,
        "listings/detail.html",
        {"property": prop, "verification": prop.latest_approved_verification},
    )
