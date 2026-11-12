"""
Builds data/records.json: top-5 all-time league records from ESPN.

pip install espn-api

Env vars:
  LEAGUE_ID   required
  START_YEAR  first season of the league (default 2018)
  ESPN_S2, ESPN_SWID   only needed if the league is private
  LEAGUE_YEAR optional, last season to include (default: current year)
  OWNER_NAMES optional JSON, e.g. {"{ABC-123}": "Steve"} to override display names
  scripts/owner_names.json  optional file to merge one person who shows up under two
      accounts/names, e.g. {"{OLD-ID}": "Steve Rock", "Stephen Rock": "Steve Rock"}
  Also writes data/owners.json: every person found, with all ids/names/seasons, for review.
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


ALIAS_FILE = "scripts/owner_names.json"
ALIASES = json.load(open(ALIAS_FILE)) if os.path.exists(ALIAS_FILE) else {}
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
            oid, name, raw_id, raw_name = owner_of(t)
            people[oid]["names"].add(raw_name)
            people[oid]["ids"].add(str(raw_id))
            people[oid]["seasons"].add(year)
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
    # One display name per person everywhere (their most recent one), so
    # "Steve Rock" and "Stephen Rock" on the same account never look like two people.
    latest = {r["owner_id"]: r["owner"] for r in seasons}
    for r in games + seasons:
        r["owner"] = latest.get(r["owner_id"], r["owner"])
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
    review = sorted(
        [dict(owner=sorted(v["names"])[-1], names=sorted(v["names"]), ids=sorted(v["ids"]),
              seasons=sorted(v["seasons"])) for v in people.values()],
        key=lambda r: r["owner"].lower())
    with open("data/owners.json", "w") as f:
        json.dump(review, f, indent=2)
    with open("data/records.json", "w") as f:
        json.dump(out, f, indent=2)
    print("wrote data/records.json")
