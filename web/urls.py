from django.urls import path

from . import views

app_name = "web"

urlpatterns = [
    # Public marketing pages (no auth)
    path("", views.public_home, name="home"),
    path("features/", views.public_features, name="features"),
    path("pricing/", views.public_pricing, name="pricing"),
    path("contact/", views.public_contact, name="contact"),
    path("signup/", views.public_signup, name="signup"),

    # Auth
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),

    # Shop app
    path("app/", views.dashboard, name="dashboard"),
    path("chart-data/", views.dashboard_chart_data, name="chart_data"),
    path("search/", views.universal_search, name="universal_search"),
    path("barcode/resolve/", views.barcode_resolve, name="barcode_resolve"),

    path("products/", views.products, name="products"),
    path("products/import/", views.product_import, name="product_import"),
    path("products/purchase/", views.purchase_scan, name="purchase_scan"),
    path("products/lookup/", views.item_lookup, name="item_lookup"),
    path("products/set-barcode/", views.set_barcode, name="set_barcode"),
    path("products/<int:pk>/", views.product_profile, name="product_profile"),
    path("products/<int:pk>/edit/", views.product_edit, name="product_edit"),
    path("products/<int:pk>/delete/", views.product_delete, name="product_delete"),
    path("products/<int:pk>/toggle/", views.product_toggle, name="product_toggle"),
    path("products/<int:pk>/restock/", views.product_restock, name="product_restock"),
    path("inventory/", views.inventory, name="inventory"),

    path("pos/", views.pos, name="pos"),
    path("pos/checkout/", views.pos_checkout, name="pos_checkout"),
    path("pos/cart/", views.pos_cart_save, name="pos_cart_save"),
    path("pos/customer/", views.pos_customer, name="pos_customer"),
    path("pos/add-customer/", views.pos_add_customer, name="pos_add_customer"),

    path("sales/", views.sales, name="sales"),
    path("sales/products/", views.sold_products, name="sold_products"),
    path("sales/details/", views.selling_details, name="selling_details"),
    path("sales/<int:pk>/", views.sale_detail, name="sale_detail"),
    path("sales/<int:pk>/edit/", views.sale_edit, name="sale_edit"),
    path("sales/<int:pk>/print/", views.sale_print, name="sale_print"),

    path("customers/", views.customers, name="customers"),
    path("dues/", views.dues, name="dues"),
    path("suppliers/", views.suppliers, name="suppliers"),
    path("purchases/", views.purchases, name="purchases"),
    path("purchases/<int:pk>/receive/", views.purchase_receive, name="purchase_receive"),
    path("purchases/<int:pk>/pay/", views.purchase_payment, name="purchase_payment"),
    path("purchases/<int:pk>/print/", views.purchase_print, name="purchase_print"),
    path("expenses/", views.expenses, name="expenses"),
    path("accounting/", views.accounting, name="accounting"),

    path("reports/", views.reports, name="reports"),
    path("reports/export/<str:rtype>/", views.report_export, name="report_export"),

    path("service/tickets/", views.tickets, name="tickets"),
    path("service/tickets/<int:pk>/", views.ticket_detail, name="ticket_detail"),
    path("service/tickets/<int:pk>/status/", views.ticket_status, name="ticket_status"),
    path("service/tickets/<int:pk>/invoice/", views.ticket_invoice, name="ticket_invoice"),
    path("service/warranties/", views.warranties, name="warranties"),
    path("service/warranty-coverage/", views.warranty_coverage, name="warranty_coverage"),


    path("notifications/", views.notifications, name="notifications"),
    path("tutorials/", views.tutorials, name="tutorials"),
    path("barcodes/", views.barcodes, name="barcodes"),
    path("barcodes/<int:pk>/edit/", views.barcode_edit, name="barcode_edit"),
    path("users/", views.users, name="users"),
    path("settings/", views.settings_view, name="settings"),
]
