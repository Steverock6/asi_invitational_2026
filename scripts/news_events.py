"""
Scans the most recently completed week for newsworthy events: blowouts,
nail-biters, upset wins, teams that jumped or dropped sharply in the
power rankings, and — during the stretch run — the playoff picture itself.

This module only extracts FACTS (as plain dicts) — no writing happens
here. That separation matters: the AI news generator downstream should
only ever be given these facts and never asked to invent details.

Playoff awareness:
- In the final regular season week(s), a "playoff_picture" event summarizes
  who's in, who's on the bubble.
- Once playoffs start, only games in the winners bracket (matchup_type ==
  "WINNERS_BRACKET") are covered. Consolation/losers bracket games are
  skipped entirely — once a team is eliminated, the news feed moves on.
- Playoff games are tagged with a round name (quarterfinal, semifinal,
  championship) so the AI can frame the story appropriately.
"""

import math

BLOWOUT_MARGIN = 40
NAIL_BITER_MARGIN = 8
RANK_JUMP_THRESHOLD = 3
PLAYOFF_PUSH_WEEKS = 2  # how many final regular season weeks count as "the push"


def get_most_recent_completed_week(league):
    """Returns the last week number that has final scores."""
    current = league.current_week
    for week in range(current, 0, -1):
        matchups = league.scoreboard(week)
        if any(m.home_score or m.away_score for m in matchups):
            return week
    return current


def get_season_phase(league, week):
    """
    Returns one of: "regular_season", "playoff_push", "playoffs", "championship".
    """
    reg_season_weeks = league.settings.reg_season_count
    playoff_team_count = getattr(league.settings, "playoff_team_count", 4)

    # Standard single-elimination bracket: number of rounds to go from
    # playoff_team_count teams down to 1 champion.
    playoff_rounds = max(1, round(math.log2(max(playoff_team_count, 2))))
    championship_week = reg_season_weeks + playoff_rounds

    if week > reg_season_weeks:
        if week >= championship_week:
            return "championship"
        return "playoffs"
    if week > reg_season_weeks - PLAYOFF_PUSH_WEEKS:
        return "playoff_push"
    return "regular_season"


def get_playoff_round_name(league, week):
    """Returns a human label like 'quarterfinal', 'semifinal', 'championship'."""
    reg_season_weeks = league.settings.reg_season_count
    playoff_team_count = getattr(league.settings, "playoff_team_count", 4)
    playoff_rounds = max(1, round(math.log2(max(playoff_team_count, 2))))
    championship_week = reg_season_weeks + playoff_rounds

    rounds_from_championship = championship_week - week
    if rounds_from_championship <= 0:
        return "championship"
    elif rounds_from_championship == 1:
        return "semifinal"
    elif rounds_from_championship == 2:
        return "quarterfinal"
    else:
        return "playoff"


def _is_relevant_matchup(m, phase):
    """
    During playoff weeks, only winners-bracket (still-alive) games count.
    Consolation/losers bracket games are excluded — once a team is
    eliminated, we stop generating stories about them.
    """
    if phase in ("playoffs", "championship"):
        return getattr(m, "matchup_type", None) == "WINNERS_BRACKET"
    return True


def get_matchup_events(league, week, phase):
    """Returns blowout / nail-biter events for a given week."""
    events = []
    matchups = league.scoreboard(week)
    round_name = get_playoff_round_name(league, week) if phase in ("playoffs", "championship") else None

    for m in matchups:
        if not (hasattr(m, "home_team") and hasattr(m, "away_team")):
            continue
        if m.home_team is None or m.away_team is None:
            continue
        if not _is_relevant_matchup(m, phase):
            continue

        home_name = m.home_team.team_name
        away_name = m.away_team.team_name
        home_score = m.home_score
        away_score = m.away_score
        margin = abs(home_score - away_score)

        winner = home_name if home_score > away_score else away_name
        loser = away_name if home_score > away_score else home_name
        winner_score = max(home_score, away_score)
        loser_score = min(home_score, away_score)

        base_event = {
            "week": week,
            "winner": winner,
            "loser": loser,
            "winner_score": winner_score,
            "loser_score": loser_score,
            "margin": round(margin, 1),
            "is_playoff": phase in ("playoffs", "championship"),
            "playoff_round": round_name,
        }

        if margin >= BLOWOUT_MARGIN:
            events.append({**base_event, "type": "blowout"})
        elif margin <= NAIL_BITER_MARGIN:
            events.append({**base_event, "type": "nail_biter"})

    return events


