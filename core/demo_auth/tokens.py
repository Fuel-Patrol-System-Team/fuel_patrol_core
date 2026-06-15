from datetime import timedelta

from rest_framework_simplejwt.tokens import Token


class DemoAccessToken(Token):
    token_type = "demo_access"
    lifetime = timedelta(days=365 * 100)

    def verify(self):
        self.verify_token_type()


def generate_demo_token(demo_user) -> str:
    token = DemoAccessToken.for_user(demo_user)
    token.payload.pop("exp", None)
    token.payload.pop("jti", None)
    token.payload["is_demo"] = True
    return str(token)
