"""
Calculates weekly power rankings for the league.

This is a ported and slightly hardened version of the original notebook
logic: same factors, same weights, same tier cutoffs. Two fixes were
added: (1) a safe z-score that won't crash if a column has zero variance
early in the season, and (2) defensive handling of missing/bye-week scores.
"""

import numpy as np
import pandas as pd


def safe_zscore(series):
    """Z-score that returns all zeros instead of NaN if there's no variance."""
    std = series.std()
    if std == 0 or pd.isna(std):
        return pd.Series([0.0] * len(series), index=series.index)
    return (series - series.mean()) / std


def assign_tier(z):
    if z >= 1.5:
        return "S"
    elif z >= 0.5:
        return "A"
    elif z >= -0.5:
        return "B"
    elif z >= -1.0:
        return "C"
    elif z >= -1.5:
        return "D"
    else:
        return "F"


_DECIDED = ("W", "L", "T")


def last_completed_week(league):
    """
    The most recent week with final results, playoffs included.
    ESPN fills unplayed weeks with a score of 0 (not None), so this goes by
    decided outcomes, with the league's own week counter as a backstop.
    """
    decided = max(
        (max((i + 1 for i, o in enumerate(t.outcomes) if o in _DECIDED), default=0) for t in league.teams),
        default=0,
    )
    total = max((len(t.outcomes) for t in league.teams), default=0)
    return max(decided, min(int(league.current_week or 1) - 1, total))


def completed_weeks(league):
    """Number of regular-season weeks with final results."""
    return min(last_completed_week(league), league.settings.reg_season_count)


def _scoreboard(league, week):
    cache = league.__dict__.setdefault("_scoreboard_cache", {})
    if week not in cache:
        cache[week] = league.scoreboard(week)
    return cache[week]


def calculate_power_rankings(league, through_week=None):
    """
    Takes a connected espn_api League object and returns a DataFrame
    of power rankings, ranked and tiered. Pass through_week to see the
    rankings exactly as they stood after that week.
    """
    weeks = through_week or completed_weeks(league)

    all_play_wins = {}
    all_play_losses = {}
    recent_margins = {}
    points_against_by_team = {team.team_id: [] for team in league.teams}

    # Points against, gathered from actual matchups (regular season only)
    for week in range(weeks):
        for matchup in _scoreboard(league, week + 1):
            if getattr(matchup, "home_team", None) and getattr(matchup, "away_team", None):
                points_against_by_team[matchup.home_team.team_id].append(matchup.away_score)
                points_against_by_team[matchup.away_team.team_id].append(matchup.home_score)

    # All-play record: how a team would have done against every other
    # team's score that same week, not just their actual opponent
    for week in range(weeks):
        weekly_scores = [team.scores[week] for team in league.teams if team.scores[week] is not None]
        for team in league.teams:
            if team.scores[week] is None:
                continue
            team_score = team.scores[week]
            all_play_wins.setdefault(team.team_id, 0)
            all_play_losses.setdefault(team.team_id, 0)
            all_play_wins[team.team_id] += sum(
                team_score > opp_score for opp_score in weekly_scores if opp_score != team_score
            )
            all_play_losses[team.team_id] += sum(
                team_score < opp_score for opp_score in weekly_scores if opp_score != team_score
            )

    # Recent momentum: point differential over the last 3 completed weeks
    for team in league.teams:
        recent_scores = [s for s in team.scores[:weeks][-3:] if s is not None]
        recent_against = points_against_by_team[team.team_id][-3:]
        if recent_scores and len(recent_scores) == len(recent_against):
            recent_margins[team.team_id] = sum(a - b for a, b in zip(recent_scores, recent_against))
        else:
            recent_margins[team.team_id] = 0

    data = []
    for team in league.teams:
        opponents = team.schedule[:weeks]
        schedule_strength = (
            sum(sum(s for s in op.scores[:weeks] if s is not None) for op in opponents) / len(opponents)
            if opponents
            else 0
        )

        all_play_total = all_play_wins.get(team.team_id, 0) + all_play_losses.get(team.team_id, 0)
        all_play_win_pct = (
            all_play_wins.get(team.team_id, 0) / all_play_total if all_play_total > 0 else 0
        )

        results = team.outcomes[:weeks]
        points_for = sum(s for s in team.scores[:weeks] if s is not None)
        points_against = sum(points_against_by_team[team.team_id])

        data.append(
            {
                "Team": team.team_name,
                "Abbrev": team.team_abbrev,
                "Wins": results.count("W"),
                "Losses": results.count("L"),
                "Points For": points_for,
                "Points Against": points_against,
                "Schedule Strength": schedule_strength,
                "All Play %": all_play_win_pct,
                "Recent Momentum": recent_margins[team.team_id],
            }
        )

    df = pd.DataFrame(data)

    games_played = df["Wins"] + df["Losses"]
    df["Win %"] = np.where(games_played > 0, df["Wins"] / games_played, 0)
    df["Margin"] = df["Points For"] - df["Points Against"]

    for col in ["Points For", "Win %", "Margin", "Schedule Strength", "All Play %", "Recent Momentum"]:
        df[f"z_{col}"] = safe_zscore(df[col])

    df["Power Rank Score"] = (
        df["z_Points For"] * 0.20
        + df["z_Win %"] * 0.35
        + df["z_Margin"] * 0.15
        + df["z_Schedule Strength"] * 0.05
        + df["z_All Play %"] * 0.25
    )

    df = df.sort_values("Power Rank Score", ascending=False).reset_index(drop=True)
    df["Rank"] = df.index + 1

    df["z_Power Rank Score"] = safe_zscore(df["Power Rank Score"])
    df["Tier"] = df["z_Power Rank Score"].apply(assign_tier)

    df = df[
        [
            "Rank",
            "Team",
            "Abbrev",
            "Wins",
            "Losses",
            "Points For",
            "Points Against",
            "Win %",
            "All Play %",
            "Recent Momentum",
            "Schedule Strength",
            "Power Rank Score",
            "Tier",
        ]
    ]
    return df


