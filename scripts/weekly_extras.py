"""
The fun stuff layered on top of the core weekly data:

- weekly awards (Top Dog, Bad Beat, Bench Blunder, ...)
- this week's matchups with DonBot's picks, plus his season pick record
- richer standings (streak, luck, playoff line, defending-champ crown)
- extra news events for DonBot to post about (streaks, trades, big FAAB bids)

Everything here only reports facts pulled from ESPN. Each builder is called
from main.py inside its own safety net, so a hiccup in one of these can't
take down the rest of the weekly update.
"""

import json
import math
import os
import statistics

from power_rankings import _scoreboard, completed_weeks

CHAMPIONS_PATH = "data/champions.json"
BENCH_SLOTS = {"BE", "IR"}
STREAK_MIN = 3          # win/loss streaks this long get a DonBot post
BIG_BID_MIN = 15        # FAAB bids this size get a DonBot post
RECENT_DAYS = 7


def _played(matchup):
    return getattr(matchup, "home_team", None) is not None and getattr(matchup, "away_team", None) is not None


# ---------------------------------------------------------------- awards

def build_awards(league, week):
    """Superlatives for one completed week. Returns a list of award dicts."""
    games = [m for m in _scoreboard(league, week) if _played(m)]
    if not games:
        return []

    results = []  # one row per team
    for m in games:
        for team, pts, opp, opp_pts in (
            (m.home_team, m.home_score, m.away_team, m.away_score),
            (m.away_team, m.away_score, m.home_team, m.home_score),
        ):
            results.append({"team": team.team_name, "pts": pts, "opp": opp.team_name, "opp_pts": opp_pts})

    def award(key, title, blurb, team, value, detail):
        return {"key": key, "title": title, "blurb": blurb, "team": team, "value": value, "detail": detail}

    def score_line(r):
        verb = "beat" if r["pts"] > r["opp_pts"] else "lost to"
        return f"{verb} {r['opp']} {r['pts']:.2f}-{r['opp_pts']:.2f}"

    awards = []
    top = max(results, key=lambda r: r["pts"])
    low = min(results, key=lambda r: r["pts"])
    awards.append(award("top_dog", "Top Dog", "Highest score of the week", top["team"], f"{top['pts']:.2f} pts", score_line(top)))
    awards.append(award("low_energy", "Low Energy", "Lowest score of the week", low["team"], f"{low['pts']:.2f} pts", score_line(low)))

    winners = [r for r in results if r["pts"] > r["opp_pts"]]
    losers = [r for r in results if r["pts"] < r["opp_pts"]]
    if winners:
        blow = max(winners, key=lambda r: r["pts"] - r["opp_pts"])
        close = min(winners, key=lambda r: r["pts"] - r["opp_pts"])
        lucky = min(winners, key=lambda r: r["pts"])
        awards.append(award("blowout", "Biggest Beatdown", "Largest margin of victory", blow["team"],
                            f"+{blow['pts'] - blow['opp_pts']:.2f}", score_line(blow)))
        awards.append(award("photo_finish", "Photo Finish", "Closest game of the week", close["team"],
                            f"+{close['pts'] - close['opp_pts']:.2f}", score_line(close)))
        awards.append(award("lucky_duck", "Lucky Duck", "Lowest score that still won", lucky["team"],
                            f"{lucky['pts']:.2f} pts", score_line(lucky)))
    if losers:
        beat = max(losers, key=lambda r: r["pts"])
        outscored = sum(1 for r in results if r["pts"] < beat["pts"])
        awards.append(award("bad_beat", "Bad Beat", "Highest score that still lost", beat["team"],
                            f"{beat['pts']:.2f} pts", f"outscored {outscored} of {len(results) - 1} other teams and {score_line(beat)}"))

    # Player-level awards need box scores, which are a separate ESPN request.
    try:
        starters, bench = [], []
        for box in league.box_scores(week):
            for team, lineup in ((box.home_team, box.home_lineup), (box.away_team, box.away_lineup)):
                if not hasattr(team, "team_name"):
                    continue
                for p in lineup or []:
                    row = {"team": team.team_name, "player": p.name, "pts": p.points or 0}
                    (bench if p.slot_position in BENCH_SLOTS else starters).append(row)
        if starters:
            mvp = max(starters, key=lambda r: r["pts"])
            awards.append(award("mvp", "League MVP", "Top-scoring starter", mvp["team"],
                                f"{mvp['pts']:.2f} pts", mvp["player"]))
        if bench:
            b = max(bench, key=lambda r: r["pts"])
            awards.append(award("bench_blunder", "Bench Blunder", "Most points left on the bench by one player",
                                b["team"], f"{b['pts']:.2f} pts", f"{b['player']} rode the bench"))
    except Exception as e:
        print(f"  Note: skipping player awards ({e}).")

    return awards


