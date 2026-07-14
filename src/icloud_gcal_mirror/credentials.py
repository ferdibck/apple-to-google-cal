from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

SERVICE_NAME = "icloud-google-calendar-mirror"
APPLE_EMAIL_KEY = "apple-email"
APPLE_APP_PASSWORD_KEY = "apple-app-specific-password"
GOOGLE_TOKEN_JSON_KEY = "google-oauth-token-json"


class CredentialStore(Protocol):
    def get(self, key: str) -> str | None: ...

    def set(self, key: str, value: str) -> None: ...

    def delete(self, key: str) -> None: ...


class KeyringCredentialStore:
    def __init__(self, service_name: str = SERVICE_NAME) -> None:
        self.service_name = service_name

    def get(self, key: str) -> str | None:
        import keyring

        return keyring.get_password(self.service_name, key)

    def set(self, key: str, value: str) -> None:
        import keyring

        keyring.set_password(self.service_name, key, value)

    def delete(self, key: str) -> None:
        import keyring

        try:
            keyring.delete_password(self.service_name, key)
        except keyring.errors.PasswordDeleteError:
            return


@dataclass
class MemoryCredentialStore:
    values: dict[str, str] = field(default_factory=dict)

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def set(self, key: str, value: str) -> None:
        self.values[key] = value

    def delete(self, key: str) -> None:
        self.values.pop(key, None)


def credential_report(store: CredentialStore) -> dict[str, bool]:
    return {
        "apple_email": store.get(APPLE_EMAIL_KEY) is not None,
        "apple_app_specific_password": store.get(APPLE_APP_PASSWORD_KEY) is not None,
        "google_oauth_token": store.get(GOOGLE_TOKEN_JSON_KEY) is not None,
    }
