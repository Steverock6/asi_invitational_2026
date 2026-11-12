"""
Builds data/records.json: top-5 all-time league records from ESPN.

pip install espn-api

Env vars:
  LEAGUE_ID   required
  START_YEAR  first season of the league (default 2018)
  LEAGUE_YEAR optional, last season to include (default: current year)
  ESPN_S2, ESPN_SWID   only needed if the league is private
  OWNER_NAMES optional JSON, e.g. {"{ABC-123}": "Steve"} to override display names

Optional files (both live in scripts/):
  owner_names.json  merge one person who shows up under two accounts/names,
                    e.g. {"{OLD-ID}": "Steve Rock", "Stephen Rock": "Steve Rock"}
  champions.json    only if a champion comes out wrong, e.g. {"2019": "Steve Rock"}

Also writes data/owners.json, data/champions.json and data/playoff_games.json so you can sanity-check the results.
  MIN_PLAYOFF_GAMES optional, minimum playoff games to qualify for the playoff win % record (default 3)

Game-level records use regular-season games only. Streaks run across seasons.
Championships count only finished seasons (a season is finished once the calendar year passes it).
"""
import json
import os
from collections import defaultdict
from datetime import datetime, timezone

from espn_api.football import League

LEAGUE_ID = int(os.environ["LEAGUE_ID"])
START_YEAR = int(os.environ.get("START_YEAR", 2018))
NOW = datetime.now()
END_YEAR = int(os.environ.get("LEAGUE_YEAR") or NOW.year)
ESPN_S2 = os.environ.get("ESPN_S2")
SWID = os.environ.get("ESPN_SWID") or os.environ.get("SWID")
NAME_OVERRIDES = json.loads(os.environ.get("OWNER_NAMES", "{}"))
TOP_N = 5
MIN_PLAYOFF_GAMES = int(os.environ.get("MIN_PLAYOFF_GAMES", 3))  # keeps a lone 1-0 off the list


def _load(path):
    return json.load(open(path)) if os.path.exists(path) else {}


ALIASES = _load("scripts/owner_names.json")
CHAMP_OVERRIDES = {int(k): v for k, v in _load("scripts/champions.json").items()}
people = defaultdict(lambda: {"names": set(), "ids": set(), "seasons": set()})


def owner_of(team):
    """Returns (key, display_name, raw_id, raw_name).
    key is what records group on: a manual alias if you set one, else ESPN's member id
    (team names change every year, so they are never used when an id exists)."""
    o = team.owners[0] if getattr(team, "owners", None) else None
    if isinstance(o, dict):
        raw_id = o.get("id", team.team_name)
        raw_name = f"{o.get('firstName', '').strip()} {o.get('lastName', '').strip()}".strip()
    else:
        raw_id = raw_name = (o or team.team_name)
    raw_name = NAME_OVERRIDES.get(raw_id, raw_name) or team.team_name
    canon = ALIASES.get(raw_id) or ALIASES.get(raw_name)
    if canon:
        return canon, canon, raw_id, raw_name
    return raw_id, raw_name, raw_id, raw_name


def get_playoffs(league, year, key_by_team_id):
    """Championship-bracket games only (no consolation games, no byes)."""
    try:
        data = league.espn_request.league_get(params={"view": "mMatchupScore"})
        if isinstance(data, list):
            data = data[0]
        rows = []
        for m in data.get("schedule", []):
            if m.get("playoffTierType") != "WINNERS_BRACKET":
                continue
            home, away = m.get("home"), m.get("away")
            if not home or not away or m.get("winner") not in ("HOME", "AWAY"):
                continue  # bye week, unplayed, or tied
            h_id, a_id = key_by_team_id.get(home["teamId"]), key_by_team_id.get(away["teamId"])
            if h_id is None or a_id is None:
                continue
            h_won = m["winner"] == "HOME"
            hp, ap = home.get("totalPoints", 0), away.get("totalPoints", 0)
            wk = m.get("matchupPeriodId")
            rows.append(dict(owner_id=h_id, season=year, week=wk, won=h_won, opp_id=a_id, pts=hp, opp_pts=ap))
            rows.append(dict(owner_id=a_id, season=year, week=wk, won=not h_won, opp_id=h_id, pts=ap, opp_pts=hp))
        if not rows:
            print(f"  no playoff games found for {year}")
        return rows
    except Exception as e:
        print(f"  playoff data unavailable for {year}: {e}")
        return []


