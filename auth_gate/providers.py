"""OAuth 2.0 device-authorization flow for GitHub and Google (RFC 8628)."""

from dataclasses import dataclass

from . import http
from .errors import GateError

DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"


@dataclass(frozen=True)
class DeviceCode:
    device_code: str
    user_code: str
    verification_uri: str
    interval: int
    expires_in: int


@dataclass(frozen=True)
class Identity:
    provider: str
    subject: str  # allowlist key, e.g. "github:octocat" or "google:me@x.com"
    display: str


class Provider:
    name: str

    def start(self) -> DeviceCode:
        raise NotImplementedError

    def poll(self, device_code: str) -> dict:
        """Return the token response; it contains "error" while still pending."""
        raise NotImplementedError

    def identify(self, access_token: str) -> Identity | None:
        """Return who owns the token, or None if the token is invalid/revoked."""
        raise NotImplementedError

    def revoke(self, access_token: str) -> None:
        pass


class GitHubProvider(Provider):
    name = "github"

    def __init__(self, client_id: str):
        if not client_id:
            raise GateError("GITHUB_CLIENT_ID is not set")
        self.client_id = client_id

    def start(self) -> DeviceCode:
        status, data = http.post_form(
            "https://github.com/login/device/code",
            {"client_id": self.client_id, "scope": "read:user"},
        )
        if status != 200 or "device_code" not in data:
            raise GateError(f"GitHub device code request failed: {data.get('error', status)}")
        return DeviceCode(
            device_code=data["device_code"],
            user_code=data["user_code"],
            verification_uri=data["verification_uri"],
            interval=int(data.get("interval", 5)),
            expires_in=int(data.get("expires_in", 900)),
        )

    def poll(self, device_code: str) -> dict:
        _, data = http.post_form(
            "https://github.com/login/oauth/access_token",
            {"client_id": self.client_id, "device_code": device_code, "grant_type": DEVICE_GRANT},
        )
        return data

    def identify(self, access_token: str) -> Identity | None:
        status, data = http.get_json("https://api.github.com/user", access_token)
        if status == 401:
            return None
        if status != 200 or not data.get("login"):
            raise GateError(f"GitHub identity check failed (HTTP {status})")
        login = data["login"]
        return Identity("github", f"github:{login.lower()}", login)


class GoogleProvider(Provider):
    name = "google"

    def __init__(self, client_id: str, client_secret: str):
        if not client_id or not client_secret:
            raise GateError("GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must be set")
        self.client_id = client_id
        self.client_secret = client_secret

    def start(self) -> DeviceCode:
        status, data = http.post_form(
            "https://oauth2.googleapis.com/device/code",
            {"client_id": self.client_id, "scope": "openid email"},
        )
        if status != 200 or "device_code" not in data:
            raise GateError(f"Google device code request failed: {data.get('error', status)}")
        return DeviceCode(
            device_code=data["device_code"],
            user_code=data["user_code"],
            verification_uri=data.get("verification_url") or data["verification_uri"],
            interval=int(data.get("interval", 5)),
            expires_in=int(data.get("expires_in", 1800)),
        )

    def poll(self, device_code: str) -> dict:
        _, data = http.post_form(
            "https://oauth2.googleapis.com/token",
            {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "device_code": device_code,
                "grant_type": DEVICE_GRANT,
            },
        )
        return data

    def identify(self, access_token: str) -> Identity | None:
        status, data = http.get_json("https://openidconnect.googleapis.com/v1/userinfo", access_token)
        if status == 401:
            return None
        if status != 200 or not data.get("email"):
            raise GateError(f"Google identity check failed (HTTP {status})")
        if data.get("email_verified") is not True:
            raise GateError("Google account email is not verified")
        email = data["email"]
        return Identity("google", f"google:{email.lower()}", email)

    def revoke(self, access_token: str) -> None:
        http.post_form("https://oauth2.googleapis.com/revoke", {"token": access_token})
