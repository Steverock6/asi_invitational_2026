"""
Playoff Race: odds of making the playoffs (and grabbing a bye) for every team.

How it works: the rest of the regular season is played out 10,000 times.
In each simulated game, both teams "score" a random number centered on
their scoring average this season, with the same spread DonBot uses for
his picks. Standings are sorted by wins, then Points For (the league's
tiebreaker), and we count how often each team lands in each seed.

The results go into data.json under "playoff_race". The website also gets
the raw inputs (averages, spread, remaining schedule) so its What-If
picker can re-run the simulation in the browser with your picks locked in.
"""

import math
import random
import statistics

from power_rankings import _scoreboard, completed_weeks

SIMS = 10_000


def _bye_count(spots):
    """A 7-team bracket gives 1 bye, a 6-team bracket gives 2, a 4- or 8-team bracket gives none."""
    if spots <= 1:
        return 0
    bracket = 2 ** math.ceil(math.log2(spots))
    return bracket - spots


def _sorted_names(teams):
    """Standings order: wins, then Points For."""
    return sorted(teams, key=lambda n: (teams[n]["wins"], teams[n]["pf"]), reverse=True)


def clinch_status(teams, games_left, spots, byes):
    """
    Labels that are mathematically certain, no simulation involved.
    games_left = how many unplayed games each team still has.
    Because Points For can go either way, a tie in wins counts as a possible loss.
    """
    status = {}
    for name, t in teams.items():
        lo = t["wins"]
        hi = t["wins"] + games_left.get(name, 0)
        others = [o for o in teams if o != name]
        can_catch = sum(1 for o in others if teams[o]["wins"] + games_left.get(o, 0) >= lo)
        sure_ahead = sum(1 for o in others if teams[o]["wins"] > hi)
        if byes and can_catch < byes:
            status[name] = "bye"
        elif can_catch < spots:
            status[name] = "in"
        elif sure_ahead >= spots:
            status[name] = "out"
        else:
            status[name] = ""
    return status


def simulate(teams, spread, remaining, spots, byes, sims=SIMS, seed=0):
    """Plays out the remaining weeks `sims` times. Returns odds plus next-week stakes."""
    rng = random.Random(seed)
    names = list(teams)
    made = {n: 0 for n in names}
    bye = {n: 0 for n in names}
    seeds = {n: [0] * len(names) for n in names}
    next_games = remaining[0]["games"] if remaining else []
    # made_given[(game index, winner)][team] = times `team` made it when `winner` won that game
    made_given = {}
    wins_given = {}

    for _ in range(sims):
        w = {n: teams[n]["wins"] for n in names}
        pf = {n: teams[n]["pf"] for n in names}
        first_week_winners = []
        for wi, wk in enumerate(remaining):
            for home, away in wk["games"]:
                hs = rng.gauss(teams[home]["mean"], spread)
                as_ = rng.gauss(teams[away]["mean"], spread)
                winner = home if hs >= as_ else away
                w[winner] += 1
                pf[home] += hs
                pf[away] += as_
                if wi == 0:
                    first_week_winners.append(winner)
        order = sorted(names, key=lambda n: (w[n], pf[n]), reverse=True)
        for i, n in enumerate(order):
            seeds[n][i] += 1
            if i < spots:
                made[n] += 1
            if i < byes:
                bye[n] += 1
        in_set = set(order[:spots])
        for gi, winner in enumerate(first_week_winners):
            key = (gi, winner)
            wins_given[key] = wins_given.get(key, 0) + 1
            bucket = made_given.setdefault(key, {})
            for n in in_set:
                bucket[n] = bucket.get(n, 0) + 1

    pct = lambda x, of: round(100 * x / of, 1) if of else 0.0
    odds = {
        n: {
            "playoffs": pct(made[n], sims),
            "bye": pct(bye[n], sims),
            "seeds": [pct(c, sims) for c in seeds[n]],
        }
        for n in names
    }

    def p_in(gi, winner, team):
        times = wins_given.get((gi, winner), 0)
        return pct(made_given.get((gi, winner), {}).get(team, 0), times)

    stakes, best_game, best_swing, best_gi = [], None, -1, 0
    for gi, (home, away) in enumerate(next_games):
        for team, opp in ((home, away), (away, home)):
            stakes.append({
                "team": team,
                "opponent": opp,
                "now": odds[team]["playoffs"],
                "if_win": p_in(gi, team, team),
                "if_lose": p_in(gi, opp, team),
            })
        swing = sum(abs(p_in(gi, home, n) - p_in(gi, away, n)) for n in names)
        if swing > best_swing:
            best_swing, best_gi = swing, gi
    # Only teams whose fate actually moves are interesting.
    stakes = [s for s in stakes if abs(s["if_win"] - s["if_lose"]) >= 1]
    stakes.sort(key=lambda s: abs(s["if_win"] - s["if_lose"]), reverse=True)
    if best_swing >= 0:
        home, away = next_games[best_gi]
        affected = [
            {"team": n, "if_home_wins": p_in(best_gi, home, n), "if_away_wins": p_in(best_gi, away, n)}
            for n in names
        ]
        best_game = {
            "home": home,
            "away": away,
            "total_swing": round(best_swing, 1),
            "teams_affected": [t for t in affected if abs(t["if_home_wins"] - t["if_away_wins"]) >= 3],
        }
    return odds, stakes, best_game


