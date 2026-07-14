from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast

from .credentials import APPLE_APP_PASSWORD_KEY, APPLE_EMAIL_KEY, CredentialStore
from .logging_config import get_logger
from .models import CalendarRef, CalendarSyncState, ChangeBatch, EventDateTime, SourceEvent

LOGGER = get_logger("icloud")

SAFE_CALDAV_METHODS = {"GET", "HEAD", "OPTIONS", "PROPFIND", "REPORT"}


class ReadOnlyCalDAVViolationError(RuntimeError):
    pass


class ICloudCalDAVService:
    def __init__(
        self,
        *,
        base_url: str,
        credentials: CredentialStore,
    ) -> None:
        self.base_url = base_url
        self.credentials = credentials

    def list_calendars(self) -> list[CalendarRef]:
        principal = self._principal()
        calendars = self._principal_calendars(principal)
        refs: list[CalendarRef] = []
        for calendar in calendars:
            refs.append(
                CalendarRef(
                    id=self._calendar_id(calendar),
                    name=self._calendar_name(calendar),
                )
            )
        return refs

    def get_changes(
        self,
        calendar: CalendarRef,
        state: CalendarSyncState,
        window_start: datetime,
        window_end: datetime,
        *,
        full: bool,
    ) -> ChangeBatch:
        caldav_calendar = self._calendar_by_id(calendar.id)
        collection_tag = self._collection_tag(caldav_calendar)

        if not full and state.sync_token:
            sync_batch = self._try_sync_token(caldav_calendar, calendar, state.sync_token)
            if sync_batch is not None:
                return ChangeBatch(
                    events=sync_batch.events,
                    deleted_source_keys=sync_batch.deleted_source_keys,
                    sync_token=sync_batch.sync_token or state.sync_token,
                    collection_tag=collection_tag,
                )

        if not full and collection_tag and collection_tag == state.collection_tag:
            return ChangeBatch(
                events=(),
                sync_token=state.sync_token,
                collection_tag=collection_tag,
            )

        objects = self._search_objects(caldav_calendar, window_start, window_end)
        events: list[SourceEvent] = []
        for obj in objects:
            events.extend(self._parse_calendar_object(calendar, obj))

        return ChangeBatch(
            events=tuple(events),
            sync_token=state.sync_token,
            collection_tag=collection_tag,
        )

    def _principal(self) -> Any:
        client = self._client()
        return client.principal()

    def _client(self) -> Any:
        email = self.credentials.get(APPLE_EMAIL_KEY)
        password = self.credentials.get(APPLE_APP_PASSWORD_KEY)
        if not email or not password:
            raise RuntimeError(
                "Apple email or app-specific password is missing from Credential Manager."
            )

        import caldav

        dav_client_factory = cast(Any, caldav).DAVClient
        client = dav_client_factory(url=self.base_url, username=email, password=password)
        self._install_read_only_guard(client)
        return client

    def _install_read_only_guard(self, client: Any) -> None:
        for owner_name in ("session", "requests", "http"):
            owner = getattr(client, owner_name, None)
            request = getattr(owner, "request", None)
            if callable(request):
                owner_any = cast(Any, owner)
                owner_any.request = self._guarded_request(request)
        request = getattr(client, "request", None)
        if callable(request):
            client.request = self._guarded_request(request)

    @staticmethod
    def _guarded_request(request: Any) -> Any:
        def guarded(*args: Any, **kwargs: Any) -> Any:
            method = kwargs.get("method")
            if not isinstance(method, str):
                for arg in args[:2]:
                    if isinstance(arg, str) and arg.upper() in SAFE_CALDAV_METHODS | {
                        "PUT",
                        "POST",
                        "DELETE",
                    }:
                        method = arg
                        break
            if isinstance(method, str) and method.upper() not in SAFE_CALDAV_METHODS:
                raise ReadOnlyCalDAVViolationError(
                    f"Blocked mutating CalDAV method: {method.upper()}"
                )
            return request(*args, **kwargs)

        return guarded

    @staticmethod
    def _principal_calendars(principal: Any) -> list[Any]:
        for method_name in ("calendars", "get_calendars"):
            method = getattr(principal, method_name, None)
            if callable(method):
                return list(method())
        raise RuntimeError("The CalDAV library did not expose a calendar listing method.")

    def _calendar_by_id(self, calendar_id: str) -> Any:
        principal = self._principal()
        for calendar in self._principal_calendars(principal):
            if self._calendar_id(calendar) == calendar_id:
                return calendar
        raise RuntimeError(f"Selected iCloud calendar no longer exists: {calendar_id}")

    @staticmethod
    def _calendar_id(calendar: Any) -> str:
        url = getattr(calendar, "url", None)
        return str(url or getattr(calendar, "canonical_url", "") or getattr(calendar, "id", ""))

    @staticmethod
    def _calendar_name(calendar: Any) -> str:
        name = getattr(calendar, "name", None)
        if name:
            return str(name)
        get_properties = getattr(calendar, "get_properties", None)
        if callable(get_properties):
            try:
                props = get_properties(["{DAV:}displayname"])
                display_name = props.get("{DAV:}displayname") if isinstance(props, dict) else None
                if display_name:
                    return str(display_name)
            except Exception:
                LOGGER.debug("Unable to read calendar display name", exc_info=True)
        return ICloudCalDAVService._calendar_id(calendar)

    @staticmethod
    def _collection_tag(calendar: Any) -> str | None:
        get_properties = getattr(calendar, "get_properties", None)
        if not callable(get_properties):
            return None
        for props in (
            ["{http://calendarserver.org/ns/}getctag", "{DAV:}getetag"],
            ["getctag", "getetag"],
        ):
            try:
                values = get_properties(props)
            except Exception:
                continue
            if isinstance(values, dict):
                for key in (
                    "{http://calendarserver.org/ns/}getctag",
                    "getctag",
                    "{DAV:}getetag",
                    "getetag",
                ):
                    value = values.get(key)
                    if value:
                        return str(value)
        return None

    def _try_sync_token(
        self,
        calendar: Any,
        calendar_ref: CalendarRef,
        sync_token: str,
    ) -> ChangeBatch | None:
        method = getattr(calendar, "objects_by_sync_token", None)
        if not callable(method):
            return None
        try:
            result = method(sync_token)
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in {400, 403, 404, 409, 410}:
                return ChangeBatch(events=(), full_sync_required=True)
            LOGGER.debug(
                "CalDAV sync-token query failed; falling back to collection comparison.",
                exc_info=True,
            )
            return None

        objects: list[Any] = []
        deleted_source_keys: list[str] = []
        next_token = sync_token

        if isinstance(result, tuple):
            for item in result:
                if isinstance(item, str):
                    next_token = item
                elif isinstance(item, list | tuple):
                    objects.extend(item)
        elif isinstance(result, dict):
            objects.extend(result.get("objects", ()))
            deleted_source_keys.extend(result.get("deleted", ()))
            next_token = str(result.get("sync_token") or sync_token)
        else:
            objects = list(result)

        events: list[SourceEvent] = []
        for obj in objects:
            events.extend(self._parse_calendar_object(calendar_ref, obj))
        return ChangeBatch(
            events=tuple(events),
            deleted_source_keys=tuple(deleted_source_keys),
            sync_token=next_token,
        )

    @staticmethod
    def _search_objects(calendar: Any, window_start: datetime, window_end: datetime) -> list[Any]:
        start = window_start.astimezone(UTC)
        end = window_end.astimezone(UTC)
        for method_name in ("search", "date_search"):
            method = getattr(calendar, method_name, None)
            if not callable(method):
                continue
            try:
                if method_name == "search":
                    return list(method(start=start, end=end, event=True, expand=False))
                return list(method(start=start, end=end, event=True, expand=False))
            except TypeError:
                try:
                    return list(method(start, end))
                except TypeError:
                    continue
        method = getattr(calendar, "events", None)
        if callable(method):
            return list(method())
        raise RuntimeError("The CalDAV library did not expose an event search method.")

    def _parse_calendar_object(self, calendar: CalendarRef, obj: Any) -> list[SourceEvent]:
        raw = getattr(obj, "data", None)
        if raw is None:
            get = getattr(obj, "load", None)
            if callable(get):
                obj = get()
                raw = getattr(obj, "data", None)
        raw_text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw or "")
        if not raw_text.strip():
            return []

        from icalendar import Calendar

        parsed = Calendar.from_ical(raw_text)
        href = str(getattr(obj, "url", "") or getattr(obj, "href", "") or "") or None
        etag = self._object_attr(obj, ("etag", "getetag", "get_etag"))
        last_modified = self._object_attr(obj, ("last_modified", "lastmodified"))

        events: list[SourceEvent] = []
        for component in parsed.walk("VEVENT"):
            event = self._parse_vevent(calendar, component, href, etag, last_modified, raw_text)
            events.append(event)
        return events

    @staticmethod
    def _object_attr(obj: Any, names: tuple[str, ...]) -> str | None:
        for name in names:
            value = getattr(obj, name, None)
            if callable(value):
                try:
                    value = value()
                except Exception:
                    continue
            if value:
                return str(value)
        return None

    def _parse_vevent(
        self,
        calendar: CalendarRef,
        component: Any,
        href: str | None,
        etag: str | None,
        last_modified: str | None,
        raw_text: str,
    ) -> SourceEvent:
        uid = self._text(component, "UID") or self._fallback_uid(href, raw_text)
        recurrence_id = self._recurrence_id(component)
        start = self._event_datetime(component, "DTSTART")
        end = self._event_datetime(component, "DTEND")
        if end is None:
            end = self._duration_end(component, start)
        if start is None or end is None:
            source_key = make_source_key(calendar.id, uid, recurrence_id)
            return SourceEvent(
                source_key=source_key,
                calendar_id=calendar.id,
                calendar_name=calendar.name,
                uid=uid,
                recurrence_id=recurrence_id,
                href=href,
                etag=etag,
                last_modified=last_modified,
                source_hash=hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
                summary=self._text(component, "SUMMARY") or "(No title)",
                description=self._text(component, "DESCRIPTION"),
                location=self._text(component, "LOCATION"),
                start=EventDateTime(datetime.now(UTC)),
                end=EventDateTime(datetime.now(UTC) + timedelta(hours=1)),
                status=self._text(component, "STATUS"),
                unsupported_reason="VEVENT is missing DTSTART or DTEND/DURATION.",
            )

        recurrence = self._recurrence(component) if recurrence_id is None else ()
        source_key = make_source_key(calendar.id, uid, recurrence_id)
        source_hash = self._meaningful_hash(component, start, end, recurrence)
        return SourceEvent(
            source_key=source_key,
            calendar_id=calendar.id,
            calendar_name=calendar.name,
            uid=uid,
            recurrence_id=recurrence_id,
            href=href,
            etag=etag,
            last_modified=last_modified,
            source_hash=source_hash,
            summary=self._text(component, "SUMMARY") or "(No title)",
            description=self._text(component, "DESCRIPTION"),
            location=self._text(component, "LOCATION"),
            start=start,
            end=end,
            url=self._text(component, "URL"),
            transparency=self._text(component, "TRANSP"),
            status=self._text(component, "STATUS"),
            recurrence=recurrence,
            sequence=self._integer(component, "SEQUENCE"),
        )

    @staticmethod
    def _text(component: Any, name: str) -> str | None:
        value = component.get(name)
        if value is None:
            return None
        return str(value)

    @staticmethod
    def _integer(component: Any, name: str) -> int | None:
        value = component.get(name)
        if value is None:
            return None
        try:
            return int(value)
        except ValueError:
            return None

    @staticmethod
    def _fallback_uid(href: str | None, raw_text: str) -> str:
        return hashlib.sha256(f"{href or ''}\0{raw_text}".encode()).hexdigest()

    @classmethod
    def _recurrence_id(cls, component: Any) -> str | None:
        value = component.get("RECURRENCE-ID")
        if value is None:
            return None
        return cls._component_date_to_identity(value)

    @classmethod
    def _event_datetime(cls, component: Any, name: str) -> EventDateTime | None:
        prop = component.get(name)
        if prop is None:
            return None
        value = getattr(prop, "dt", prop)
        all_day = isinstance(value, date) and not isinstance(value, datetime)
        tzid = cls._tzid(prop)
        return EventDateTime(value=value, all_day=all_day, time_zone=tzid)

    @classmethod
    def _duration_end(cls, component: Any, start: EventDateTime | None) -> EventDateTime | None:
        if start is None:
            return None
        duration_prop = component.get("DURATION")
        if duration_prop is None:
            if (
                start.all_day
                and isinstance(start.value, date)
                and not isinstance(start.value, datetime)
            ):
                return EventDateTime(
                    value=start.value + timedelta(days=1),
                    all_day=True,
                    time_zone=start.time_zone,
                )
            return None
        duration = getattr(duration_prop, "dt", duration_prop)
        return EventDateTime(
            value=start.value + duration,
            all_day=start.all_day,
            time_zone=start.time_zone,
        )

    @staticmethod
    def _tzid(prop: Any) -> str | None:
        params = getattr(prop, "params", None)
        if not params:
            return None
        tzid = params.get("TZID")
        return str(tzid) if tzid else None

    @staticmethod
    def _component_date_to_identity(prop: Any) -> str:
        value = getattr(prop, "dt", prop)
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, date):
            return value.isoformat()
        return str(value)

    @staticmethod
    def _recurrence(component: Any) -> tuple[str, ...]:
        lines: list[str] = []
        for name in ("RRULE", "RDATE", "EXDATE"):
            values = component.get(name)
            if values is None:
                continue
            if not isinstance(values, list):
                values = [values]
            for value in values:
                try:
                    encoded = value.to_ical().decode("utf-8")
                except Exception:
                    encoded = str(value)
                lines.append(f"{name}:{encoded}")
        return tuple(lines)

    def _meaningful_hash(
        self,
        component: Any,
        start: EventDateTime,
        end: EventDateTime,
        recurrence: tuple[str, ...],
    ) -> str:
        payload = {
            "summary": self._text(component, "SUMMARY") or "",
            "description": self._text(component, "DESCRIPTION") or "",
            "location": self._text(component, "LOCATION") or "",
            "start": self._datetime_payload(start),
            "end": self._datetime_payload(end),
            "url": self._text(component, "URL") or "",
            "transparency": self._text(component, "TRANSP") or "",
            "status": self._text(component, "STATUS") or "",
            "recurrence": list(recurrence),
            "sequence": self._integer(component, "SEQUENCE"),
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _datetime_payload(value: EventDateTime) -> dict[str, str | bool]:
        return {
            "value": value.value.isoformat(),
            "all_day": value.all_day,
            "time_zone": value.time_zone or "",
        }


def make_source_key(calendar_id: str, uid: str, recurrence_id: str | None) -> str:
    material = "\0".join((calendar_id, uid, recurrence_id or ""))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()
