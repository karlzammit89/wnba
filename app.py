import streamlit as st
import requests
import time
from datetime import datetime, time as dtime, date as ddate, timedelta
from zoneinfo import ZoneInfo

# =========================
# PAGE CONFIG & TITLE
# =========================
st.set_page_config(page_title="WNBA Play by Play", page_icon="🏀", layout="wide")
st.title("🏀 WNBA Play by Play")

# =========================
# CONSTANTS
# =========================
ET = ZoneInfo("America/New_York")

ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard"
ESPN_SUMMARY    = "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/summary"
ESPN_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/122.0.0.0 Safari/537.36",
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         "https://www.espn.com/",
    "Origin":          "https://www.espn.com",
}

# ESPN CDN slug overrides — maps API abbreviation to the correct CDN path.
# Used for any team whose numeric ESPN ID returns a broken logo (e.g. "0").
# Expansion teams and some others need the slug path instead of the numeric ID.
_ABBR_TO_SLUG = {
    "ATL":  "atl",
    "CHI":  "chi",
    "CONN": "conn",
    "DAL":  "dal",
    "GSV":  "gs",        # Golden State Valkyries — https://a.espncdn.com/i/teamlogos/wnba/500/gs.png
    "GS":   "gs",        # Golden State Valkyries — alternate abbreviation returned by ESPN API
    "IND":  "ind",
    "LV":   "lv",
    "LA":   "la",
    "MIN":  "min",
    "NY":   "ny",
    "PHX":  "phx",
    "POR":  "portland",  # Portland Fire — new 2026 expansion
    "SEA":  "sea",
    "TOR":  "tor",       # Toronto Tempo — new 2026 expansion
    "WSH":  "wsh",
}

def wnba_logo(team_id, team_abbr: str = "") -> str:
    """
    Return ESPN CDN logo URL for a WNBA team.
    Tries abbreviation slug first (covers expansion teams + GS whose id=0).
    Also checks all known slug values in case the API abbr varies (e.g. GS vs GSV).
    Falls back to numeric ID for established teams with valid IDs.
    """
    # Try exact abbreviation match first
    abbr_upper = (team_abbr or "").upper()
    slug = _ABBR_TO_SLUG.get(abbr_upper)
    if slug:
        return f"https://a.espncdn.com/i/teamlogos/wnba/500/{slug}.png"

    # If team_id is 0 / falsy, it's almost certainly Golden State — hardcode it
    try:
        if not team_id or int(team_id) == 0:
            return "https://a.espncdn.com/i/teamlogos/wnba/500/gs.png"
    except (ValueError, TypeError):
        return "https://a.espncdn.com/i/teamlogos/wnba/500/gs.png"

    # Fallback: numeric ID (works for established teams with valid IDs)
    return f"https://a.espncdn.com/i/teamlogos/wnba/500/{team_id}.png"

# Scoring play emojis — only shown when score actually changed
SCORING_EMOJI = {
    "three point": "🔥",
    "3-point":     "🔥",
    "three-point": "🔥",
    "dunk":        "💥",
    "layup":       "🟢",
    "jump shot":   "🟢",
    "free throw":  "🎯",
    "jumper":      "🟢",
    "hook":        "🟢",
    "tip shot":    "🟢",
}

# Non-scoring play emojis — always shown
PLAY_EMOJI = {
    "turnover":    "❌",
    "steal":       "🏃",
    "block":       "🚫",
    "rebound":     "🔄",
    "foul":        "🟡",
    "substitution":"🔁",
    "sub ":        "🔁",
    "timeout":     "⏸️",
    "violation":   "🚨",
    "jump ball":   "⬆️",
}

MISS_EMOJI = "🤦"

def _play_emoji(desc: str, is_scoring: bool) -> str:
    d = (desc or "").lower()
    if "miss" in d:
        return MISS_EMOJI
    for k, v in SCORING_EMOJI.items():
        if k in d:
            return v if is_scoring else "🏀"
    for k, v in PLAY_EMOJI.items():
        if k in d:
            return v
    return "🏀"

