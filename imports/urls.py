from django.urls import path

from . import views

app_name = "imports"

urlpatterns = [
    path("", views.index, name="index"),
    path("upload/", views.upload, name="upload"),
    path("<uuid:job_id>/map/", views.mapping, name="mapping"),
    path("<uuid:job_id>/preview/", views.preview, name="preview"),
    path("<uuid:job_id>/commit/", views.commit, name="commit"),
    path("<uuid:job_id>/", views.detail, name="detail"),
    path("<uuid:job_id>/report/", views.report, name="report"),
    path("<uuid:job_id>/rollback/", views.rollback, name="rollback"),
]
