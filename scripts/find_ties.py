"""
Lists every tied game in league history (regular season and playoffs), and
counts the games checked each season so you can tell nothing came back short.
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


def is_final(game, hp, ap):
    winner = game.get("winner")
    if winner in ("HOME", "AWAY", "TIE"):
        return True
    return winner is None and (hp or ap)  # some old seasons leave this blank


total_ties = 0
problems = []
for year in range(FIRST_YEAR, LAST_YEAR + 1):
    try:
        league = League(league_id=int(os.environ["LEAGUE_ID"]), year=year,
                        espn_s2=os.environ["ESPN_S2"], swid=os.environ["ESPN_SWID"])
    except Exception as e:
        print(f"{year}: COULDN'T LOAD ({e})")
        problems.append(year)
        continue

    names = {t.team_id: t.team_name for t in league.teams}
    reg_weeks = league.settings.reg_season_count
    per_week = len(league.teams) // 2

    ties, reg_games, playoff_games, weeks_seen = [], 0, 0, set()
    for g in schedule(league):
        home, away = g.get("home") or {}, g.get("away") or {}
        if not away:
            continue  # bye
        hp, ap = home.get("totalPoints", 0), away.get("totalPoints", 0)
        if not is_final(g, hp, ap):
            continue  # not played yet
        week = g.get("matchupPeriodId")
        if week <= reg_weeks:
            reg_games += 1
            weeks_seen.add(week)
        else:
            playoff_games += 1
        if g.get("winner") == "TIE" or hp == ap:
            ties.append(f"    Week {week}: {names.get(home.get('teamId'), '?')} "
                        f"{hp} - {ap} {names.get(away.get('teamId'), '?')}")

    expected = len(weeks_seen) * per_week
    check = "OK" if reg_games == expected and reg_games > 0 else f"CHECK THIS (expected {expected})"
    if check != "OK":
        problems.append(year)
    print(f"{year}: {len(ties)} tie(s) | {reg_games} regular-season games over {len(weeks_seen)} weeks "
          f"[{check}] | {playoff_games} playoff games | {len(league.teams)} teams")
    for line in ties:
        print(line)
    total_ties += len(ties)

print(f"\nTOTAL TIES {FIRST_YEAR}-{LAST_YEAR}: {total_ties}")
print("Every season checked out." if not problems else f"Look closer at: {', '.join(map(str, problems))}")