# =========================
# SESSION STATE INIT
# =========================
for key, default in {
    "selected_game_id":   None,
    "selected_away_abbr": "",
    "selected_home_abbr": "",
    "selected_away_id":   None,
    "selected_home_id":   None,
    "cached_events":      None,
    "cached_game_id":     None,
    "filtered_events":    None,
    "filters_applied":    False,
    "schedule_date":      None,  # None = use today; persists across game navigation
    "last_refresh":       None,  # ET datetime of last manual refresh
    "force_bucket":       0,     # bumped by Refresh to bust this call's cache only
    "selected_event_id":  None,  # ESPN event id for live score lookup
    "selected_away_score":0,     # fallback score (from schedule at entry)
    "selected_home_score":0,
    "sort_newest_first":  False, # render-time sort direction
    # Snapshots of which filters were active at last Apply — drives banners
    "snap_quarter":       False,
    "snap_time":          False,
    "snap_scoring":       False,
    "snap_quarter_labels":[],
    "snap_start_dt":      None,
    "snap_end_dt":        None,
}.items():
    if key not in st.session_state:
        st.session_state[key] = default

# =========================
# HELPERS  (original WNBA logic — unchanged)
# =========================
def convert_to_et(raw_time):
    if not raw_time:
        return None
    try:
        dt = datetime.fromisoformat(str(raw_time).replace("Z", "+00:00"))
        return dt.astimezone(ET).replace(microsecond=0)
    except Exception:
        return None

def convert_to_et_str(raw_time):
    dt = convert_to_et(raw_time)
    if not dt:
        return None
    tz_label = "EDT" if dt.dst() != timedelta(0) else "EST"
    return dt.strftime(f"%Y-%m-%d %H:%M:%S {tz_label}")

def fmt_et(dt) -> str:
    return dt.strftime("%H:%M ET") if dt else "TBD"

def fmt_full_et(dt) -> str:
    if not dt:
        return "N/A"
    label = "EDT" if dt.dst() != timedelta(0) else "EST"
    return dt.strftime(f"%Y-%m-%d %H:%M:%S {label}")

# =========================
# CACHED API CALLS  (original WNBA logic — unchanged)
# =========================
@st.cache_data(ttl=300, show_spinner=False)
def fetch_schedule(date_str: str) -> list:
    """Returns list of parsed game dicts for a given YYYYMMDD date string."""
    url  = f"{ESPN_SCOREBOARD}?dates={date_str}&limit=50"
    resp = requests.get(url, headers=ESPN_HEADERS, timeout=10)
    resp.raise_for_status()
    data   = resp.json()
    events = data.get("events", [])
    games  = []

    for event in events:
        game_id    = event.get("id", "N/A")
        short_name = event.get("shortName", event.get("name", "Unknown"))
        comp       = (event.get("competitions") or [{}])[0]
        status     = comp.get("status", {})
        state      = status.get("type", {}).get("state", "")   # pre / in / post
        detail     = status.get("type", {}).get("detail", "")

        competitors = comp.get("competitors", [])
        away_info = next((c for c in competitors if c.get("homeAway") == "away"), {})
        home_info = next((c for c in competitors if c.get("homeAway") == "home"), {})

        away_abbr  = away_info.get("team", {}).get("abbreviation", "?")
        home_abbr  = home_info.get("team", {}).get("abbreviation", "?")
        away_id    = away_info.get("team", {}).get("id")
        home_id    = home_info.get("team", {}).get("id")
        away_score = away_info.get("score", "0") or "0"
        home_score = home_info.get("score", "0") or "0"

        et_dt    = convert_to_et(comp.get("date", ""))
        time_str = fmt_et(et_dt)

        is_live    = state == "in"
        is_final   = state == "post"
        period     = status.get("period", 0) or 0
        is_ot      = period > 4 and (is_live or is_final)

        if is_live:
            disp_clock = status.get("displayClock", "")
            status_badge = f"LIVE — Q{period} {disp_clock}"
        elif is_final:
            status_badge = "Final"
        else:
            status_badge = "Scheduled"

        games.append({
            "gameId":     game_id,
            "short_name": short_name,
            "away_abbr":  away_abbr,
            "home_abbr":  home_abbr,
            "away_id":    away_id,
            "home_id":    home_id,
            "away_logo":  wnba_logo(away_id, away_abbr),
            "home_logo":  wnba_logo(home_id, home_abbr),
            "away_score": away_score,
            "home_score": home_score,
            "time_str":   time_str,       # always the scheduled tip-off time in ET
            "status_badge": status_badge, # live / final / scheduled label
            "is_live_or_final": is_live or is_final,
            "is_ot":      is_ot,
            "detail":     detail,
        })

    return games


