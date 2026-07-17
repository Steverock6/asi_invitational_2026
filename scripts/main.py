"""
The main weekly job. This is the one script GitHub Actions will run
every Tuesday morning (and that you can run manually any time).

It pulls fresh data from ESPN, computes power rankings, detects
newsworthy events, generates AI news stories, and writes it all into a
single data/data.json file for the website to read.
"""

import json
import os
from datetime import datetime, timezone

from espn_connector import get_league
from power_rankings import calculate_power_rankings
from news_events import get_weekly_events
from news_generator import generate_news_stories
from custom_stories import ensure_file_exists, load_custom_events, clear_custom_events
from rankings_history import load_previous_rankings, save_current_rankings

OUTPUT_PATH = "data/data.json"


def build_standings(league):
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
        }
        for i, t in enumerate(standings)
    ]


def build_news(auto_events, custom_events):
    """Generates AI stories for both auto-detected and custom events, tagged by source."""
    all_events = auto_events + custom_events
    if not all_events:
        return []

    stories = generate_news_stories(all_events)

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
    rankings_df = calculate_power_rankings(league)
    previous_rankings_df = load_previous_rankings()

    print("Detecting newsworthy events...")
    auto_events = get_weekly_events(league, rankings_df, previous_rankings_df)
    print(f"  Found {len(auto_events)} automatic event(s).")

    ensure_file_exists()
    custom_events = load_custom_events()
    print(f"  Found {len(custom_events)} custom story idea(s).")

    print("Generating news stories...")
    news = build_news(auto_events, custom_events)
    print(f"  Generated {len(news)} stories.")

    print("Building standings...")
    standings = build_standings(league)

    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "league_name": league.settings.name,
        "current_week": league.current_week,
        "standings": standings,
        "power_rankings": rankings_df.to_dict(orient="records"),
        "news": news,
        "transactions": [],  # placeholder until the transactions module is revisited
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