def load_rows():
    """games   = one row per team per regular-season week (with opponent's score)
    seasons = one row per team per season"""
    games, seasons, playoffs = [], [], []
    for year in range(START_YEAR, END_YEAR + 1):
        try:
            league = League(league_id=LEAGUE_ID, year=year, espn_s2=ESPN_S2, swid=SWID)
        except Exception as e:
            print(f"skip {year}: {e}")
            continue
        reg_weeks = league.settings.reg_season_count
        key_by_team_id = {t.team_id: owner_of(t)[0] for t in league.teams}
        playoffs.extend(get_playoffs(league, year, key_by_team_id))
        for t in league.teams:
            key, name, raw_id, raw_name = owner_of(t)
            scores = list(t.scores[:reg_weeks])
            if not any(s > 0 for s in scores):  # season not started / no data
                continue
            people[key]["names"].add(raw_name)
            people[key]["ids"].add(str(raw_id))
            people[key]["seasons"].add(year)
            for w, pts in enumerate(scores):
                if pts <= 0:
                    continue
                try:
                    opp = t.schedule[w]
                    opp_pts = opp.scores[w]
                    opp_key = owner_of(opp)[0]
                except (IndexError, AttributeError):
                    opp_pts, opp_key = None, None
                if opp_pts is not None and opp_pts <= 0:
                    opp_pts = None
                games.append(dict(owner_id=key, owner=name, team=t.team_name, season=year,
                                  week=w + 1, points=pts, opp_id=opp_key, opp_points=opp_pts))
            seasons.append(dict(owner_id=key, owner=name, team=t.team_name, season=year,
                                wins=t.wins, points=round(t.points_for, 2),
                                final_standing=getattr(t, "final_standing", 0) or 0,
                                complete=NOW.year > year))
        print(f"loaded {year}")
    # One display name per person everywhere (their most recent), so
    # "Steve Rock" and "Stephen Rock" on the same account never look like two people.
    latest = {r["owner_id"]: r["owner"] for r in seasons}
    for r in games + seasons:
        r["owner"] = latest.get(r["owner_id"], r["owner"])
    for g in games:
        g["opp_owner"] = latest.get(g["opp_id"], "Unknown")
    for p in playoffs:
        p["owner"] = latest.get(p["owner_id"], "Unknown")
        p["opp_owner"] = latest.get(p["opp_id"], "Unknown")
    return games, seasons, playoffs


def top(rows, key, fmt=lambda v: v, extra=(), reverse=True):
    """Top N rows by `key`. Equal values share a rank."""
    ranked = sorted(rows, key=lambda r: r[key], reverse=reverse)[:TOP_N]
    out, prev, rank = [], None, 0
    for i, r in enumerate(ranked):
        if r[key] != prev:
            rank, prev = i + 1, r[key]
        out.append(dict(rank=rank, owner=r["owner"], value=fmt(r[key]),
                        **{k: r[k] for k in extra if k in r}))
    return out


def career(seasons, key):
    tot, names = defaultdict(float), {}
    for s in seasons:
        tot[s["owner_id"]] += s[key]
        names[s["owner_id"]] = s["owner"]
    rows = [dict(owner=names[o], total=round(v, 2)) for o, v in tot.items()]
    return top(rows, "total")


def decided_games(games):
    """Winner's-eye view of each game (one row per game, ties skipped)."""
    out = []
    for g in games:
        if g["opp_points"] is not None and g["points"] > g["opp_points"]:
            out.append(dict(g, margin=round(g["points"] - g["opp_points"], 2),
                            team=f"over {g['opp_owner']} ({g['points']:.2f}\u2013{g['opp_points']:.2f})"))
    return out


def win_streaks(games):
    by_owner = defaultdict(list)
    for g in games:
        if g["opp_points"] is not None:
            by_owner[g["owner_id"]].append(g)
    label = lambda g: f"{g['season']} Wk {g['week']}"
    rows = []
    for gs in by_owner.values():
        gs.sort(key=lambda g: (g["season"], g["week"]))
        best, cur, cur_start, best_start, best_end = 0, 0, None, None, None
        for g in gs:
            if g["points"] > g["opp_points"]:
                if cur == 0:
                    cur_start = g
                cur += 1
                if cur > best:
                    best, best_start, best_end = cur, cur_start, g
            else:
                cur = 0
        if best:
            rows.append(dict(owner=gs[0]["owner"], streak=best,
                             team=label(best_start) if best == 1 else f"{label(best_start)} to {label(best_end)}"))
    return top(rows, "streak", extra=("team",))


