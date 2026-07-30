from core.api import TenantScopedViewSet

from .models import Brand, Category, Product, ProductVariation, Unit
from .serializers import (
    BrandSerializer,
    CategorySerializer,
    ProductSerializer,
    ProductVariationSerializer,
    UnitSerializer,
)


class CategoryViewSet(TenantScopedViewSet):
    serializer_class = CategorySerializer
    required_perm = "manage_products"

    def get_queryset(self):
        return Category.objects.all()


class BrandViewSet(TenantScopedViewSet):
    serializer_class = BrandSerializer
    required_perm = "manage_products"

    def get_queryset(self):
        return Brand.objects.all()


class UnitViewSet(TenantScopedViewSet):
    serializer_class = UnitSerializer
    required_perm = "manage_products"

    def get_queryset(self):
        return Unit.objects.all()


class ProductViewSet(TenantScopedViewSet):
    serializer_class = ProductSerializer
    required_perm = "manage_products"

    def get_queryset(self):
        qs = Product.objects.select_related("category", "brand", "unit").prefetch_related("variations")
        params = self.request.query_params
        if params.get("low_stock") in {"1", "true"}:
            from django.db.models import F
            qs = qs.filter(track_inventory=True, current_stock__lte=F("reorder_level"))
        if search := params.get("search"):
            from django.db.models import Q
            qs = qs.filter(Q(name__icontains=search) | Q(sku__icontains=search) | Q(barcode__icontains=search))
        return qs


class ProductVariationViewSet(TenantScopedViewSet):
    serializer_class = ProductVariationSerializer
    required_perm = "manage_products"

    def get_queryset(self):
        return ProductVariation.objects.select_related("product")
