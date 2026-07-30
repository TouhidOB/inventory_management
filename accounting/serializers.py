from rest_framework import serializers

from .models import Expense, ExpenseCategory


class ExpenseCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = ExpenseCategory
        fields = ["id", "name"]


class ExpenseSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True, default=None)

    class Meta:
        model = Expense
        fields = [
            "id", "category", "category_name", "amount", "spent_on",
            "payment_method", "note", "created_at",
        ]
