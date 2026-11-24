"""
The main weekly job. This is the one script GitHub Actions will run
every Tuesday morning (and that you can run manually any time).

It pulls fresh data from ESPN, computes power rankings, detects
newsworthy events, generates AI news stories, and writes it all into a
single data/data.json file for the website to read.
"""

import json
import math
import os
from datetime import datetime, timezone

from espn_connector import get_league
from power_rankings import calculate_rankings_with_trends, last_completed_week
from news_events import get_weekly_events
from news_generator import generate_news_stories
from custom_stories import ensure_file_exists, load_custom_events, clear_custom_events
from rankings_history import save_current_rankings
from transactions import get_transactions, _owner_by_team_id
import weekly_extras as extras
from playoff_odds import build_playoff_race, playoff_race_event

OUTPUT_PATH = "data/data.json"
TRANSACTIONS_LIMIT = 40  # most recent N moves shown on the site
MAX_AUTO_STORIES = 10    # DonBot posts per week, not counting your custom ideas and his picks

# When there's more news than room, DonBot covers these first.
STORY_PRIORITY = [
    "trade", "playoff_stakes", "playoff_picture", "upset", "blowout", "nail_biter", "weekly_high_score",
    "weekly_low_score", "bad_beat", "win_streak", "losing_streak", "big_waiver_bid",
    "bench_blunder", "rank_jump", "rank_drop", "player_of_the_week",
]


def safely(label, fn, fallback):
    """Runs one of the nice-to-have builders; a failure just leaves that section empty."""
    try:
        return fn()
    except Exception as e:
        print(f"  Warning: {label} unavailable this run ({e}).")
        return fallback


def pick_stories(events):
    """Keeps the most newsworthy events when a week produces more than MAX_AUTO_STORIES."""
    order = {t: i for i, t in enumerate(STORY_PRIORITY)}
    ranked = sorted(events, key=lambda e: order.get(e.get("type"), len(order)))
    return ranked[:MAX_AUTO_STORIES]


def build_standings(league, more):
    """Returns a simple list of team records sorted by wins, then points."""
    standings = sorted(league.teams, key=lambda t: (t.wins, t.points_for), reverse=True)
    return [
        {
            "rank": i + 1,
            "team": t.team_name,
            "abbrev": t.team_abbrev,
            "wins": t.wins,
            "losses": t.losses,
            "points_for": round(t.points_for, 1),
            "points_against": round(t.points_against, 1),
            **more.get(t.team_name, {}),
        }
        for i, t in enumerate(standings)
    ]


def build_transactions(league):
    """
    Returns this season's transactions (adds/drops/trades), most recent first. Failures here (e.g. ESPN's communication endpoint
    hiccups) shouldn't take down the whole weekly job, so this just warns
    and falls back to an empty list rather than raising.
    """
    try:
        transactions = get_transactions(league)
    except Exception as e:
        print(f"  Warning: couldn't fetch transactions ({e}). Leaving transactions empty for this run.")
        return []
    return transactions


def _clean_records(records):
    """Swap NaN for None so data.json stays valid JSON for the browser."""
    return [
        {k: (None if isinstance(v, float) and math.isnan(v) else v) for k, v in r.items()}
        for r in records
    ]


def build_news(auto_events, custom_events):
    """
    Generates AI stories for both auto-detected and custom events, tagged by source.

    Any failure here (bad/missing API key, Anthropic outage, etc.) is caught
    so it can't take down the whole weekly job — standings, power rankings,
    and transactions should still update even when news generation can't.
    """
    all_events = auto_events + custom_events
    if not all_events:
        return []

    try:
        stories = generate_news_stories(all_events)
    except Exception as e:
        print(f"  Warning: news generation failed ({e}). Leaving news empty for this run.")
        return []

    news = []
    for event, story in zip(all_events, stories):
        news.append(
            {
                "headline": story["headline"],
                "body": story["body"],
                "type": event.get("type"),
                "source": "custom" if event.get("type") == "custom_prompt" else "auto",
            }
        )
    return news


def run():
    print("Connecting to ESPN...")
    league = get_league()
    print(f"Connected to league: {league.settings.name}")

    print("Calculating power rankings...")
    # previous = last week's rankings, rebuilt from ESPN results (None in week 1)
    rankings_df, previous_rankings_df = calculate_rankings_with_trends(league)

    week = last_completed_week(league)
    reg_season = league.settings.reg_season_count
    in_regular_season = 1 <= week <= reg_season
    rank_by_team = {t: int(r) for t, r in zip(rankings_df["Team"], rankings_df["Rank"])}

    print("Fetching transactions...")
    all_moves = build_transactions(league)
    print(f"  Found {len(all_moves)} move(s) this season.")

    print("Handing out weekly awards...")
    awards = safely("weekly awards", lambda: extras.build_awards(league, week), []) if week >= 1 else []

    print("Making DonBot's picks...")
    upcoming_week = week + 1
    matchups = safely("matchup preview", lambda: extras.build_matchups(league, upcoming_week, rank_by_team), [])
    record = safely("pick record", lambda: extras.pick_record(league), {"wins": 0, "losses": 0})

    print("Simulating the playoff race...")
    race = safely("playoff race", lambda: build_playoff_race(league), None) if in_regular_season else None

    print("Detecting newsworthy events...")
    auto_events = get_weekly_events(league, rankings_df, previous_rankings_df)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    auto_events += safely("transaction news", lambda: extras.transaction_events(all_moves, now_ms), [])
    if in_regular_season:
        auto_events += extras.award_events(awards, week)
        auto_events += safely("streak news", lambda: extras.streak_events(league), [])
        stakes_event = playoff_race_event(race)
        if stakes_event:
            auto_events.append(stakes_event)
    auto_events = pick_stories(auto_events)
    picks = extras.picks_event(matchups, record, upcoming_week) if upcoming_week <= reg_season else None
    if picks:
        auto_events.append(picks)
    print(f"  Found {len(auto_events)} automatic event(s).")

    ensure_file_exists()
    custom_events = load_custom_events()
    print(f"  Found {len(custom_events)} custom story idea(s).")

    print("Generating news stories...")
    news = build_news(custom_events + auto_events, [])
    print(f"  Generated {len(news)} stories.")

    print("Building standings...")
    more = safely("standings extras", lambda: extras.standings_extras(league, rankings_df), {})
    standings = build_standings(league, more)
    teams = safely("team info", lambda: extras.build_team_info(league, _owner_by_team_id(league)), {})

    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "league_name": league.settings.name,
        "current_week": league.current_week,
        "last_completed_week": week,
        "upcoming_week": upcoming_week,
        "playoff_spots": getattr(league.settings, "playoff_team_count", 0),
        "teams": teams,
        "standings": standings,
        "power_rankings": _clean_records(rankings_df.to_dict(orient="records")),
        "news": news,
        "awards": awards,
        "matchups": matchups,
        "donbot_record": record,
        "transactions": all_moves[:TRANSACTIONS_LIMIT],
        "playoff_race": race,
    }

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(data, f, indent=2, default=str)
    print(f"Wrote {OUTPUT_PATH}")

    # Only update history/clear custom ideas after everything above
    # succeeded, so a failed run doesn't lose this week's context.
    save_current_rankings(rankings_df)
    clear_custom_events()
    print("Done.")


if __name__ == "__main__":
    run()
