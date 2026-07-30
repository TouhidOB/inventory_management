from rest_framework import serializers

from tenants.models import Shop

from .models import User


class ShopRegistrationSerializer(serializers.Serializer):
    """Public self-service shop signup payload."""

    shop_name = serializers.CharField(max_length=150)
    owner_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    owner_email = serializers.EmailField()
    owner_password = serializers.CharField(write_only=True, min_length=8)
    business_type = serializers.ChoiceField(
        choices=Shop.BusinessType.choices, default=Shop.BusinessType.GENERAL
    )
    phone = serializers.CharField(max_length=30, required=False, allow_blank=True)

    def validate_owner_email(self, value):
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return value


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = [
            "id", "email", "first_name", "last_name", "phone",
            "role", "shop", "branch", "is_staff",
        ]
        read_only_fields = ["id", "shop", "is_staff"]
