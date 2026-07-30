from django.urls import path

from .views import ReportCatalogView, ReportExportView

urlpatterns = [
    path("", ReportCatalogView.as_view(), name="report-catalog"),
    path("export/", ReportExportView.as_view(), name="report-export"),
]
