import csv
import hashlib
import time
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
 
# The proxy occasionally has a slow moment or a network blip. Rather than
# failing the whole weekly run on the first hiccup, try a few times with
# a pause in between, and give each attempt longer to respond.
FETCH_ATTEMPTS = 4
FETCH_TIMEOUT_SECONDS = 60
FETCH_RETRY_WAIT_SECONDS = [30, 60, 120]
 
# Manual fail-safe: a hand-maintained CSV that is cross-checked against the
# proxy's data every run. Rows here can correct a wrong time/venue, add a
# fixture the proxy is missing (e.g. cup games), or -- if the proxy is down
# entirely -- stand in as the only source.
MANUAL_FIXTURES_FILE = Path("manual-fixtures.csv")
MANUAL_DATETIME_FORMAT = "%d/%m/%Y %H:%M"
 
 
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
 
    last_error = None
 
    for attempt in range(1, FETCH_ATTEMPTS + 1):
        try:
            response = requests.get(FIXTURES_API_URL, timeout=FETCH_TIMEOUT_SECONDS)
            response.raise_for_status()
            fixtures = response.json()
            break
        except (requests.RequestException, ValueError) as error:
            last_error = error
            print(f"Attempt {attempt}/{FETCH_ATTEMPTS} failed: {error}")
            if attempt < FETCH_ATTEMPTS:
                wait = FETCH_RETRY_WAIT_SECONDS[attempt - 1]
                print(f"Waiting {wait}s before retrying...")
                time.sleep(wait)
    else:
        raise RuntimeError(
            f"Fixture feed failed after {FETCH_ATTEMPTS} attempts: {last_error}"
        )
 
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
 
 
def load_manual_fixtures():
    """
    Load hand-entered fixtures from manual-fixtures.csv, if present.
    Columns: Date (DD/MM/YYYY), Time (HH:MM, 24-hour), Home, Away,
    Venue (optional), Competition (optional). Bad rows are skipped.
    """
    if not MANUAL_FIXTURES_FILE.exists():
        return []
 
    manual_fixtures = []
 
    with open(MANUAL_FIXTURES_FILE, newline="", encoding="utf-8-sig") as file:
        for row in csv.DictReader(file):
            date = str(row.get("Date") or "").strip()
            time_ = str(row.get("Time") or "").strip()
            home = str(row.get("Home") or "").strip()
            away = str(row.get("Away") or "").strip()
 
            if not (date and time_ and home and away):
                continue
 
            if TEAM_NAME.lower() not in (home.lower(), away.lower()):
                print(f"Skipping manual row not involving {TEAM_NAME}: {home} vs {away}")
                continue
 
            try:
                dt = datetime.strptime(f"{date} {time_}", MANUAL_DATETIME_FORMAT)
            except ValueError:
                print(f"Skipping manual row with unreadable date/time: {date} {time_}")
                continue
 
            manual_fixtures.append(
                {
                    "homeTeam": home,
                    "awayTeam": away,
                    "location": str(row.get("Venue") or "").strip(),
                    "competition": str(row.get("Competition") or "").strip() or "Fixture",
                    "fixtureDateTime": dt.strftime(FIXTURE_DATETIME_FORMAT),
                }
            )
 
    print(f"Loaded {len(manual_fixtures)} usable row(s) from {MANUAL_FIXTURES_FILE}.")
 
    return manual_fixtures
 
 
def merge_fixtures(api_fixtures, manual_fixtures):
    """
    Merge manual rows into the proxy's fixtures. API-only fixtures are
    never collapsed or removed. Each manual row is matched in two tiers:
 
    1. Same home, away and date -> corrects that exact fixture (works even
       when the same two teams meet twice in a season).
    2. Otherwise same home and away -> treated as a postponement and moves
       that fixture to the new date rather than duplicating it.
 
    No match at either tier -> added as a new fixture (e.g. a cup game the
    proxy doesn't list).
    """
    def pair_key(fixture):
        return (
            str(fixture.get("homeTeam", "")).strip().lower(),
            str(fixture.get("awayTeam", "")).strip().lower(),
        )
 
    def full_key(fixture):
        try:
            day = datetime.strptime(
                str(fixture.get("fixtureDateTime", "")).strip(),
                FIXTURE_DATETIME_FORMAT,
            ).date().isoformat()
        except ValueError:
            day = None
        return pair_key(fixture) + (day,)
 
    merged = list(api_fixtures)
    matched = set()
 
    for manual in manual_fixtures:
        match_index = next(
            (i for i, f in enumerate(merged) if i not in matched and full_key(f) == full_key(manual)),
            None,
        )
        if match_index is None:
            match_index = next(
                (i for i, f in enumerate(merged) if i not in matched and pair_key(f) == pair_key(manual)),
                None,
            )
 
        label = f"{manual['homeTeam']} vs {manual['awayTeam']}"
 
        if match_index is not None:
            print(f"Manual override: {label}")
            merged[match_index] = manual
            matched.add(match_index)
        else:
            print(f"Manual addition: {label}")
            merged.append(manual)
            matched.add(len(merged) - 1)
 
    return merged
 
 
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
    try:
        api_fixtures = fetch_fixtures()
    except Exception as error:
        print(f"WARNING: fixture feed unavailable: {error}")
        print("Falling back to manual-fixtures.csv only, if it has usable rows.")
        api_fixtures = []
 
    fixtures = merge_fixtures(api_fixtures, load_manual_fixtures())
 
    if not fixtures:
        raise RuntimeError(
            f"No {TEAM_NAME} fixtures were found from the feed or "
            "manual-fixtures.csv. Refusing to overwrite the published calendar."
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
 