def award_events(awards, week):
    """Turns a few of the awards into news events for DonBot."""
    wanted = {"top_dog": "weekly_high_score", "low_energy": "weekly_low_score",
              "bad_beat": "bad_beat", "bench_blunder": "bench_blunder", "mvp": "player_of_the_week"}
    return [
        {"type": wanted[a["key"]], "week": week, "team": a["team"], "stat": a["value"], "detail": a["detail"]}
        for a in awards if a["key"] in wanted
    ]


# ---------------------------------------------------------------- DonBot's picks

def _win_prob(team_a, team_b, through, all_teams):
    """Chance team_a outscores team_b, from each team's scoring average through `through` weeks."""
    if through <= 0:
        return 0.5
    mean = lambda t: sum(t.scores[:through]) / through
    every_score = [s for t in all_teams for s in t.scores[:through]]
    spread = statistics.pstdev(every_score) if len(every_score) > 1 else 0
    spread = max(spread, 10.0)
    z = (mean(team_a) - mean(team_b)) / (spread * math.sqrt(2))
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def pick_record(league):
    """How DonBot's picks (the team with the better scoring average) have done so far this season."""
    wins = losses = 0
    for week in range(2, completed_weeks(league) + 1):
        for m in _scoreboard(league, week):
            if not _played(m) or m.home_score == m.away_score:
                continue
            p = _win_prob(m.home_team, m.away_team, week - 1, league.teams)
            if p == 0.5:
                continue
            pick_home = p > 0.5
            if pick_home == (m.home_score > m.away_score):
                wins += 1
            else:
                losses += 1
    return {"wins": wins, "losses": losses}


def build_matchups(league, week, rank_by_team):
    """The slate for `week` (the next one to be played) with DonBot's pick for each game."""
    reg = league.settings.reg_season_count
    through = completed_weeks(league)
    games = []
    for m in _scoreboard(league, week):
        if not _played(m):
            continue
        if week > reg and getattr(m, "matchup_type", None) != "WINNERS_BRACKET":
            continue
        p_home = _win_prob(m.home_team, m.away_team, through, league.teams)
        side = lambda t, p: {
            "team": t.team_name,
            "record": f"{t.wins}-{t.losses}",
            "power_rank": rank_by_team.get(t.team_name),
            "avg": round(sum(t.scores[:through]) / through, 1) if through else None,
            "win_prob": round(p * 100),
        }
        home, away = side(m.home_team, p_home), side(m.away_team, 1 - p_home)
        games.append({
            "home": home,
            "away": away,
            "pick": None if p_home == 0.5 else (home["team"] if p_home > 0.5 else away["team"]),
            "confidence": round(max(p_home, 1 - p_home) * 100),
        })
    games.sort(key=lambda g: g["confidence"])  # closest games first
    return games