def build_playoff_race(league):
    """Everything the Playoff Race tab needs, ready for data.json."""
    through = completed_weeks(league)
    reg = league.settings.reg_season_count
    spots = int(getattr(league.settings, "playoff_team_count", 0) or 0)
    if not spots or through < 1:
        return None
    byes = _bye_count(spots)

    every_score = [s for t in league.teams for s in t.scores[:through]]
    spread = max(statistics.pstdev(every_score) if len(every_score) > 1 else 0, 10.0)

    teams = {
        t.team_name: {
            "wins": t.wins,
            "losses": t.losses,
            "pf": round(t.points_for, 2),
            "mean": round(sum(t.scores[:through]) / through, 2),
        }
        for t in league.teams
    }

    remaining = []
    for week in range(through + 1, reg + 1):
        games = [
            (m.home_team.team_name, m.away_team.team_name)
            for m in _scoreboard(league, week)
            if getattr(m, "home_team", None) is not None and getattr(m, "away_team", None) is not None
            and hasattr(m.home_team, "team_name") and hasattr(m.away_team, "team_name")
        ]
        if games:
            remaining.append({"week": week, "games": games})

    games_left = {}
    for wk in remaining:
        for home, away in wk["games"]:
            games_left[home] = games_left.get(home, 0) + 1
            games_left[away] = games_left.get(away, 0) + 1

    odds, stakes, game_of_week = simulate(teams, spread, remaining, spots, byes, seed=through)
    status = clinch_status(teams, games_left, spots, byes)

    table = []
    for i, name in enumerate(_sorted_names(teams)):
        o = odds[name]
        table.append({
            "team": name,
            "current_seed": i + 1,
            "wins": teams[name]["wins"],
            "losses": teams[name]["losses"],
            "pf": teams[name]["pf"],
            "playoffs": o["playoffs"],
            "bye": o["bye"],
            "seeds": o["seeds"],
            "status": status[name],
        })

    return {
        "through_week": through,
        "reg_season_weeks": reg,
        "spots": spots,
        "byes": byes,
        "sims": SIMS,
        "spread": round(spread, 2),
        "teams": teams,
        "remaining": [{"week": wk["week"], "games": [list(g) for g in wk["games"]]} for wk in remaining],
        "table": table,
        "stakes": stakes,
        "game_of_week": game_of_week,
    }


def playoff_race_event(race):
    """A DonBot post about the biggest game of the week for the playoff race."""
    if not race or not race.get("remaining") or not race.get("game_of_week"):
        return None
    g = race["game_of_week"]
    if not g["teams_affected"]:
        return None
    return {
        "type": "playoff_stakes",
        "week": race["remaining"][0]["week"],
        "weeks_left": len(race["remaining"]),
        "game_of_the_week": f"{g['away']} at {g['home']}",
        "playoff_odds_if_home_team_wins": {t["team"]: f"{t['if_home_wins']:.0f}%" for t in g["teams_affected"]},
        "playoff_odds_if_away_team_wins": {t["team"]: f"{t['if_away_wins']:.0f}%" for t in g["teams_affected"]},
        "home_team": g["home"],
        "away_team": g["away"],
    }
