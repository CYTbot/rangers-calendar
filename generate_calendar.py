import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

# --- FA Full-Time identifiers -------------------------------------------
# fulltime.thefa.com now blocks server-side scraping (403 Forbidden), so
# fixtures are pulled from a third-party proxy that mirrors the same data
# as JSON. DIVISION_ID / SEASON_ID come from the fixtures URL on
# fulltime.thefa.com for this team's division (view page source / network
# tab there if these ever need updating).
DIVISION_ID = 628154766
SEASON_ID = 585452548

FIXTURES_API_URL = (
    f"https://faapi.jwhsolutions.co.uk/api/Fixtures/{DIVISION_ID}/season/{SEASON_ID}"
)

TEAM_NAME = "Orton Rangers U11"

OUTPUT_FILE = Path("public/orton-rangers-u11.ics")
TIMEZONE = "Europe/London"
FIXTURE_DATETIME_FORMAT = "%d/%m/%y %H:%M"
MATCH_DURATION_MINUTES = 90


def ics_escape(value):
    """Escape text for an iCalendar field."""
    if value is None:
        return ""

    value = str(value).strip()
    value = value.replace("\\", "\\\\")
    value = value.replace(";", "\\;")
    value = value.replace(",", "\\,")
    value = value.replace("\r\n", "\\n")
    value = value.replace("\n", "\\n")

    return value


def fold_ics_line(line):
    """Fold long iCalendar lines according to RFC 5545."""
    result = []

    while len(line.encode("utf-8")) > 75:
        cut = 75

        while len(line[:cut].encode("utf-8")) > 75:
            cut -= 1

        result.append(line[:cut])
        line = " " + line[cut:]

    result.append(line)

    return "\r\n".join(result)


def make_uid(home, away, competition, match_date):
    """
    Create a stable UID.

    The kick-off *time* is deliberately excluded so that if a fixture's
    time changes but not its date, Apple Calendar treats it as an update
    to the same event rather than a duplicate. The *date* is included
    because the same two teams can legitimately meet more than once in a
    season (e.g. home and away league fixtures) -- hashing on team names
    and competition alone would collide those into a single UID and one
    of the fixtures would silently disappear from the subscription.
    """
    key = f"{home}|{away}|{competition}|{match_date.isoformat()}".lower()

    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]

    return f"{digest}@orton-rangers-u11-calendar"


def fetch_fixtures():
    """Retrieve all fixtures for the division from the fixtures proxy."""

    response = requests.get(FIXTURES_API_URL, timeout=30)
    response.raise_for_status()

    fixtures = response.json()

    print(f"Fixture feed returned {len(fixtures)} fixtures for the division.")

    team_fixtures = [
        fixture
        for fixture in fixtures
        if TEAM_NAME.lower()
        in (
            str(fixture.get("homeTeam", "")).strip().lower(),
            str(fixture.get("awayTeam", "")).strip().lower(),
        )
    ]

    print(f"Found {len(team_fixtures)} fixtures for {TEAM_NAME}.")

    return team_fixtures


def create_calendar(fixtures):
    tz = ZoneInfo(TIMEZONE)

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Orton Rangers U11//Fixture Calendar//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Orton Rangers U11 Fixtures",
        "X-WR-TIMEZONE:Europe/London",
    ]

    now = datetime.now(tz).strftime("%Y%m%dT%H%M%S")
    seen_uids = set()

    for fixture in fixtures:
        home = str(fixture.get("homeTeam", "")).strip()
        away = str(fixture.get("awayTeam", "")).strip()
        venue = str(fixture.get("location", "")).strip()
        competition = str(fixture.get("competition", "Fixture")).strip()
        raw_datetime = str(fixture.get("fixtureDateTime", "")).strip()

        try:
            start = datetime.strptime(raw_datetime, FIXTURE_DATETIME_FORMAT)
        except ValueError:
            # Fixtures without a confirmed kick-off time come through as
            # "TBC" or similar rather than a parseable date/time.
            print(f"Skipping fixture with no confirmed time: {home} vs {away}")
            continue

        start = start.replace(tzinfo=tz)

        # Use timedelta rather than datetime.replace so that matches
        # crossing an hour boundary work correctly.
        end = start + timedelta(minutes=MATCH_DURATION_MINUTES)

        uid = make_uid(home, away, competition, start.date())

        # Belt-and-braces: if two fixtures still hash to the same UID
        # (e.g. a genuine same-day doubleheader), disambiguate rather
        # than silently dropping one of them.
        original_uid = uid
        suffix = 2
        while uid in seen_uids:
            uid = f"{original_uid.split('@')[0]}-{suffix}@orton-rangers-u11-calendar"
            suffix += 1
        seen_uids.add(uid)

        summary = f"{home} vs {away}"

        description_lines = [competition, summary]
        if venue:
            description_lines.append(venue)
        description = "\n".join(description_lines)

        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{uid}",
                f"DTSTAMP:{now}",
                f"DTSTART;TZID={TIMEZONE}:{start.strftime('%Y%m%dT%H%M%S')}",
                f"DTEND;TZID={TIMEZONE}:{end.strftime('%Y%m%dT%H%M%S')}",
                f"SUMMARY:{ics_escape(summary)}",
                f"DESCRIPTION:{ics_escape(description)}",
            ]
        )

        if venue:
            lines.append(f"LOCATION:{ics_escape(venue)}")

        lines.extend(
            [
                "STATUS:CONFIRMED",
                "TRANSP:OPAQUE",
                "END:VEVENT",
            ]
        )

    lines.append("END:VCALENDAR")

    return "\r\n".join(fold_ics_line(line) for line in lines) + "\r\n"


def main():
    # If anything below raises, this script exits non-zero, the GitHub
    # Actions job fails, and the "deploy" job (which needs it) never runs
    # -- so GitHub Pages keeps serving the last successfully published
    # calendar instead of an empty or broken one.
    fixtures = fetch_fixtures()

    if not fixtures:
        raise RuntimeError(
            f"No {TEAM_NAME} fixtures were found. Refusing to overwrite "
            "the published calendar."
        )

    calendar = create_calendar(fixtures)

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    with open(OUTPUT_FILE, "w", encoding="utf-8", newline="") as file:
        file.write(calendar)

    print(f"Generated {OUTPUT_FILE} with {len(fixtures)} fixtures.")

    for fixture in fixtures:
        print(
            f"{fixture.get('fixtureDateTime')} - "
            f"{fixture.get('homeTeam')} vs {fixture.get('awayTeam')} "
            f"({fixture.get('location')})"
        )


if __name__ == "__main__":
    main()
