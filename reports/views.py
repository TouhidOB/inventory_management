"""Report export endpoints (8.6). ?type=<report>&format=csv|excel|pdf."""
from django.utils.dateparse import parse_datetime
from rest_framework.response import Response
from rest_framework.views import APIView

from core.permissions import HasPermCode, IsTenantMember
from core.tenant_context import set_current_tenant

from .datasets import BUILDERS
from .exporters import export


class ReportExportView(APIView):
    permission_classes = [IsTenantMember, HasPermCode]
    required_perm = "view_reports"

    def initial(self, request, *args, **kwargs):
        set_current_tenant(getattr(request.user, "shop", None))
        request.tenant = getattr(request.user, "shop", None)
        super().initial(request, *args, **kwargs)

    def get(self, request):
        report_type = request.query_params.get("type")
        # NB: 'format' is reserved by DRF content negotiation, so use a
        # distinct param name for the export file format.
        fmt = request.query_params.get("export_format", "csv")
        builder = BUILDERS.get(report_type)
        if builder is None:
            return Response(
                {"detail": f"Unknown report type. Options: {sorted(BUILDERS)}"},
                status=400,
            )
        start = parse_datetime(request.query_params.get("start", "") or "")
        end = parse_datetime(request.query_params.get("end", "") or "")
        title, columns, rows = builder(request.user.shop, start=start, end=end)
        return export(fmt, title, columns, rows)


class ReportCatalogView(APIView):
    """List available report types."""

    permission_classes = [IsTenantMember]

    def get(self, request):
        return Response({"reports": sorted(BUILDERS), "formats": ["csv", "excel", "pdf"]})
