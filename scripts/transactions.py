"""
Pulls this season's completed moves (waiver claims, free-agent pickups,
drops and trades) for the home page, most recent first.

Reads ESPN's transaction log (view=mTransactions2), the same source the
record book uses, so team, owner and player names resolve correctly.
Each row is one move: an add/drop pair or a trade shows as a single line.
"""

import json
import os

PICKUP_TYPES = ["FREEAGENT", "WAIVER"]
TRADE_TYPES = ["TRADE_ACCEPT", "TRADE_PROPOSAL", "TRADE_VETO"]
OWNER_ALIASES_PATH = "scripts/owner_names.json"


def _raw_txns(league, period, types):
    headers = {"x-fantasy-filter": json.dumps({"transactions": {"filterType": {"value": sorted(types)}}})}
    params = {"view": "mTransactions2"}
    if period is not None:
        params["scoringPeriodId"] = period
    data = league.espn_request.league_get(params=params, headers=headers)
    if isinstance(data, list):
        data = data[0] if data else {}
    return data.get("transactions") or []


def _owner_by_team_id(league):
    """Team id -> owner display name, using the same aliases as the record book."""
    aliases = {}
    if os.path.exists(OWNER_ALIASES_PATH):
        with open(OWNER_ALIASES_PATH) as f:
            aliases = json.load(f)
    out = {}
    for team in league.teams:
        o = team.owners[0] if getattr(team, "owners", None) else None
        if isinstance(o, dict):
            raw_id = o.get("id")
            name = f"{o.get('firstName', '').strip()} {o.get('lastName', '').strip()}".strip()
        else:
            raw_id, name = o, (o or "")
        out[team.team_id] = aliases.get(raw_id) or aliases.get(name) or name
    return out


class _Players:
    """Player id -> name, falling back to a direct lookup for anyone missing from the map."""

    def __init__(self, league):
        self.league = league
        self.cache = {}

    def name(self, pid):
        name = self.league.player_map.get(pid)
        if isinstance(name, str):
            return name
        if pid not in self.cache:
            try:
                info = self.league.player_info(playerId=pid)
                self.cache[pid] = getattr(info, "name", None) or "Unknown player"
            except Exception:
                self.cache[pid] = "Unknown player"
        return self.cache[pid]


def _periods(league):
    last = min(max(int(league.current_week or 1), 1), 18)
    return [None, 0] + list(range(1, last + 1))


def get_transactions(league):
    """Returns a list of move dicts covering the whole season so far, newest first."""
    team_name = {t.team_id: t.team_name for t in league.teams}
    owner = _owner_by_team_id(league)
    players = _Players(league)
    periods = _periods(league)

    def side(team_id):
        return {"team": team_name.get(team_id, "Unknown"), "owner": owner.get(team_id, "")}

    moves, seen = [], set()

    # ---- waiver claims and free-agent adds/drops ----
    for period in periods:
        for t in _raw_txns(league, period, PICKUP_TYPES):
            if t.get("status") != "EXECUTED" or t.get("id") in seen or t.get("teamId") not in team_name:
                continue
            seen.add(t.get("id"))
            adds = [players.name(i.get("playerId")) for i in t.get("items") or [] if i.get("type") == "ADD"]
            drops = [players.name(i.get("playerId")) for i in t.get("items") or [] if i.get("type") == "DROP"]
            if not adds and not drops:
                continue
            kind = "WAIVER" if t.get("type") == "WAIVER" else ("FREE_AGENT" if adds else "DROP")
            moves.append({
                "id": t.get("id"),
                "date": t.get("processDate") or t.get("proposedDate") or 0,
                "kind": kind,
                **side(t["teamId"]),
                "adds": adds,
                "drops": drops,
                "bid": (t.get("bidAmount") or 0) if kind == "WAIVER" else None,
            })

    # ---- trades: one row per completed trade, listing what each team received ----
    # Every accepted trade has a TRADE_ACCEPT row (ESPN often leaves its status blank).
    # League members can then vote to veto; enough votes cancels the trade.
    rows = {}
    for period in periods:
        for t in _raw_txns(league, period, TRADE_TYPES):
            rows.setdefault(t.get("id"), t)
    required = getattr(league.settings, "veto_votes_required", 0) or 0
    vetoes = {}
    for r in rows.values():
        if r.get("type") == "TRADE_VETO" and r.get("status") in (None, "EXECUTED"):
            vetoes[r.get("relatedTransactionId")] = vetoes.get(r.get("relatedTransactionId"), 0) + 1
    done = set()
    for a in rows.values():
        if a.get("type") != "TRADE_ACCEPT" or a.get("status") not in (None, "EXECUTED"):
            continue
        key = a.get("relatedTransactionId") or a.get("id")
        if key in done:
            continue
        done.add(key)
        veto_count = vetoes.get(a.get("id"), 0) + (vetoes.get(key, 0) if key != a.get("id") else 0)
        if a.get("status") != "EXECUTED" and required and veto_count >= required:
            continue
        gets = {}
        for r in (a, rows.get(a.get("relatedTransactionId"))):
            for it in (r or {}).get("items") or []:
                to_id, from_id = it.get("toTeamId"), it.get("fromTeamId")
                if to_id in team_name and from_id in team_name and to_id != from_id:
                    gets.setdefault(from_id, [])
                    name = players.name(it.get("playerId"))
                    if name not in gets.setdefault(to_id, []):
                        gets[to_id].append(name)
        if not gets:
            continue
        moves.append({
            "id": key,
            "date": a.get("processDate") or a.get("acceptedDate") or a.get("proposedDate") or 0,
            "kind": "TRADE",
            "sides": [{**side(tid), "gets": names} for tid, names in gets.items()],
        })

    moves.sort(key=lambda m: m["date"] or 0, reverse=True)
    return moves


if __name__ == "__main__":
    from espn_connector import get_league

    league = get_league()
    moves = get_transactions(league)
    print(f"Found {len(moves)} moves this season.")
    for m in moves[:15]:
        print(m)