def get_upset_events(league, week, phase, rankings_df):
    """
    Flags games where the lower-ranked (worse power score) team beat the
    higher-ranked team, using this week's power rankings as the basis.
    """
    events = []
    rank_by_team = {row["Team"]: row["Rank"] for _, row in rankings_df.iterrows()}
    matchups = league.scoreboard(week)
    round_name = get_playoff_round_name(league, week) if phase in ("playoffs", "championship") else None

    for m in matchups:
        if not (hasattr(m, "home_team") and hasattr(m, "away_team")):
            continue
        if m.home_team is None or m.away_team is None:
            continue
        if not _is_relevant_matchup(m, phase):
            continue

        home_name = m.home_team.team_name
        away_name = m.away_team.team_name
        home_score = m.home_score
        away_score = m.away_score

        if home_name not in rank_by_team or away_name not in rank_by_team:
            continue

        winner = home_name if home_score > away_score else away_name
        loser = away_name if home_score > away_score else home_name
        winner_rank = rank_by_team[winner]
        loser_rank = rank_by_team[loser]

        if winner_rank - loser_rank >= 4:
            events.append(
                {
                    "type": "upset",
                    "week": week,
                    "winner": winner,
                    "loser": loser,
                    "winner_power_rank": int(winner_rank),
                    "loser_power_rank": int(loser_rank),
                    "is_playoff": phase in ("playoffs", "championship"),
                    "playoff_round": round_name,
                }
            )

    return events


def get_ranking_movement_events(current_rankings_df, previous_rankings_df, phase):
    """
    Compares this week's power rankings to last week's and flags teams
    that moved sharply. Skipped once playoffs start, since power score
    ranking movement stops being the relevant story.
    """
    if previous_rankings_df is None or phase in ("playoffs", "championship"):
        return []

    events = []
    prev_rank_by_team = {row["Team"]: row["Rank"] for _, row in previous_rankings_df.iterrows()}

    for _, row in current_rankings_df.iterrows():
        team = row["Team"]
        current_rank = row["Rank"]
        if team not in prev_rank_by_team:
            continue
        previous_rank = prev_rank_by_team[team]
        change = previous_rank - current_rank

        if abs(change) >= RANK_JUMP_THRESHOLD:
            events.append(
                {
                    "type": "rank_jump" if change > 0 else "rank_drop",
                    "team": team,
                    "previous_power_rank": int(previous_rank),
                    "current_power_rank": int(current_rank),
                    "spots": int(abs(change)),
                }
            )

    return events


def get_playoff_picture_event(league, phase):
    """
    During the final regular season week(s), summarizes who's currently
    in playoff position and who's on the bubble.
    """
    if phase != "playoff_push":
        return None

    playoff_team_count = getattr(league.settings, "playoff_team_count", 4)

    standings = sorted(
        league.teams,
        key=lambda t: (t.wins, t.points_for),
        reverse=True,
    )

    teams_in = [t.team_name for t in standings[:playoff_team_count]]
    teams_bubble = [t.team_name for t in standings[playoff_team_count:playoff_team_count + 2]]

    if not teams_in and not teams_bubble:
        return None

    return {
        "type": "playoff_picture",
        "teams_in": teams_in,
        "teams_on_bubble": teams_bubble,
    }


def get_weekly_events(league, current_rankings_df, previous_rankings_df=None):
    """Runs all detectors and returns a combined list of event facts."""
    week = get_most_recent_completed_week(league)
    phase = get_season_phase(league, week)

    events = []
    events.extend(get_matchup_events(league, week, phase))
    events.extend(get_upset_events(league, week, phase, current_rankings_df))
    events.extend(get_ranking_movement_events(current_rankings_df, previous_rankings_df, phase))

    picture_event = get_playoff_picture_event(league, phase)
    if picture_event:
        events.append(picture_event)

    return events


if __name__ == "__main__":
    from espn_connector import get_league
    from power_rankings import calculate_power_rankings

    league = get_league()
    rankings = calculate_power_rankings(league)
    events = get_weekly_events(league, rankings)
    print(f"Found {len(events)} newsworthy events.")
    for e in events:
        print(e)