def picks_event(matchups, record, week):
    """One news event so DonBot can brag about his record and name a lock of the week."""
    picks = [g for g in matchups if g["pick"]]
    if not picks:
        return None
    lock = max(picks, key=lambda g: g["confidence"])
    other = lock["away"]["team"] if lock["pick"] == lock["home"]["team"] else lock["home"]["team"]
    closest = min(picks, key=lambda g: g["confidence"])
    return {
        "type": "donbot_picks",
        "upcoming_week": week,
        "donbot_season_pick_record": f"{record['wins']}-{record['losses']}",
        "lock_of_the_week": {"pick": lock["pick"], "over": other, "confidence_pct": lock["confidence"]},
        "toss_up_game": [closest["home"]["team"], closest["away"]["team"]],
    }


# ---------------------------------------------------------------- standings

def defending_champion(league):
    """Owner name of last season's champion, from data/champions.json (None if unknown)."""
    if not os.path.exists(CHAMPIONS_PATH):
        return None
    with open(CHAMPIONS_PATH) as f:
        champs = json.load(f)
    years = sorted((int(y) for y in champs if int(y) < int(league.year)), reverse=True)
    return champs[str(years[0])] if years else None


def build_team_info(league, owner_by_team_id):
    """Per-team extras the site uses everywhere: owner, logo, and the champ's crown."""
    champ = (defending_champion(league) or "").strip().lower()
    info = {}
    for t in league.teams:
        owner = owner_by_team_id.get(t.team_id, "")
        logo = getattr(t, "logo_url", "") or ""
        info[t.team_name] = {
            "owner": owner,
            "logo": logo if logo.startswith("https://") else "",
            "champ": bool(champ) and owner.strip().lower() == champ,
        }
    return info


def standings_extras(league, rankings_df):
    """Team name -> streak, luck and FAAB left. Luck = real wins minus the wins their scores 'deserved'."""
    all_play = dict(zip(rankings_df["Team"], rankings_df["All Play %"]))
    budget = getattr(league.settings, "acquisition_budget", 0) or 0
    out = {}
    for t in league.teams:
        games = t.wins + t.losses
        streak_type = (getattr(t, "streak_type", "") or "")[:1].upper()
        streak_len = getattr(t, "streak_length", 0) or 0
        out[t.team_name] = {
            "streak": f"{streak_type}{streak_len}" if streak_type in ("W", "L") and streak_len else "",
            "luck": round(t.wins - all_play.get(t.team_name, 0) * games, 1) if games else 0,
            "faab_left": (budget - (getattr(t, "acquisition_budget_spent", 0) or 0)) if budget else None,
        }
    return out


# ---------------------------------------------------------------- more news for DonBot

def streak_events(league):
    events = []
    for t in league.teams:
        kind = (getattr(t, "streak_type", "") or "").upper()
        length = getattr(t, "streak_length", 0) or 0
        if length >= STREAK_MIN and kind in ("WIN", "LOSS"):
            events.append({"type": "win_streak" if kind == "WIN" else "losing_streak",
                           "team": t.team_name, "games_in_a_row": length, "record": f"{t.wins}-{t.losses}"})
    events.sort(key=lambda e: e["games_in_a_row"], reverse=True)
    return events[:2]


def transaction_events(moves, now_ms):
    """Trades from the past week, plus the single biggest waiver bid."""
    cutoff = now_ms - RECENT_DAYS * 24 * 3600 * 1000
    recent = [m for m in moves if (m.get("date") or 0) >= cutoff]
    events = []
    for m in recent:
        if m["kind"] == "TRADE":
            events.append({"type": "trade", "sides": [{"team": s["team"], "receives": s["gets"]} for s in m["sides"]]})
    bids = [m for m in recent if m["kind"] == "WAIVER" and (m.get("bid") or 0) >= BIG_BID_MIN and m.get("adds")]
    if bids:
        big = max(bids, key=lambda m: m["bid"])
        events.append({"type": "big_waiver_bid", "team": big["team"], "player_added": big["adds"][0],
                       "faab_dollars_spent": big["bid"]})
    return events