@st.cache_data(ttl=60, show_spinner=False)
def fetch_play_by_play(game_id: str, cache_bucket: int = 0) -> tuple:
    """
    Returns (away_abbr, home_abbr, status_detail, plays_raw).
    Original WNBA ESPN summary fetch — logic unchanged.
    """
    url  = f"{ESPN_SUMMARY}?event={game_id}"
    resp = requests.get(url, headers=ESPN_HEADERS, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    header      = data.get("header", {})
    competitions = header.get("competitions", [{}])
    comp         = competitions[0] if competitions else {}
    competitors  = comp.get("competitors", [])

    team_labels = {}
    team_ids    = {}
    for c in competitors:
        ha = c.get("homeAway")
        team_labels[ha] = c.get("team", {}).get("abbreviation", "?")
        team_ids[ha]    = c.get("team", {}).get("id")

    away_abbr      = team_labels.get("away", "Away")
    home_abbr      = team_labels.get("home", "Home")
    away_id        = team_ids.get("away")
    home_id        = team_ids.get("home")
    status_detail  = comp.get("status", {}).get("type", {}).get("detail", "")
    plays_raw      = data.get("plays", [])

    return away_abbr, home_abbr, away_id, home_id, status_detail, plays_raw


# =========================
# PLAY PARSER
# Stored in session_state — only re-runs when game_id changes
# =========================
def get_events(game_id: str) -> tuple:
    """
    Returns (away_abbr, home_abbr, away_id, home_id, status_detail, events_list).
    Caches parsed events in session_state so filter reruns skip the API.
    """
    if (
        st.session_state.cached_game_id == game_id
        and st.session_state.cached_events is not None
    ):
        return st.session_state.cached_events

    bucket = st.session_state.get("force_bucket", 0)
    away_abbr, home_abbr, away_id, home_id, status_detail, plays_raw = fetch_play_by_play(game_id, cache_bucket=bucket)
    # last_refresh updates only after a real network fetch completes
    st.session_state.last_refresh = datetime.now(ET)

    prev_away = prev_home = 0
    events = []

    for play in plays_raw:
        period_obj    = play.get("period", {})
        period        = period_obj.get("number", 0)
        clock_display = play.get("clock", {}).get("displayValue", "")
        desc          = play.get("text", "No description")
        score_home    = play.get("homeScore", 0) or 0
        score_away    = play.get("awayScore", 0) or 0
        wall_clock_raw = play.get("wallclock", "")
        ptype         = play.get("type", {}).get("text", "")

        try:
            score_away = int(score_away)
            score_home = int(score_home)
        except (ValueError, TypeError):
            score_away = score_home = 0

        is_scoring = (score_away + score_home) > (prev_away + prev_home)
        prev_away, prev_home = score_away, score_home

        action_dt  = convert_to_et(wall_clock_raw) if wall_clock_raw else None
        p_label    = f"OT{period - 4}" if period > 4 else f"Q{period}"

        events.append({
            "period":        period,
            "period_label":  p_label,
            "clock":         clock_display,
            "desc":          desc,
            "away_score":    score_away,
            "home_score":    score_home,
            "score_str":     f"{away_abbr} {score_away} – {home_abbr} {score_home}",
            "is_scoring":    is_scoring,
            "action_dt":     action_dt,
            "action_dt_str": fmt_full_et(action_dt),
            "type":          ptype,
            "emoji":         _play_emoji(desc, is_scoring),
        })

    result = (away_abbr, home_abbr, away_id, home_id, status_detail, events)
    st.session_state.cached_events  = result
    st.session_state.cached_game_id = game_id
    return result


# =========================
# SHARED CSS  (mirrors NBA script)
# =========================
st.markdown("""
<style>
div[data-testid="stVerticalBlockBorderWrapper"] {
    min-height: 150px;
}
.sched-team-row {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 4px;
}
.sched-team-row img {
    width: 34px;
    height: 34px;
    object-fit: contain;
}
.sched-team-name {
    font-size: 22px;
    font-weight: 800;
    letter-spacing: 0.4px;
}
.sched-score {
    font-size: 22px;
    font-weight: 800;
    color: #aaa;
    margin-left: auto;
}
.sched-meta {
    font-size: 13px;
    color: #999;
    margin-top: 4px;
    border-top: 1px solid rgba(255,255,255,0.08);
    padding-top: 5px;
}
.sched-extra {
    display: inline-block;
    background: #e67e22;
    color: #fff;
    font-size: 11px;
    font-weight: 700;
    padding: 1px 6px;
    border-radius: 4px;
    margin-left: 6px;
    vertical-align: middle;
    letter-spacing: 0.5px;
}
</style>
""", unsafe_allow_html=True)


# ======================================================
# GAME FEED VIEW
# ======================================================
if st.session_state.selected_game_id:
    game_id   = st.session_state.selected_game_id

    # Column ratios: [Back, Refresh, Sort, Timestamp, Spacer]
    nav_col1, nav_col2, nav_col3, nav_col4, _ = st.columns([1.3, 1, 1.3, 1.3, 5.1])

    with nav_col1:
        if st.button("⬅ Back to Schedule", use_container_width=True):
            st.session_state.selected_game_id = None
            st.session_state.last_refresh = None  # Clear the old timestamp
            st.rerun()

    with nav_col2:
        if st.button("🔄 Refresh", use_container_width=True):
            # Bump bucket so fetch_play_by_play(game_id, cache_bucket=...) misses
            # the cache for THIS call only — never .clear() (nukes all users).
            st.session_state.force_bucket    = int(time.time() // 30) + 1
            st.session_state.cached_events   = None
            st.session_state.cached_game_id  = None
            st.session_state.filtered_events = None
            st.session_state.filters_applied = False
            st.rerun()

    with nav_col3:
        # Label shows CURRENT state; clicking switches to the other.
        sort_label = ("↑ Newest First" if st.session_state.sort_newest_first
                      else "↓ Oldest First")
        sort_type  = "primary" if st.session_state.sort_newest_first else "secondary"
        if st.button(sort_label, use_container_width=True, type=sort_type):
            st.session_state.sort_newest_first = not st.session_state.sort_newest_first
            st.rerun()

    with nav_col4:
        if st.session_state.last_refresh:
            st.markdown(
                f"""
                <div style="
                    background-color: #2e7d32;
                    color: white;
                    padding: 8px 12px;
                    border-radius: 4px;
                    font-size: 14px;
                    font-weight: bold;
                    width: 100%;
                    text-align: center;
                    display: block;
                    white-space: nowrap;
                ">
                    Last refresh {st.session_state.last_refresh.strftime('%H:%M:%S ET')}
                </div>
                """,
                unsafe_allow_html=True,
            )

    with st.spinner("Loading game data…"):
        away_abbr, home_abbr, away_id, home_id, status_detail, events = get_events(game_id)

    # Live score from the already-cached scoreboard (fetch_schedule TTL=300s)
    # — looked up every render. Falls back to the score stored at game entry
    # if the event isn't found or the fetch throws.
    away_runs = st.session_state.selected_away_score
    home_runs = st.session_state.selected_home_score
    try:
        _board = fetch_schedule(st.session_state.schedule_date.strftime("%Y%m%d"))
        for _g in _board:
            if str(_g["gameId"]) == str(st.session_state.selected_event_id):
                away_runs = _g["away_score"]
                home_runs = _g["home_score"]
                break
    except Exception:
        pass

    # --- Header (mirrors NBA layout) ---
    c1, c2, c3 = st.columns([1, 6, 1])
    with c1:
        st.image(wnba_logo(away_id, away_abbr), width=60)
    with c2:
        st.markdown(
            f"""<div style="display:flex;align-items:center;justify-content:center;
                font-weight:700;font-size:clamp(16px,2.6vw,28px);gap:10px;flex-wrap:wrap;text-align:center;">
                <span>{away_abbr}</span><span style="color:#888;">{away_runs}</span>
                <span>-</span>
                <span style="color:#888;">{home_runs}</span><span>{home_abbr}</span>
            </div>""",
            unsafe_allow_html=True,
        )
    with c3:
        st.image(wnba_logo(home_id, home_abbr), width=60)

    st.divider()

    # --- Filter defaults ---
    all_dts     = [e["action_dt"] for e in events if e["action_dt"]]
    start_def   = min(all_dts) if all_dts else None
    end_def     = max(all_dts) if all_dts else None

    all_periods = sorted(
        {e["period_label"] for e in events},
        key=lambda x: (x.startswith("OT"), int(x[1:]) if x.startswith("Q") else int(x[2:]) + 100)
    )

    # --- Filter checkboxes ---
    USE_QUARTER_FILTER = st.checkbox("🏀 Filter by Quarter / OT", value=False, key="cb_quarter")
    USE_TIME_FILTER    = st.checkbox("🕐 Filter by Actual Time (ET)", value=False, key="cb_time")
    USE_SCORING_FILTER = st.checkbox("🔥 Scoring Plays Only", value=False, key="cb_scoring")

    START_DT = END_DT = None
    selected_quarters  = []

    if USE_QUARTER_FILTER:
        selected_quarters = st.multiselect("Select quarters", options=all_periods, default=[], key="ms_quarter")

    if USE_TIME_FILTER:
        def_start_date = start_def.date() if start_def else ddate.today()
        def_end_date   = end_def.date()   if end_def   else ddate.today()
        def_start_time = start_def.time() if start_def else dtime(19, 0)
        def_end_time   = end_def.time()   if end_def   else dtime(23, 59)

        st.markdown("**Start date/time (ET)**")
        sc1, sc2 = st.columns(2)
        with sc1:
            start_date_input = st.date_input("Start date", value=def_start_date, key="tf_start_date")
        with sc2:
            start_time_input = st.time_input("Start time", value=def_start_time, step=60, key="tf_start_time")

        st.markdown("**End date/time (ET)**")
        ec1, ec2 = st.columns(2)
        with ec1:
            end_date_input = st.date_input("End date", value=def_end_date, key="tf_end_date")
        with ec2:
            end_time_input = st.time_input("End time", value=def_end_time, step=60, key="tf_end_time")

        START_DT = datetime.combine(start_date_input, start_time_input).replace(tzinfo=ET)
        END_DT   = datetime.combine(end_date_input,   end_time_input).replace(tzinfo=ET)

    # --- Action Buttons ---
    btn_col1, btn_col2, _ = st.columns([1.5, 1.5, 7])

    with btn_col1:
        if st.button("🚀 Apply Filters", use_container_width=True):
            def passes(e):
                if USE_QUARTER_FILTER:
                    if not selected_quarters or e["period_label"] not in selected_quarters:
                        return False
                if USE_TIME_FILTER:
                    if not e["action_dt"] or START_DT is None or END_DT is None:
                        return False
                    if not (START_DT <= e["action_dt"] <= END_DT):
                        return False
                if USE_SCORING_FILTER and not e["is_scoring"]:
                    return False
                return True

            st.session_state.filtered_events = [e for e in events if passes(e)]
            st.session_state.filters_applied = True
            # Snapshot exactly which filters were active at Apply time (drives banners)
            st.session_state.snap_quarter        = USE_QUARTER_FILTER
            st.session_state.snap_time           = USE_TIME_FILTER
            st.session_state.snap_scoring        = USE_SCORING_FILTER
            st.session_state.snap_quarter_labels = list(selected_quarters)
            st.session_state.snap_start_dt       = START_DT
            st.session_state.snap_end_dt         = END_DT
            st.rerun()

    with btn_col2:
        def reset_filters():
            st.session_state.filters_applied = False
            st.session_state.filtered_events = None
            st.session_state.snap_quarter        = False
            st.session_state.snap_time           = False
            st.session_state.snap_scoring        = False
            st.session_state.snap_quarter_labels = []
            st.session_state.snap_start_dt       = None
            st.session_state.snap_end_dt         = None
            st.session_state.cb_quarter = False
            st.session_state.cb_time = False
            st.session_state.cb_scoring = False
            if "ms_quarter" in st.session_state:
                st.session_state.ms_quarter = []
            for key in ["tf_start_date", "tf_end_date", "tf_start_time", "tf_end_time"]:
                if key in st.session_state:
                    del st.session_state[key]

        # Change 3: disabled when no filters are active
        st.button("🗑️ Remove Filters", use_container_width=True,
                  on_click=reset_filters,
                  disabled=not st.session_state.get("filters_applied", False))

    # --- Data Selection ---
    filters_active = st.session_state.get("filters_applied", False)
    filtered = st.session_state.get("filtered_events") if filters_active else events
    # Sort reversal happens at render time only — never mutate the stored list.
    # Filters are applied first (above), then the reversal.
    if st.session_state.sort_newest_first:
        filtered = filtered[::-1]

    # --- Info banners — driven from Apply snapshot, not live checkbox state ---
    if filters_active:
        total   = len(events)
        showing = len(filtered)

        if showing == 0:
            st.warning("⚠️ No results found — please check the filters applied.")
            st.stop()

        if st.session_state.snap_quarter:
            labels = st.session_state.snap_quarter_labels or ["none selected"]
            st.info(f"🏀 **Quarter filter:** {', '.join(labels)} — showing **{showing}** of **{total}** plays")

        if st.session_state.snap_time:
            s = st.session_state.snap_start_dt
            e_ = st.session_state.snap_end_dt
            if s and e_:
                st.info(
                    f"🕐 **Time filter:** {s.strftime('%Y-%m-%d %H:%M')} → "
                    f"{e_.strftime('%Y-%m-%d %H:%M')} ET — showing **{showing}** of **{total}** plays"
                )

        if st.session_state.snap_scoring:
            n_scoring = sum(1 for e in events if e["is_scoring"])
            st.info(f"🔥 **Scoring plays filter:** {n_scoring} scoring play(s) in game — showing **{showing}** of **{total}** plays")

    # --- Render loop ---
    for e in filtered:
        st.subheader(f"{e['emoji']} {e['period_label']} | ⏱️ {e['clock']}")

        if e["is_scoring"]:
            st.markdown(f"📊 **Score:** {e['score_str']} &nbsp; 🔥 *Scoring Play!*")
        else:
            st.markdown(f"📊 **Score:** {e['score_str']}")

        if e["type"]:
            st.markdown(f"🏷️ **Type:** {e['type']}")

        st.markdown(f"📋 **Play:** {e['desc']}")
        st.markdown(f"🕐 **Time (ET)** `{e['action_dt_str']}`")

        st.divider()


# ======================================================
# SCHEDULE VIEW
# ======================================================
else:

    # First open: default to today. Returning from game feed: restore last date.
    if st.session_state.schedule_date is None:
        st.session_state.schedule_date = datetime.now(ET).date()

    date = st.date_input(
        "Select date",
        value=st.session_state.schedule_date,
        format="YYYY-MM-DD",
        key="schedule_date_picker",
    )
    # Only update session state when date actually changes — avoids double-click bug
    if date != st.session_state.schedule_date:
        st.session_state.schedule_date = date
        st.rerun()
    date_str = date.strftime("%Y%m%d")
    st.markdown(f"## WNBA Schedule — {date.strftime('%Y-%m-%d')}")

    with st.spinner("Loading schedule…"):
        try:
            games = fetch_schedule(date_str)
        except Exception as e:
            st.error(f"Failed to fetch schedule: {e}")
            st.stop()

    if not games:
        st.info("No WNBA games scheduled for this date.")
        st.stop()

    cols = st.columns(2)
    for i, g in enumerate(games):
        has_started = g["is_live_or_final"]

        btn_label = f"▶  Open  {g['away_abbr']} @ {g['home_abbr']}" if has_started else "⏳ Not Started"
        btn_help  = "View live play-by-play and game summary" if has_started else "Data will be available once the game starts."

        away_score_html = f'<span class="sched-score">{g["away_score"]}</span>' if has_started else ""
        home_score_html = f'<span class="sched-score">{g["home_score"]}</span>' if has_started else ""
        ot_badge        = ' <span class="sched-extra">OT</span>' if g["is_ot"] else ""
        meta = f'{g["time_str"]} &nbsp;·&nbsp; {g["status_badge"]}{ot_badge}'

        inner_html = f"""
<div class="sched-team-row">
  <img src="{g['away_logo']}" />
  <span class="sched-team-name">{g['away_abbr']}</span>
  {away_score_html}
</div>
<div class="sched-team-row">
  <img src="{g['home_logo']}" />
  <span class="sched-team-name">{g['home_abbr']}</span>
  {home_score_html}
</div>
<div class="sched-meta">{meta}</div>
"""

        with cols[i % 2]:
            with st.container(border=True):
                st.markdown(inner_html, unsafe_allow_html=True)
                if st.button(
                    btn_label,
                    key=f"go_{g['gameId']}",
                    use_container_width=True,
                    disabled=not has_started,
                    help=btn_help,
                ):
                    st.session_state.last_refresh    = datetime.now(ET)
                    st.session_state.cached_events   = None
                    st.session_state.cached_game_id  = None
                    st.session_state.filtered_events = None
                    st.session_state.filters_applied = False
                    st.session_state.selected_game_id   = g["gameId"]
                    st.session_state.selected_away_abbr = g["away_abbr"]
                    st.session_state.selected_home_abbr = g["home_abbr"]
                    st.session_state.selected_away_id   = g["away_id"]
                    st.session_state.selected_home_id   = g["home_id"]
                    st.session_state.selected_event_id    = g["gameId"]
                    st.session_state.selected_away_score  = g["away_score"]
                    st.session_state.selected_home_score  = g["home_score"]
                    st.rerun()
