from rest_framework.routers import DefaultRouter

from .views import StockMovementViewSet

router = DefaultRouter()
router.register("stock-movements", StockMovementViewSet, basename="stock-movement")

urlpatterns = router.urls
