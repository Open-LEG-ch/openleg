# SPDX-License-Identifier: AGPL-3.0-or-later
"""Meetup inputs and RFC 5545 output; never a recurring scheduling promise."""

import re
from datetime import UTC, datetime
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

LOCALES = ("de_CH", "en", "fr_CH", "it_CH")
ZURICH = ZoneInfo("Europe/Zurich")


class EventInputError(ValueError):
    """An incomplete or ambiguous public occurrence, independent of storage."""


def local_instant(value):
    try:
        # Compare both Zurich folds before accepting a local wall-clock value.
        naive = datetime.strptime(value, "%Y-%m-%dT%H:%M")  # noqa: DTZ007
        instants = {
            naive.replace(tzinfo=ZURICH, fold=fold).astimezone(UTC)
            for fold in (0, 1)
            if naive.replace(tzinfo=ZURICH, fold=fold)
            .astimezone(UTC)
            .astimezone(ZURICH)
            .replace(tzinfo=None)
            == naive
        }
        if len(instants) != 1:
            raise ValueError
        return instants.pop()
    except (TypeError, ValueError):
        raise EventInputError("Invalid or ambiguous Europe/Zurich time") from None


def validated(data):
    """Copy only public occurrence fields; private approval evidence stays separate."""
    try:
        slug = data["slug"].strip()
        start, end = local_instant(data["start"]), local_instant(data["end"])
        organiser = data["organiser"].strip()
        languages = data["spoken_languages"].strip()
        status = data["status"]
        state = data["join_state"]
        url = data.get("join_url", "").strip()
        translations = {
            locale: {
                key: data["translations"][locale][key].strip()
                for key in ("title", "audience", "agenda")
            }
            for locale in LOCALES
        }
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,79}", slug) or end <= start:
            raise ValueError
        if not 3 <= len(organiser) <= 160 or not 2 <= len(languages) <= 100:
            raise ValueError
        if status not in {"draft", "scheduled", "cancelled"} or state not in {
            "open",
            "full",
            "closed",
            "unavailable",
        }:
            raise ValueError
        parts = urlsplit(url)
        if url and (
            parts.scheme != "https"
            or not parts.hostname
            or parts.username
            or parts.password
            or len(url) > 500
        ):
            raise ValueError
        if state == "open" and not url:
            raise ValueError
        if any(
            not 3 <= len(text) <= (160 if key == "title" else 4000)
            for entry in translations.values()
            for key, text in entry.items()
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError, AttributeError):
        raise EventInputError(
            "Complete actual occurrence details required in all four languages"
        ) from None
    return {
        "slug": slug,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "organiser": organiser,
        "spoken_languages": languages,
        "status": status,
        "join_state": state,
        "join_url": url,
        "translations": translations,
    }


def calendar(event, locale, detail_url):
    """Public calendar carries only a detail URL, never joining/access information."""

    def escape(value):
        return (
            str(value)
            .replace("\\", "\\\\")
            .replace("\r\n", "\n")
            .replace("\r", "\n")
            .replace("\n", "\\n")
            .replace(";", "\\;")
            .replace(",", "\\,")
        )

    def stamp(value):
        return value.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//OpenLEG//Meetups//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-TIMEZONE:Europe/Zurich",
        "BEGIN:VEVENT",
        "UID:" + str(event["id"]) + "@openleg.ch",
        "DTSTAMP:" + stamp(event["updated_at"]),
        "SEQUENCE:" + str(event["version"]),
        "DTSTART:" + stamp(event["starts_at"]),
        "DTEND:" + stamp(event["ends_at"]),
        "SUMMARY:" + escape(event["content"]["translations"][locale]["title"]),
        "URL:" + escape(detail_url),
        "STATUS:" + ("CANCELLED" if event["status"] == "cancelled" else "CONFIRMED"),
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    folded = []
    for line in lines:
        part = ""
        for character in line:
            if len((part + character).encode("utf-8")) > 75:
                folded.append(part)
                part = " "
            part += character
        folded.append(part)
    return "\r\n".join(folded) + "\r\n"
