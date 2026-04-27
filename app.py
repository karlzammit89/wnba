import streamlit as st
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# =========================
# TITLE
# =========================
st.title("🏀 WNBA Dashboard")

# =========================
# ESPN API BASE URLS
# =========================
ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard"
ESPN_PBP = "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/playbyplay"
ESPN_SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/summary"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; WNBA-Dashboard/1.0)"
}

# =========================
# MODE
# =========================
mode = st.radio("Select Mode", ["Schedule", "Game Feed"])

# =========================
# HELPERS
# =========================
def convert_to_et(raw_time):
    if not raw_time:
        return None
    try:
        dt = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
        return dt.astimezone(ZoneInfo("America/New_York")).replace(microsecond=0)
    except Exception:
        return None


def convert_to_et_str(raw_time):
    dt = convert_to_et(raw_time)
    if not dt:
        return None
    is_dst = dt.dst() != timedelta(0)
    tz_label = "EDT" if is_dst else "EST"
    return dt.strftime(f"%Y-%m-%d %H:%M:%S {tz_label}")


# =========================
# MODE 1 — SCHEDULE
# =========================
if mode == "Schedule":

    date_input = st.text_input(
        "Enter date (YYYY-MM-DD)",
        datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    )

    if st.button("Load Games"):

        try:
            selected_date = datetime.fromisoformat(date_input).date()
        except Exception:
            st.error("Invalid date format. Please use YYYY-MM-DD.")
            st.stop()

        date_str = selected_date.strftime("%Y%m%d")
        url = f"{ESPN_SCOREBOARD}?dates={date_str}&limit=50"

        try:
            resp = requests.get(url, headers=HEADERS, timeout=10)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            st.error(f"Failed to fetch schedule: {e}")
            st.stop()

        events = data.get("events", [])

        games = []

        for event in events:

            game_id = event.get("id", "N/A")

            competitions = event.get("competitions", [{}])
            comp = competitions[0] if competitions else {}

            # time
            date_raw = comp.get("date", "")
            et_dt = convert_to_et(date_raw)
            if not et_dt:
                continue

            time_str = et_dt.strftime("%H:%M ET")

            # teams
            competitors = comp.get("competitors", [])

            home = None
            away = None

            for c in competitors:
                name = c.get("team", {}).get("abbreviation", "?")
                if c.get("homeAway") == "home":
                    home = name
                else:
                    away = name

            if not home or not away:
                continue

            games.append({
                "gameId": game_id,
                "teams": f"{away} @ {home}",
                "time": time_str,
                "sort_time": et_dt.strftime("%H:%M")
            })

        if games:
            for game in sorted(games, key=lambda x: x["sort_time"]):
                st.write(f"{game['gameId']} | 🏀 {game['teams']} | 🕒 {game['time']}")
        else:
            st.warning("No games found for selected date")

# =========================
# MODE 2 — GAME FEED
# =========================
if mode == "Game Feed":

    game_id = st.text_input("Enter Game ID", "")

    USE_QUARTER_FILTER = st.checkbox("Filter by Quarter", value=False)
    TARGET_QUARTERS = []

    if USE_QUARTER_FILTER:
        TARGET_QUARTERS = st.multiselect(
            "Select Quarters",
            [1, 2, 3, 4, "OT"],
            default=[1]
        )

    USE_TIME_FILTER = st.checkbox("Filter by Actual Time (ET)", value=False)

    et_now = datetime.now(ZoneInfo("America/New_York"))
    today_start = et_now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = et_now.replace(hour=23, minute=59, second=0, microsecond=0)

    if "start_time" not in st.session_state:
        st.session_state.start_time = today_start.strftime("%Y-%m-%d %H:%M")
    if "end_time" not in st.session_state:
        st.session_state.end_time = today_end.strftime("%Y-%m-%d %H:%M")

    START_TIME = None
    END_TIME = None

    if USE_TIME_FILTER:
        START_TIME = st.text_input("Start Time (YYYY-MM-DD HH:MM)", st.session_state.start_time)
        END_TIME = st.text_input("End Time (YYYY-MM-DD HH:MM)", st.session_state.end_time)

    if st.button("Load Game Feed"):

        if not game_id.strip():
            st.error("Please enter a valid ESPN Game ID.")
            st.stop()

        # Fetch play-by-play via ESPN summary endpoint (most reliable for PBP)
        url = f"{ESPN_SUMMARY}?event={game_id.strip()}"

        try:
            resp = requests.get(url, headers=HEADERS, timeout=10)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            st.error(f"Failed to fetch game data: {e}")
            st.stop()

        # ── Game header ──────────────────────────────────────────
        header = data.get("header", {})
        competitions = header.get("competitions", [{}])
        comp = competitions[0] if competitions else {}

        competitors = comp.get("competitors", [])
        team_labels = {}
        for c in competitors:
            team_labels[c.get("homeAway")] = c.get("team", {}).get("abbreviation", "?")

        away_abbr = team_labels.get("away", "Away")
        home_abbr = team_labels.get("home", "Home")

        status = comp.get("status", {})
        status_detail = status.get("type", {}).get("detail", "")

        # ── Play-by-play ─────────────────────────────────────────
        plays_raw = data.get("plays", [])

        if not plays_raw:
            st.warning("No play-by-play data available for this game yet.")
            st.stop()

        # Parse time filters
        START_DT = None
        END_DT = None
        if USE_TIME_FILTER and START_TIME and END_TIME:
            try:
                START_DT = datetime.fromisoformat(START_TIME).replace(tzinfo=ZoneInfo("America/New_York"))
                END_DT = datetime.fromisoformat(END_TIME).replace(tzinfo=ZoneInfo("America/New_York"))
            except Exception:
                st.error("Invalid time format. Use YYYY-MM-DD HH:MM")
                st.stop()

        events = []

        for play in plays_raw:

            period_obj = play.get("period", {})
            period = period_obj.get("number", 0)
            clock_display = play.get("clock", {}).get("displayValue", "")
            desc = play.get("text", "No description")
            score_home = play.get("homeScore", "-")
            score_away = play.get("awayScore", "-")
            wall_clock_raw = play.get("wallclock", "")

            actual_dt = convert_to_et(wall_clock_raw) if wall_clock_raw else None

            # Quarter filter
            if USE_QUARTER_FILTER and TARGET_QUARTERS:
                if period >= 5:
                    if "OT" not in TARGET_QUARTERS:
                        continue
                else:
                    if period not in TARGET_QUARTERS:
                        continue

            # Time filter
            if USE_TIME_FILTER and actual_dt and START_DT and END_DT:
                if not (START_DT <= actual_dt <= END_DT):
                    continue

            events.append({
                "period": period,
                "clock": clock_display,
                "desc": desc,
                "score": f"{away_abbr} {score_away} – {home_abbr} {score_home}",
                "time": convert_to_et_str(wall_clock_raw) if wall_clock_raw else None,
                "type": play.get("type", {}).get("text", ""),
            })

        # ── Output ───────────────────────────────────────────────
        if not events:
            st.warning("No events matched the selected filters.")
        else:
            for e in events:
                label = f"🔥 OT" if e["period"] >= 5 else f"🏀 Q{e['period']}"

                st.write(f"**{label} | ⏱️ {e['clock']}**")
                st.write(f"📊 Score: {e['score']}")
                if e["type"]:
                    st.write(f"🏷️ Type: {e['type']}")
                st.write(f"📌 {e['desc']}")
                if e["time"]:
                    st.success(f"🕒 {e['time']}")
                st.markdown("---")

            st.success(f"Loaded {len(events)} events.")
