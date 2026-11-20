"""
Lists every tied game in league history (regular season and playoffs).
Run it from the Actions tab: "Find Ties" -> Run workflow. Results print in the log.
"""

import os
from espn_api.football import League

FIRST_YEAR = 2016
LAST_YEAR = int(os.environ["LEAGUE_YEAR"])


def schedule(league):
    data = league.espn_request.league_get(params={"view": "mMatchupScore"})
    if isinstance(data, list):
        data = data[0] if data else {}
    return data.get("schedule") or []


total = 0
for year in range(FIRST_YEAR, LAST_YEAR + 1):
    try:
        league = League(league_id=int(os.environ["LEAGUE_ID"]), year=year,
                        espn_s2=os.environ["ESPN_S2"], swid=os.environ["ESPN_SWID"])
    except Exception as e:
        print(f"{year}: couldn't load ({e})")
        continue
    names = {t.team_id: t.team_name for t in league.teams}
    ties = []
    for g in schedule(league):
        home, away = g.get("home") or {}, g.get("away") or {}
        hp, ap = home.get("totalPoints", 0), away.get("totalPoints", 0)
        if not away or (hp == 0 and ap == 0):
            continue  # bye week or not played yet
        if g.get("winner") == "TIE" or hp == ap:
            ties.append(f"  Week {g.get('matchupPeriodId')}: {names.get(home.get('teamId'), '?')} "
                        f"{hp} - {ap} {names.get(away.get('teamId'), '?')}")
    print(f"{year}: {len(ties)} tie(s)")
    print("\n".join(ties)) if ties else None
    total += len(ties)

print(f"\nTOTAL TIES {FIRST_YEAR}-{LAST_YEAR}: {total}")
