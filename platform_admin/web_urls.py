from django.urls import path

from . import web

app_name = "platform"

urlpatterns = [
    path("", web.dashboard, name="dashboard"),
    path("shops/", web.shops, name="shops"),
    path("users/", web.active_users, name="users"),
    path("shops/new/", web.shop_create, name="shop_create"),
    path("shops/<int:pk>/toggle/", web.shop_toggle, name="shop_toggle"),
    path("shops/<int:pk>/delete/", web.shop_delete, name="shop_delete"),
    path("shops/<int:pk>/impersonate/", web.impersonate, name="impersonate"),
    path("shops/<int:pk>/owner-password/", web.shop_owner_password, name="shop_owner_password"),
    path("impersonate/stop/", web.stop_impersonation, name="stop_impersonation"),
    path("payments/", web.payments, name="payments"),
    path("payments/<int:pk>/review/", web.payment_review, name="payment_review"),
    path("api-keys/", web.api_keys, name="api_keys"),
    path("api-keys/<int:pk>/regenerate/", web.api_key_regenerate, name="api_key_regenerate"),
    path("api-keys/<int:pk>/revoke/", web.api_key_revoke, name="api_key_revoke"),
    path("plans/", web.plans, name="plans"),
    path("messages/", web.messages_inbox, name="messages"),
    path("messages/<int:pk>/read/", web.message_read, name="message_read"),
    path("messages/<int:pk>/delete/", web.message_delete, name="message_delete"),
    path("tutorials/", web.tutorials, name="tutorials"),
    path("tutorials/<int:pk>/edit/", web.tutorial_edit, name="tutorial_edit"),
    path("tutorials/<int:pk>/delete/", web.tutorial_delete, name="tutorial_delete"),
]
