from django.urls import path
from rest_framework.routers import DefaultRouter
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from .rbac_api import MyPermissionsView, PermissionCatalogView, RoleViewSet
from .views import MeView, RegisterShopView

app_name = "accounts"


class ThrottledTokenObtainPairView(TokenObtainPairView):
    """JWT login, rate-limited per IP (scope 'auth') to blunt brute-force."""
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"


class ThrottledTokenRefreshView(TokenRefreshView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"


router = DefaultRouter()
router.register("roles", RoleViewSet, basename="role")

urlpatterns = [
    path("auth/register/", RegisterShopView.as_view(), name="register"),
    path("auth/token/", ThrottledTokenObtainPairView.as_view(), name="token_obtain"),
    path("auth/token/refresh/", ThrottledTokenRefreshView.as_view(), name="token_refresh"),
    path("auth/me/", MeView.as_view(), name="me"),
    path("auth/my-permissions/", MyPermissionsView.as_view(), name="my-permissions"),
    path("rbac/permissions/", PermissionCatalogView.as_view(), name="permission-catalog"),
] + router.urls
