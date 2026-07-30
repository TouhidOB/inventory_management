"""
RBAC management API for Shop Owners (8.2).

Owners can view the permission catalog and edit which permissions each of their
shop's roles holds. Any change is audited. Non-owners are blocked by the
``manage_users`` permission.
"""
from rest_framework import serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.models import AuditLog
from audit.services import record
from core.permissions import HasPermCode, IsTenantMember
from core.tenant_context import set_current_tenant

from .models import Permission, Role


class PermissionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Permission
        fields = ["id", "code", "name", "category", "description"]


class RoleSerializer(serializers.ModelSerializer):
    permission_codes = serializers.SlugRelatedField(
        source="permissions", slug_field="code", many=True, read_only=True
    )

    class Meta:
        model = Role
        fields = ["id", "role_type", "name", "is_system", "permission_codes"]
        read_only_fields = ["role_type", "is_system"]


class _OwnerScoped:
    def initial(self, request, *args, **kwargs):
        set_current_tenant(getattr(request.user, "shop", None))
        request.tenant = getattr(request.user, "shop", None)
        super().initial(request, *args, **kwargs)


class PermissionCatalogView(_OwnerScoped, APIView):
    permission_classes = [IsTenantMember, HasPermCode]
    required_perm = "manage_users"

    def get(self, request):
        return Response(PermissionSerializer(Permission.objects.all(), many=True).data)


class MyPermissionsView(APIView):
    """Effective permission codes for the current user (drives UI hiding)."""

    permission_classes = [IsTenantMember]

    def get(self, request):
        user = request.user
        if user.role == "owner":
            codes = list(Permission.objects.values_list("code", flat=True))
        else:
            codes = list(
                Role.objects.filter(shop_id=user.shop_id, role_type=user.role)
                .values_list("permissions__code", flat=True)
            )
        return Response({"role": user.role, "permissions": [c for c in codes if c]})


class RoleViewSet(_OwnerScoped, viewsets.ModelViewSet):
    """Owner-managed role permission matrix, scoped to the owner's shop."""

    serializer_class = RoleSerializer
    permission_classes = [IsTenantMember, HasPermCode]
    required_perm = "manage_users"
    http_method_names = ["get", "post"]  # edit perms via set_permissions action

    def get_queryset(self):
        return (
            Role.objects.filter(shop_id=self.request.user.shop_id)
            .order_by("role_type")
            .prefetch_related("permissions")
        )

    @action(detail=True, methods=["post"])
    def set_permissions(self, request, pk=None):
        """Body: {"codes": ["view_sales", "create_sale", ...]}."""
        role = self.get_object()
        codes = request.data.get("codes", [])
        perms = list(Permission.objects.filter(code__in=codes))
        role.permissions.set(perms)
        record(
            action=AuditLog.Action.PERMISSION_CHANGE, actor=request.user,
            shop=request.user.shop, target=role,
            description=f"Set {role.role_type} permissions",
            changes={"codes": codes},
        )
        return Response(RoleSerializer(role).data)