def calculate_rankings_with_trends(league):
    """
    Returns (current_df, previous_df). current_df is this week's rankings plus:
      Score Change     - change in Power Rank Score since last week
      Rank Change      - spots gained (+) or lost (-) since last week
      Season Avg Score - average Power Rank Score across every completed week
      Rank History     - the team's rank after each completed week
    previous_df is last week's rankings (None in week 1).
    """
    weeks = completed_weeks(league)
    history = [calculate_power_rankings(league, w) for w in range(1, weeks + 1)] or [calculate_power_rankings(league)]
    current = history[-1].copy()
    previous = history[-2] if len(history) > 1 else None

    prev_score = dict(zip(previous["Team"], previous["Power Rank Score"])) if previous is not None else {}
    prev_rank = dict(zip(previous["Team"], previous["Rank"])) if previous is not None else {}
    scores_by_team = {}
    for df in history:
        for team, score in zip(df["Team"], df["Power Rank Score"]):
            scores_by_team.setdefault(team, []).append(score)

    current["Score Change"] = [
        (score - prev_score[t]) if t in prev_score else None
        for t, score in zip(current["Team"], current["Power Rank Score"])
    ]
    current["Rank Change"] = [
        (prev_rank[t] - rank) if t in prev_rank else None for t, rank in zip(current["Team"], current["Rank"])
    ]
    current["Season Avg Score"] = [sum(scores_by_team[t]) / len(scores_by_team[t]) for t in current["Team"]]

    # Rank after each week so far, for the little trend line on the site
    ranks_by_team = {}
    for df in history:
        for team, rank in zip(df["Team"], df["Rank"]):
            ranks_by_team.setdefault(team, []).append(int(rank))
    current["Rank History"] = [ranks_by_team[t] for t in current["Team"]]
    return current, previous


if __name__ == "__main__":
    from espn_connector import get_league

    league = get_league()
    rankings = calculate_power_rankings(league)
    print(rankings.to_string(index=False))
