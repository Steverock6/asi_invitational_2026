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


def calculate_power_rankings(league):
    """
    Takes a connected espn_api League object and returns a DataFrame
    of power rankings, ranked and tiered.
    """
    reg_season_weeks = league.settings.reg_season_count

    all_play_wins = {}
    all_play_losses = {}
    recent_margins = {}
    points_against_by_team = {team.team_id: [] for team in league.teams}

    # Points against, gathered from actual matchups (regular season only)
    for week in range(reg_season_weeks):
        matchups = league.scoreboard(week + 1)
        for matchup in matchups:
            if getattr(matchup, "home_team", None) and getattr(matchup, "away_team", None):
                points_against_by_team[matchup.home_team.team_id].append(matchup.away_score)
                points_against_by_team[matchup.away_team.team_id].append(matchup.home_score)

    # All-play record: how a team would have done against every other
    # team's score that same week, not just their actual opponent
    for week in range(reg_season_weeks):
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
        recent_scores = [s for s in team.scores[:reg_season_weeks][-3:] if s is not None]
        recent_against = points_against_by_team[team.team_id][-3:]
        if recent_scores and len(recent_scores) == len(recent_against):
            recent_margins[team.team_id] = sum(a - b for a, b in zip(recent_scores, recent_against))
        else:
            recent_margins[team.team_id] = 0

    data = []
    for team in league.teams:
        played_opponents = [
            opponent
            for opponent, score in zip(team.schedule[:reg_season_weeks], team.scores[:reg_season_weeks])
            if score is not None
        ]
        schedule_strength = (
            sum(op.points_for for op in played_opponents) / len(played_opponents)
            if played_opponents
            else 0
        )

        all_play_total = all_play_wins.get(team.team_id, 0) + all_play_losses.get(team.team_id, 0)
        all_play_win_pct = (
            all_play_wins.get(team.team_id, 0) / all_play_total if all_play_total > 0 else 0
        )

        points_for = sum(s for s in team.scores[:reg_season_weeks] if s is not None)
        points_against = sum(points_against_by_team[team.team_id])

        data.append(
            {
                "Team": team.team_name,
                "Abbrev": team.team_abbrev,
                "Wins": team.wins,
                "Losses": team.losses,
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


if __name__ == "__main__":
    from espn_connector import get_league

    league = get_league()
    rankings = calculate_power_rankings(league)
    print(rankings.to_string(index=False))
