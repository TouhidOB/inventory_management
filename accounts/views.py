from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from tenants.services import register_shop

from .serializers import ShopRegistrationSerializer, UserSerializer


class RegisterShopView(APIView):
    """Public endpoint: create a shop + owner and return JWT tokens."""

    permission_classes = [AllowAny]

    def post(self, request):
        ser = ShopRegistrationSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data

        shop, owner = register_shop(
            name=data["shop_name"],
            owner_email=data["owner_email"],
            owner_password=data["owner_password"],
            owner_name=data.get("owner_name", ""),
            business_type=data["business_type"],
            phone=data.get("phone", ""),
        )
        refresh = RefreshToken.for_user(owner)
        return Response(
            {
                "shop": {"id": shop.id, "name": shop.name, "slug": shop.slug},
                "user": UserSerializer(owner).data,
                "access": str(refresh.access_token),
                "refresh": str(refresh),
            },
            status=status.HTTP_201_CREATED,
        )


class MeView(APIView):
    """Return the authenticated user's profile."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(UserSerializer(request.user).data)
