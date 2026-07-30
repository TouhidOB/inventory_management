from django.contrib import admin

from .models import Expense, ExpenseCategory, LedgerEntry, RecurringExpense


@admin.register(ExpenseCategory)
class ExpenseCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "shop")


@admin.register(RecurringExpense)
class RecurringExpenseAdmin(admin.ModelAdmin):
    list_display = ("label", "shop", "amount", "day_of_month", "is_active", "last_generated_period")
    list_filter = ("is_active",)


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ("shop", "category", "amount", "spent_on")
    list_filter = ("spent_on",)


@admin.register(LedgerEntry)
class LedgerEntryAdmin(admin.ModelAdmin):
    list_display = ("created_at", "shop", "account", "amount", "source_type")
    list_filter = ("account",)
    readonly_fields = [f.name for f in LedgerEntry._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
