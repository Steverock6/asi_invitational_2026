"""
Connects to the private ESPN fantasy football league.

Credentials are read from environment variables, never hardcoded here.
When run locally, set them in your terminal first. When run in GitHub
Actions, they come from the repo's encrypted Secrets automatically.
"""

import os
from espn_api.football import League


def get_league():
    """
    Reads league_id, year, espn_s2, and swid from environment variables
    and returns a connected espn_api League object.
    """
    league_id = os.environ.get("LEAGUE_ID")
    year = os.environ.get("LEAGUE_YEAR")
    espn_s2 = os.environ.get("ESPN_S2")
    swid = os.environ.get("ESPN_SWID")

    missing = [
        name
        for name, value in [
            ("LEAGUE_ID", league_id),
            ("LEAGUE_YEAR", year),
            ("ESPN_S2", espn_s2),
            ("ESPN_SWID", swid),
        ]
        if not value
    ]
    if missing:
        raise RuntimeError(
            f"Missing required environment variable(s): {', '.join(missing)}. "
            "Set these before running the script."
        )

    league = League(
        league_id=int(league_id),
        year=int(year),
        espn_s2=espn_s2,
        swid=swid,
    )

    # Explicitly attach these so other modules (like transactions.py) can
    # reuse the same credentials without re-reading environment variables.
    league.espn_s2 = espn_s2
    league.swid = swid

    return league


if __name__ == "__main__":
    # Quick manual test: run "python scripts/espn_connector.py" after
    # setting the environment variables, and it will print your teams.
    league = get_league()
    print(f"Connected to league: {league.settings.name}")
    for team in league.teams:
        print(f"  {team.team_id}: {team.team_name} ({team.wins}-{team.losses})")
