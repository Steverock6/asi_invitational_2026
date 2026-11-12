"""
Builds data/records.json: top-5 all-time league records from ESPN.

pip install espn-api

Env vars:
  LEAGUE_ID   required
  START_YEAR  first season of the league (default 2018)
  ESPN_S2, ESPN_SWID   only needed if the league is private
  LEAGUE_YEAR optional, last season to include (default: current year)
  OWNER_NAMES optional JSON, e.g. {"{ABC-123}": "Steve"} to override display names
"""
import json
import os
from collections import defaultdict
from datetime import datetime, timezone

from espn_api.football import League

LEAGUE_ID = int(os.environ["LEAGUE_ID"])
START_YEAR = int(os.environ.get("START_YEAR", 2018))
END_YEAR = int(os.environ.get("LEAGUE_YEAR") or datetime.now().year)
ESPN_S2 = os.environ.get("ESPN_S2")
SWID = os.environ.get("ESPN_SWID") or os.environ.get("SWID")
NAME_OVERRIDES = json.loads(os.environ.get("OWNER_NAMES", "{}"))
TOP_N = 5


def owner_of(team):
    """Stable owner id + display name (team names change every year)."""
    o = team.owners[0] if getattr(team, "owners", None) else None
    if isinstance(o, dict):
        oid = o.get("id", team.team_name)
        name = f"{o.get('firstName', '').strip()} {o.get('lastName', '').strip()}".strip()
    else:
        oid, name = (o or team.team_name), (o or team.team_name)
    return oid, NAME_OVERRIDES.get(oid, name or team.team_name)


def load_rows():
    """Returns (games, seasons). games = one row per team per regular-season week.
    seasons = one row per team per season."""
    games, seasons = [], []
    for year in range(START_YEAR, END_YEAR + 1):
        try:
            league = League(league_id=LEAGUE_ID, year=year, espn_s2=ESPN_S2, swid=SWID)
        except Exception as e:
            print(f"skip {year}: {e}")
            continue
        reg_weeks = league.settings.reg_season_count
        for t in league.teams:
            oid, name = owner_of(t)
            scores = [s for s in t.scores[:reg_weeks]]
            played = [(w + 1, s) for w, s in enumerate(scores) if s > 0]
            if not played:  # season not started / no data
                continue
            for week, pts in played:
                games.append(dict(owner_id=oid, owner=name, team=t.team_name,
                                  season=year, week=week, points=pts))
            seasons.append(dict(owner_id=oid, owner=name, team=t.team_name,
                                season=year, wins=t.wins, points=round(t.points_for, 2)))
        print(f"loaded {year}")
    return games, seasons


def top(rows, key, fmt=lambda v: v, extra=()):
    ranked = sorted(rows, key=lambda r: r[key], reverse=True)[:TOP_N]
    return [dict(rank=i + 1, owner=r["owner"], value=fmt(r[key]),
                 **{k: r[k] for k in extra if k in r}) for i, r in enumerate(ranked)]


def career(seasons, key):
    tot, names = defaultdict(float), {}
    for s in seasons:
        tot[s["owner_id"]] += s[key]
        names[s["owner_id"]] = s["owner"]  # latest name wins
    rows = [dict(owner=names[o], total=round(v, 2)) for o, v in tot.items()]
    return top(rows, "total")


# --- Record registry: add a new record = add one dict here ---------------
def build(games, seasons):
    return [
        dict(id="week_points", title="Most points in a single week", unit="pts",
             entries=top(games, "points", lambda v: round(v, 2), ("season", "week", "team"))),
        dict(id="season_points", title="Most points in a single season", unit="pts",
             entries=top(seasons, "points", extra=("season", "team"))),
        dict(id="season_wins", title="Most wins in a single season", unit="wins",
             entries=top(seasons, "wins", extra=("season", "team"))),
        dict(id="career_wins", title="Most wins all time", unit="wins",
             entries=career(seasons, "wins")),
        dict(id="career_points", title="Most points all time", unit="pts",
             entries=career(seasons, "points")),
    ]


if __name__ == "__main__":
    games, seasons = load_rows()
    out = dict(generated=datetime.now(timezone.utc).isoformat(),
               seasons=sorted({s["season"] for s in seasons}),
               records=build(games, seasons))
    os.makedirs("data", exist_ok=True)
    with open("data/records.json", "w") as f:
        json.dump(out, f, indent=2)
    print("wrote data/records.json")