def champion_rows(seasons):
    out = []
    for s in seasons:
        if s["season"] in CHAMP_OVERRIDES:
            if s["owner"] == CHAMP_OVERRIDES[s["season"]]:
                out.append(s)
        elif s["complete"] and s["final_standing"] == 1:
            out.append(s)
    return out


def championships(seasons):
    years = defaultdict(list)
    for s in champion_rows(seasons):
        years[s["owner"]].append(s["season"])
    rows = [dict(owner=o, titles=len(y), team=", ".join(str(v) for v in sorted(y)))
            for o, y in years.items()]
    rows.sort(key=lambda r: r["team"].split(", ")[-1], reverse=True)  # newest title breaks ties
    return top(rows, "titles", extra=("team",))


def playoff_record(playoffs):
    """Playoff win % (championship bracket). Ties on win % go to the person with more wins."""
    tally = defaultdict(lambda: [0, 0])
    names = {}
    for p in playoffs:
        tally[p["owner_id"]][0 if p["won"] else 1] += 1
        names[p["owner_id"]] = p["owner"]
    rows = []
    for oid, (w, l) in tally.items():
        if w + l >= MIN_PLAYOFF_GAMES:
            rows.append(dict(owner=names[oid], sort=(w / (w + l), w), team=f"{w}-{l}"))
    return top(rows, "sort", fmt=lambda v: round(v[0], 3), extra=("team",))


# --- Record registry: add a new record = add one dict here ---------------
def build(games, seasons, playoffs):
    wins = decided_games(games)
    records = [
        dict(id="week_points", title="Most points in a single week", unit="pts",
             entries=top(games, "points", lambda v: round(v, 2), ("season", "week", "team"))),
        dict(id="week_low", title="Fewest points in a single week", unit="pts",
             entries=top(games, "points", lambda v: round(v, 2), ("season", "week", "team"),
                         reverse=False)),
        dict(id="blowout", title="Biggest blowout", unit="pts",
             entries=top(wins, "margin", extra=("season", "week", "team"))),
        dict(id="closest", title="Closest game", unit="pts",
             entries=top(wins, "margin", extra=("season", "week", "team"), reverse=False)),
        dict(id="season_points", title="Most points in a single season", unit="pts",
             entries=top(seasons, "points", extra=("season", "team"))),
        dict(id="season_wins", title="Most wins in a single season", unit="wins",
             entries=top(seasons, "wins", extra=("season", "team"))),
        dict(id="career_wins", title="Most wins all time", unit="wins",
             entries=career(seasons, "wins")),
        dict(id="career_points", title="Most points all time", unit="pts",
             entries=career(seasons, "points")),
        dict(id="win_streak", title="Longest win streak", unit="wins",
             entries=win_streaks(games)),
        dict(id="championships", title="Most championships", unit="titles",
             entries=championships(seasons)),
    ]
    pr = playoff_record(playoffs)
    if pr:
        records.append(dict(id="playoff_pct", title=f"Best playoff win % (min {MIN_PLAYOFF_GAMES} games)",
                            unit="win %", decimals=3, entries=pr))
    return records


if __name__ == "__main__":
    games, seasons, playoffs = load_rows()
    os.makedirs("data", exist_ok=True)
    review = sorted(
        [dict(owner=sorted(v["names"])[-1], names=sorted(v["names"]), ids=sorted(v["ids"]),
              seasons=sorted(v["seasons"])) for v in people.values()],
        key=lambda r: r["owner"].lower())
    with open("data/owners.json", "w") as f:
        json.dump(review, f, indent=2)
    with open("data/champions.json", "w") as f:
        json.dump({str(s["season"]): s["owner"] for s in
                   sorted(champion_rows(seasons), key=lambda s: s["season"])}, f, indent=2)
    with open("data/playoff_games.json", "w") as f:
        json.dump([dict(season=p["season"], week=p["week"], winner=p["owner"], loser=p["opp_owner"],
                        score=f"{p['pts']:.2f}-{p['opp_pts']:.2f}") for p in playoffs if p["won"]],
                  f, indent=2)
    out = dict(generated=datetime.now(timezone.utc).isoformat(),
               seasons=sorted({s["season"] for s in seasons}),
               records=build(games, seasons, playoffs))
    with open("data/records.json", "w") as f:
        json.dump(out, f, indent=2)
    print("wrote data/records.json")
