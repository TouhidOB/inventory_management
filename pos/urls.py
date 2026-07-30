from django.urls import path

from .views import BarcodeLookupView, CheckoutView

urlpatterns = [
    path("lookup/", BarcodeLookupView.as_view(), name="pos-lookup"),
    path("checkout/", CheckoutView.as_view(), name="pos-checkout"),
]
