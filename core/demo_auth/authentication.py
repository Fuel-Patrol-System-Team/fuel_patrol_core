import logging
from django.conf import settings
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.tokens import UntypedToken

logger = logging.getLogger(__name__)


class DemoTokenAuthentication(JWTAuthentication):
    def get_validated_token(self, raw_token):
        try:
            untyped = UntypedToken(raw_token)
            if untyped.payload.get("is_demo"):
                return _DemoUntypedToken(raw_token)
        except TokenError:
            pass

        return super().get_validated_token(raw_token)

    def authenticate(self, request):
        result = super().authenticate(request)
        if result is None:
            return None

        user, token = result
        if getattr(token, "is_demo", False) or token.payload.get("is_demo"):
            request.is_demo_user = True
        else:
            request.is_demo_user = False

        return user, token


class _DemoUntypedToken(UntypedToken):
    token_type = "demo_access"

    is_demo = True

    def verify(self):
        pass
