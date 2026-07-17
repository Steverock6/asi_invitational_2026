"""
Pulls season-long transaction history (trades, adds, drops) from the league
and formats it into a clean list of dicts, sorted most recent first.

Note: this talks to ESPN's "communication" endpoint directly with the
requests library, rather than using espn_api's built-in recent_activity()
helper, which has proven unreliable for some leagues.
"""

import json
import requests


def _build_url(league):
    return (
        f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{league.year}"
        f"/segments/0/leagues/{league.league_id}/communication/"
    )


def _get_cookies(league):
    return {
        "espn_s2": league.espn_s2,
        "SWID": league.swid,
    }


def _fetch_page(league, size=25, offset=0):
    url = _build_url(league)
    cookies = _get_cookies(league)

    # These numeric IDs are ESPN's internal codes for the message types we
    # care about: waiver adds, free agent adds, drops, and trades.
    message_type_ids = [178, 180, 179, 239, 181, 244]

    filters = {
        "topics": {
            "filterType": {"value": ["ACTIVITY_TRANSACTIONS"]},
            "limit": size,
            "limitPerMessageSet": {"value": 25},
            "offset": offset,
            "sortMessageDate": {"sortPriority": 1, "sortAsc": False},
            "sortFor": {"sortPriority": 2, "sortAsc": False},
            "filterIncludeMessageTypeIds": {"value": message_type_ids},
        }
    }
    headers = {
        "x-fantasy-filter": json.dumps(filters),
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
    }
    params = {"view": "kona_league_communication"}

    response = requests.get(url, params=params, cookies=cookies, headers=headers)
    if response.status_code != 200:
        raise RuntimeError(
            f"ESPN transactions request failed with status {response.status_code}: {response.text[:300]}"
        )
    return response.json()


def _build_player_map(league):
    """Maps ESPN player IDs to readable names using each team's roster."""
    player_map = {}
    for team in league.teams:
        for player in team.roster:
            player_map[player.playerId] = player.name
    return player_map


def get_transactions(league):
    """
    Takes a connected espn_api League object and returns a list of
    transaction dicts covering the whole season so far.
    """
    player_map = _build_player_map(league)
    team_map = {team.team_id: team.team_name for team in league.teams}

    transactions = []
    offset = 0
    page_size = 25
    max_pages = 100

    for _ in range(max_pages):
        data = _fetch_page(league, size=page_size, offset=offset)
        topics = data.get("topics", [])
        if not topics:
            break

        for topic in topics:
            date = topic.get("date")
            msg_type = topic.get("type")
            for item in topic.get("messages", []):
                for_team_id = item.get("for")
                target_id = item.get("targetId")
                trans_type = item.get("type", msg_type)
                transactions.append(
                    {
                        "date": date,
                        "team": team_map.get(for_team_id, "Unknown"),
                        "action_type": trans_type,
                        "player": player_map.get(target_id, f"Player #{target_id}"),
                    }
                )

        offset += page_size

    transactions.sort(key=lambda t: t["date"] or 0, reverse=True)
    return transactions


def group_trades(transactions):
    """
    espn_api logs each side of a trade as separate rows sharing the same
    timestamp. This groups them back into single trade events so we can
    describe "Team A traded Player X to Team B for Player Y" as one story.
    """
    trades_by_date = {}
    for t in transactions:
        if t["action_type"] == "TRADED":
            trades_by_date.setdefault(t["date"], []).append(t)

    grouped = []
    for date, entries in trades_by_date.items():
        grouped.append({"date": date, "entries": entries})

    grouped.sort(key=lambda g: g["date"], reverse=True)
    return grouped


if __name__ == "__main__":
    from espn_connector import get_league

    league = get_league()
    transactions = get_transactions(league)
    print(f"Found {len(transactions)} transactions this season.")
    for t in transactions[:10]:
        print(t)
