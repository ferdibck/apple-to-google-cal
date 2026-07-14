from __future__ import annotations

import json
from typing import Any

from .credentials import GOOGLE_TOKEN_JSON_KEY, CredentialStore
from .models import (
    PRIVATE_SOURCE_HASH_KEY,
    PRIVATE_SOURCE_ID_KEY,
    PRIVATE_SOURCE_KEY,
    PRIVATE_SOURCE_VALUE,
    CalendarRef,
    GoogleEventRecord,
)

SCOPES = ["https://www.googleapis.com/auth/calendar"]


class GoogleCalendarService:
    def __init__(self, credentials: CredentialStore) -> None:
        self.credentials = credentials

    def authorize(self, client_secret_path: str) -> None:
        from google_auth_oauthlib.flow import InstalledAppFlow

        flow = InstalledAppFlow.from_client_secrets_file(client_secret_path, SCOPES)
        creds = flow.run_local_server(port=0)
        self.credentials.set(GOOGLE_TOKEN_JSON_KEY, creds.to_json())

    def list_calendars(self) -> list[CalendarRef]:
        service = self._service()
        calendars: list[CalendarRef] = []
        page_token: str | None = None
        while True:
            response = (
                service.calendarList()
                .list(pageToken=page_token, minAccessRole="reader", showHidden=True)
                .execute()
            )
            for item in response.get("items", []):
                calendars.append(
                    CalendarRef(
                        id=str(item["id"]),
                        name=str(item.get("summary") or item["id"]),
                        color=item.get("backgroundColor"),
                    )
                )
            page_token = response.get("nextPageToken")
            if not page_token:
                return calendars

    def create_calendar(self, name: str) -> CalendarRef:
        service = self._service()
        response = service.calendars().insert(body={"summary": name}).execute()
        return CalendarRef(id=str(response["id"]), name=str(response.get("summary") or name))

    def get_event(
        self,
        calendar_id: str,
        event_id: str,
        source_key: str,
    ) -> GoogleEventRecord | None:
        service = self._service()
        try:
            event = service.events().get(calendarId=calendar_id, eventId=event_id).execute()
        except Exception as exc:
            if _http_status(exc) in {404, 410}:
                return None
            raise
        private = event.get("extendedProperties", {}).get("private", {})
        if private.get(PRIVATE_SOURCE_KEY) != PRIVATE_SOURCE_VALUE:
            return None
        if private.get(PRIVATE_SOURCE_ID_KEY) != source_key:
            return None
        return GoogleEventRecord(
            id=str(event["id"]),
            etag=event.get("etag"),
            source_key=source_key,
            source_hash=private.get(PRIVATE_SOURCE_HASH_KEY),
        )

    def create_event(self, calendar_id: str, body: dict[str, Any]) -> GoogleEventRecord:
        event = (
            self._service()
            .events()
            .insert(calendarId=calendar_id, body=body, sendUpdates="none")
            .execute()
        )
        return _record_from_event(event)

    def update_event(
        self,
        calendar_id: str,
        event_id: str,
        body: dict[str, Any],
    ) -> GoogleEventRecord:
        event = (
            self._service()
            .events()
            .update(calendarId=calendar_id, eventId=event_id, body=body, sendUpdates="none")
            .execute()
        )
        return _record_from_event(event)

    def delete_event(self, calendar_id: str, event_id: str) -> None:
        try:
            self._service().events().delete(
                calendarId=calendar_id,
                eventId=event_id,
                sendUpdates="none",
            ).execute()
        except Exception as exc:
            if _http_status(exc) in {404, 410}:
                return
            raise

    def _service(self) -> Any:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        token_json = self.credentials.get(GOOGLE_TOKEN_JSON_KEY)
        if not token_json:
            raise RuntimeError("Google OAuth token is missing. Run setup first.")
        info = json.loads(token_json)
        creds = Credentials.from_authorized_user_info(info, SCOPES)  # type: ignore[no-untyped-call]
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            self.credentials.set(GOOGLE_TOKEN_JSON_KEY, creds.to_json())
        if not creds.valid:
            raise RuntimeError("Google OAuth credentials are invalid or expired. Run setup again.")
        return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _record_from_event(event: dict[str, Any]) -> GoogleEventRecord:
    private = event.get("extendedProperties", {}).get("private", {})
    return GoogleEventRecord(
        id=str(event["id"]),
        etag=event.get("etag"),
        source_key=str(private.get(PRIVATE_SOURCE_ID_KEY, "")),
        source_hash=private.get(PRIVATE_SOURCE_HASH_KEY),
    )


def _http_status(exc: BaseException) -> int | None:
    resp = getattr(exc, "resp", None)
    status = getattr(resp, "status", None)
    if isinstance(status, int):
        return status
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None
