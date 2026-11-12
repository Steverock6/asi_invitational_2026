"""
Builds data/records.json: top-5 all-time league records from ESPN.

pip install espn-api

Env vars:
  LEAGUE_ID   required
  START_YEAR  first season of the league (default 2018)
  LEAGUE_YEAR optional, last season to include (default: current year)
  ESPN_S2, ESPN_SWID   only needed if the league is private
  OWNER_NAMES optional JSON, e.g. {"{ABC-123}": "Steve"} to override display names

Optional files (both live in scripts/):
  owner_names.json  merge one person who shows up under two accounts/names,
                    e.g. {"{OLD-ID}": "Steve Rock", "Stephen Rock": "Steve Rock"}
  champions.json    only if a champion comes out wrong, e.g. {"2019": "Steve Rock"}

Also writes data/owners.json, data/champions.json and data/playoff_games.json, data/transactions_summary.json, data/trades.json and data/trade_debug.json so you can sanity-check the results.
Transactions: ESPN only exposes trades from 2019 on, and older pickups may be missing; the record titles show the seasons actually covered.
  MIN_FAAB_SEASONS optional, seasons an owner needs to qualify for the "least FAAB" record (default 3)
  MIN_PLAYOFF_GAMES optional, minimum playoff games to qualify for the playoff win % record (default 3)

Game-level records use regular-season games only. Streaks run across seasons.
Championships count only finished seasons (a season is finished once the calendar year passes it).
"""
import json
import os
from collections import defaultdict
from datetime import datetime, timezone

from espn_api.football import League

LEAGUE_ID = int(os.environ["LEAGUE_ID"])
START_YEAR = int(os.environ.get("START_YEAR", 2018))
NOW = datetime.now()
END_YEAR = int(os.environ.get("LEAGUE_YEAR") or NOW.year)
ESPN_S2 = os.environ.get("ESPN_S2")
SWID = os.environ.get("ESPN_SWID") or os.environ.get("SWID")
NAME_OVERRIDES = json.loads(os.environ.get("OWNER_NAMES", "{}"))
TOP_N = 5
MIN_FAAB_SEASONS = int(os.environ.get("MIN_FAAB_SEASONS", 3))  # keeps brand-new owners off the "least FAAB" list
MIN_PLAYOFF_GAMES = int(os.environ.get("MIN_PLAYOFF_GAMES", 3))  # keeps a lone 1-0 off the list


def _load(path):
    return json.load(open(path)) if os.path.exists(path) else {}


ALIASES = _load("scripts/owner_names.json")
CHAMP_OVERRIDES = {int(k): v for k, v in _load("scripts/champions.json").items()}
people = defaultdict(lambda: {"names": set(), "ids": set(), "seasons": set()})


def owner_of(team):
    """Returns (key, display_name, raw_id, raw_name).
    key is what records group on: a manual alias if you set one, else ESPN's member id
    (team names change every year, so they are never used when an id exists)."""
    o = team.owners[0] if getattr(team, "owners", None) else None
    if isinstance(o, dict):
        raw_id = o.get("id", team.team_name)
        raw_name = f"{o.get('firstName', '').strip()} {o.get('lastName', '').strip()}".strip()
    else:
        raw_id = raw_name = (o or team.team_name)
    raw_name = NAME_OVERRIDES.get(raw_id, raw_name) or team.team_name
    canon = ALIASES.get(raw_id) or ALIASES.get(raw_name)
    if canon:
        return canon, canon, raw_id, raw_name
    return raw_id, raw_name, raw_id, raw_name


def get_playoffs(league, year, key_by_team_id):
    """Championship-bracket games only (no consolation games, no byes)."""
    try:
        data = league.espn_request.league_get(params={"view": "mMatchupScore"})
        if isinstance(data, list):
            data = data[0]
        rows = []
        for m in data.get("schedule", []):
            if m.get("playoffTierType") != "WINNERS_BRACKET":
                continue
            home, away = m.get("home"), m.get("away")
            if not home or not away or m.get("winner") not in ("HOME", "AWAY"):
                continue  # bye week, unplayed, or tied
            h_id, a_id = key_by_team_id.get(home["teamId"]), key_by_team_id.get(away["teamId"])
            if h_id is None or a_id is None:
                continue
            h_won = m["winner"] == "HOME"
            hp, ap = home.get("totalPoints", 0), away.get("totalPoints", 0)
            wk = m.get("matchupPeriodId")
            rows.append(dict(owner_id=h_id, season=year, week=wk, won=h_won, opp_id=a_id, pts=hp, opp_pts=ap))
            rows.append(dict(owner_id=a_id, season=year, week=wk, won=not h_won, opp_id=h_id, pts=ap, opp_pts=hp))
        if not rows:
            print(f"  no playoff games found for {year}")
        return rows
    except Exception as e:
        print(f"  playoff data unavailable for {year}: {e}")
        return []


DEBUG = {}  # written to data/trade_debug.json so we can see what ESPN actually returned


def raw_txns(league, period, types):
    """Raw ESPN transaction rows for one week. We read ESPN's data directly instead of going through
    the library's parser, which throws away a whole week if a single row is shaped unexpectedly."""
    headers = {"x-fantasy-filter": json.dumps({"transactions": {"filterType": {"value": sorted(types)}}})}
    data = league.espn_request.league_get(
        params={"view": "mTransactions2", "scoringPeriodId": period}, headers=headers)
    return data.get("transactions") or []


def get_transactions(league, year, key_by_team_id):
    """Returns (pickups, trades).
    pickups: executed free-agent/waiver moves, one row per add/drop. FAAB comes from the winning bid.
    trades:  completed trades found in the transaction log."""
    info = DEBUG.setdefault(str(year), {})
    errors = []
    rows, seen = [], set()
    pmap = league.player_map
    for period in range(1, 19):
        try:
            batch = raw_txns(league, period, {"FREEAGENT", "WAIVER"})
        except Exception as e:
            errors.append(f"pickups wk{period}: {str(e)[:120]}")
            continue
        for t in batch:
            if t.get("status") != "EXECUTED" or t.get("id") in seen or t.get("teamId") not in key_by_team_id:
                continue
            seen.add(t.get("id"))
            bid = t.get("bidAmount") or 0
            for it in t.get("items") or []:
                name = pmap.get(it.get("playerId"), "Unknown")
                if it.get("type") not in ("ADD", "DROP") or not isinstance(name, str) or name == "Unknown":
                    continue
                rows.append(dict(txn_id=t.get("id"), owner_id=key_by_team_id[t["teamId"]], season=year,
                                 kind=t.get("type"), move=it["type"], player=name,
                                 bid=bid if it["type"] == "ADD" else 0))
    if not rows:
        print(f"  no transactions found for {year}")

    # ---- trades: gather every trade-related row, then group each proposal with its accept/uphold/veto ----
    allrows, counts, shapes = {}, defaultdict(int), {}
    for period in range(1, 19):
        try:
            batch = raw_txns(league, period, {"TRADE_PROPOSAL", "TRADE_ACCEPT", "TRADE_UPHOLD", "TRADE_VETO"})
        except Exception as e:
            errors.append(f"trades wk{period}: {str(e)[:120]}")
            continue
        for t in batch:
            if t.get("id") in allrows:
                continue
            allrows[t.get("id")] = t
            counts[f"{t.get('type')}/{t.get('status')}"] += 1
            if t.get("type") not in shapes:  # structure only, no names or ids
                items = t.get("items") or []
                shapes[t.get("type")] = dict(row_keys=sorted(t.keys()), n_items=len(items),
                                             item_keys=sorted(items[0].keys()) if items else [])

    def root(i):
        for _ in range(6):
            r = allrows.get(i)
            if not r or not r.get("relatedTransactionId"):
                return i
            i = r["relatedTransactionId"]
        return i

    groups = defaultdict(list)
    for i, r in allrows.items():
        groups[root(i)].append(r)
    trades, vetoed = [], 0
    for g in groups.values():
        done = any(r.get("type") in ("TRADE_ACCEPT", "TRADE_UPHOLD") and r.get("status") == "EXECUTED" for r in g)
        if any(r.get("type") == "TRADE_VETO" and r.get("status") == "EXECUTED" for r in g):
            vetoed += 1
            continue
        if not done:
            continue
        team_ids = set()
        for r in g:
            team_ids.add(r.get("teamId"))
            for it in r.get("items") or []:
                team_ids.update(x for x in (it.get("fromTeamId"), it.get("toTeamId")) if x)
        ids = sorted({key_by_team_id[x] for x in team_ids if x in key_by_team_id})
        date = max((r.get("processDate") or r.get("acceptedDate") or r.get("proposedDate") or 0) for r in g)
        if len(ids) >= 2:
            trades.append(dict(season=year, date=date, owner_ids=ids, source="transaction log"))
    info["transaction_log_trade_rows"] = dict(counts)
    info["trade_groups"] = dict(total=len(groups), counted_as_trades=len(trades), vetoed=vetoed)
    info["row_shapes"] = shapes
    if errors:
        info["request_errors"] = errors[:6]
    return rows, trades


def get_trades(league, year, key_by_team_id):
    """Completed trades (ESPN's activity feed only goes back to 2019). One row per trade."""
    if year < 2019:
        return []
    rows, seen, offset = [], set(), 0
    try:
        while offset < 2000:
            batch = league.recent_activity(size=50, msg_type="TRADED", offset=offset)
            for act in batch:
                ids = sorted({key_by_team_id[a[0].team_id] for a in act.actions
                              if a[0] and a[0].team_id in key_by_team_id})
                sig = (act.date, tuple(ids))
                if len(ids) >= 2 and sig not in seen:
                    seen.add(sig)
                    rows.append(dict(season=year, date=act.date, owner_ids=ids, source="activity feed"))
            if len(batch) < 50:
                break
            offset += 50
    except Exception as e:
        print(f"  trade data unavailable for {year}: {e}")
        DEBUG.setdefault(str(year), {})["trade_feed_error"] = str(e)[:300]
    return rows


def load_rows():
    """games   = one row per team per regular-season week (with opponent's score)
    seasons = one row per team per season"""
    games, seasons, playoffs, txns, trades = [], [], [], [], []
    for year in range(START_YEAR, END_YEAR + 1):
        try:
            league = League(league_id=LEAGUE_ID, year=year, espn_s2=ESPN_S2, swid=SWID)
        except Exception as e:
            print(f"skip {year}: {e}")
            continue
        reg_weeks = league.settings.reg_season_count
        key_by_team_id = {t.team_id: owner_of(t)[0] for t in league.teams}
        playoffs.extend(get_playoffs(league, year, key_by_team_id))
        pickups, log_trades = get_transactions(league, year, key_by_team_id)
        feed_trades = get_trades(league, year, key_by_team_id)
        txns.extend(pickups)
        trades.extend(feed_trades or log_trades)  # prefer the activity feed, fall back to the transaction log
        print(f"  {year}: {len({p['txn_id'] for p in pickups})} pickups, trades: "
              f"{len(feed_trades)} from activity feed, {len(log_trades)} from transaction log")
        for t in league.teams:
            key, name, raw_id, raw_name = owner_of(t)
            scores = list(t.scores[:reg_weeks])
            if not any(s > 0 for s in scores):  # season not started / no data
                continue
            people[key]["names"].add(raw_name)
            people[key]["ids"].add(str(raw_id))
            people[key]["seasons"].add(year)
            for w, pts in enumerate(scores):
                if pts <= 0:
                    continue
                try:
                    opp = t.schedule[w]
                    opp_pts = opp.scores[w]
                    opp_key = owner_of(opp)[0]
                except (IndexError, AttributeError):
                    opp_pts, opp_key = None, None
                if opp_pts is not None and opp_pts <= 0:
                    opp_pts = None
                games.append(dict(owner_id=key, owner=name, team=t.team_name, season=year,
                                  week=w + 1, points=pts, opp_id=opp_key, opp_points=opp_pts))
            seasons.append(dict(owner_id=key, owner=name, team=t.team_name, season=year,
                                wins=t.wins, points=round(t.points_for, 2),
                                final_standing=getattr(t, "final_standing", 0) or 0,
                                complete=NOW.year > year))
        print(f"loaded {year}")
    # One display name per person everywhere (their most recent), so
    # "Steve Rock" and "Stephen Rock" on the same account never look like two people.
    latest = {r["owner_id"]: r["owner"] for r in seasons}
    for r in games + seasons:
        r["owner"] = latest.get(r["owner_id"], r["owner"])
    for g in games:
        g["opp_owner"] = latest.get(g["opp_id"], "Unknown")
    for p in playoffs:
        p["owner"] = latest.get(p["owner_id"], "Unknown")
        p["opp_owner"] = latest.get(p["opp_id"], "Unknown")
    for x in txns:
        x["owner"] = latest.get(x["owner_id"], "Unknown")
    for x in trades:
        x["owners"] = [latest.get(o, "Unknown") for o in x["owner_ids"]]
    return games, seasons, playoffs, txns, trades


def top(rows, key, fmt=lambda v: v, extra=(), reverse=True):
    """Top N rows by `key`. Equal values share a rank."""
    ranked = sorted(rows, key=lambda r: r[key], reverse=reverse)[:TOP_N]
    out, prev, rank = [], None, 0
    for i, r in enumerate(ranked):
        if r[key] != prev:
            rank, prev = i + 1, r[key]
        out.append(dict(rank=rank, owner=r["owner"], value=fmt(r[key]),
                        **{k: r[k] for k in extra if k in r}))
    return out


def career(seasons, key):
    tot, names = defaultdict(float), {}
    for s in seasons:
        tot[s["owner_id"]] += s[key]
        names[s["owner_id"]] = s["owner"]
    rows = [dict(owner=names[o], total=round(v, 2)) for o, v in tot.items()]
    return top(rows, "total")


def decided_games(games):
    """Winner's-eye view of each game (one row per game, ties skipped)."""
    out = []
    for g in games:
        if g["opp_points"] is not None and g["points"] > g["opp_points"]:
            out.append(dict(g, margin=round(g["points"] - g["opp_points"], 2),
                            team=f"over {g['opp_owner']} ({g['points']:.2f}\u2013{g['opp_points']:.2f})"))
    return out


def win_streaks(games):
    by_owner = defaultdict(list)
    for g in games:
        if g["opp_points"] is not None:
            by_owner[g["owner_id"]].append(g)
    label = lambda g: f"{g['season']} Wk {g['week']}"
    rows = []
    for gs in by_owner.values():
        gs.sort(key=lambda g: (g["season"], g["week"]))
        best, cur, cur_start, best_start, best_end = 0, 0, None, None, None
        for g in gs:
            if g["points"] > g["opp_points"]:
                if cur == 0:
                    cur_start = g
                cur += 1
                if cur > best:
                    best, best_start, best_end = cur, cur_start, g
            else:
                cur = 0
        if best:
            rows.append(dict(owner=gs[0]["owner"], streak=best,
                             team=label(best_start) if best == 1 else f"{label(best_start)} to {label(best_end)}"))
    return top(rows, "streak", extra=("team",))


def champion_rows(seasons):
    out = []
    for s in seasons:
        if s["season"] in CHAMP_OVERRIDES:
            if s["owner"] == CHAMP_OVERRIDES[s["season"]]:
                out.append(s)
        elif s["complete"] and s["final_standing"] == 1:
            out.append(s)
    return out


def championships(seasons):
    years = defaultdict(list)
    for s in champion_rows(seasons):
        years[s["owner"]].append(s["season"])
    rows = [dict(owner=o, titles=len(y), team=", ".join(str(v) for v in sorted(y)))
            for o, y in years.items()]
    rows.sort(key=lambda r: r["team"].split(", ")[-1], reverse=True)  # newest title breaks ties
    return top(rows, "titles", extra=("team",))


def playoff_record(playoffs):
    """Playoff win % (championship bracket). Ties on win % go to the person with more wins."""
    tally = defaultdict(lambda: [0, 0])
    names = {}
    for p in playoffs:
        tally[p["owner_id"]][0 if p["won"] else 1] += 1
        names[p["owner_id"]] = p["owner"]
    rows = []
    for oid, (w, l) in tally.items():
        if w + l >= MIN_PLAYOFF_GAMES:
            rows.append(dict(owner=names[oid], sort=(w / (w + l), w), team=f"{w}-{l}"))
    return top(rows, "sort", fmt=lambda v: round(v[0], 3), extra=("team",))


def span(rows):
    yrs = sorted({r["season"] for r in rows})
    return f"{yrs[0]}\u2013{yrs[-1]}" if len(yrs) > 1 else (str(yrs[0]) if yrs else "")


def txn_records(seasons, txns, trades):
    out, sect = [], "Transactions"
    if txns:
        span_t = span(txns)
        names = {x["owner_id"]: x["owner"] for x in txns}
        tcount = defaultdict(int)
        for tr in trades:
            for o in tr["owner_ids"]:
                tcount[o] += 1
        # a pickup and its paired drop share a txn id, so count each executed transaction once
        all_ids = defaultdict(set)
        for x in txns:
            all_ids[x["owner_id"]].add(x["txn_id"])
        for tr in trades:
            for o, n in zip(tr["owner_ids"], tr["owners"]):
                names.setdefault(o, n)
                all_ids.setdefault(o, set())
        rows = [dict(owner=names[o], total=len(ids) + tcount.get(o, 0),
                     team=f"{len(ids)} pickups + {tcount.get(o, 0)} trades") for o, ids in all_ids.items()]
        out.append(dict(id="most_txns", section=sect, title=f"Most transactions ({span_t})", unit="moves",
                        entries=top(rows, "total", extra=("team",))))
        # players
        pl = defaultdict(lambda: [0, 0])
        for x in txns:
            if "D/ST" in x["player"]:  # team defenses are churned constantly, so they are left out
                continue
            pl[x["player"]][0 if x["move"] == "ADD" else 1] += 1
        rows = [dict(owner=n, total=a + d, team=f"{a} adds, {d} drops") for n, (a, d) in pl.items()]
        out.append(dict(id="top_player", section=sect, title=f"Most-transacted player ({span_t})", unit="moves",
                        entries=top(rows, "total", extra=("team",))))
        # FAAB
        bids = [dict(x, team=x["player"]) for x in txns if x["move"] == "ADD" and x["bid"] > 0]
        if bids:
            faab_years = {b["season"] for b in bids}
            span_f = span(bids)
            out.append(dict(id="faab_single", section=sect, title=f"Most FAAB spent on one player ({span_f})",
                            unit="$", entries=top(bids, "bid", extra=("season", "team"))))
            spent = defaultdict(float)
            for b in bids:
                spent[b["owner_id"]] += b["bid"]
            yrs = defaultdict(set)
            for s_ in seasons:
                if s_["season"] in faab_years:
                    yrs[s_["owner_id"]].add(s_["season"])
            nm = {s_["owner_id"]: s_["owner"] for s_ in seasons}
            rows = [dict(owner=nm[o], total=round(spent.get(o, 0), 2), team=f"{len(y)} seasons")
                    for o, y in yrs.items() if len(y) >= MIN_FAAB_SEASONS]
            out.append(dict(id="faab_most", section=sect, title=f"Most FAAB spent all time ({span_f})", unit="$",
                            entries=top(rows, "total", extra=("team",))))
            out.append(dict(id="faab_least", section=sect,
                            title=f"Least FAAB spent all time ({span_f}, min {MIN_FAAB_SEASONS} seasons)", unit="$",
                            entries=top(rows, "total", extra=("team",), reverse=False)))
    if trades:
        tc, nm = defaultdict(int), {}
        for tr in trades:
            for o, n in zip(tr["owner_ids"], tr["owners"]):
                tc[o] += 1
                nm[o] = n
        rows = [dict(owner=nm[o], total=c) for o, c in tc.items()]
        out.append(dict(id="most_trades", section=sect, title=f"Most trades ({span(trades)})", unit="trades",
                        entries=top(rows, "total")))
    return out


# --- Record registry: add a new record = add one dict here ---------------
def build(games, seasons, playoffs, txns, trades):
    wins = decided_games(games)
    records = [
        dict(id="week_points", title="Most points in a single week", unit="pts",
             entries=top(games, "points", lambda v: round(v, 2), ("season", "week", "team"))),
        dict(id="week_low", title="Fewest points in a single week", unit="pts",
             entries=top(games, "points", lambda v: round(v, 2), ("season", "week", "team"),
                         reverse=False)),
        dict(id="blowout", title="Biggest blowout", unit="pts",
             entries=top(wins, "margin", extra=("season", "week", "team"))),
        dict(id="closest", title="Closest game", unit="pts",
             entries=top(wins, "margin", extra=("season", "week", "team"), reverse=False)),
        dict(id="season_points", title="Most points in a single season", unit="pts",
             entries=top(seasons, "points", extra=("season", "team"))),
        dict(id="season_wins", title="Most wins in a single season", unit="wins",
             entries=top(seasons, "wins", extra=("season", "team"))),
        dict(id="career_wins", title="Most wins all time", unit="wins",
             entries=career(seasons, "wins")),
        dict(id="career_points", title="Most points all time", unit="pts",
             entries=career(seasons, "points")),
        dict(id="win_streak", title="Longest win streak", unit="wins",
             entries=win_streaks(games)),
        dict(id="championships", title="Most championships", unit="titles",
             entries=championships(seasons)),
    ]
    pr = playoff_record(playoffs)
    if pr:
        records.append(dict(id="playoff_pct", title=f"Best playoff win % (min {MIN_PLAYOFF_GAMES} games)",
                            unit="win %", decimals=3, entries=pr))
    records.extend(txn_records(seasons, txns, trades))
    return records


if __name__ == "__main__":
    games, seasons, playoffs, txns, trades = load_rows()
    os.makedirs("data", exist_ok=True)
    review = sorted(
        [dict(owner=sorted(v["names"])[-1], names=sorted(v["names"]), ids=sorted(v["ids"]),
              seasons=sorted(v["seasons"])) for v in people.values()],
        key=lambda r: r["owner"].lower())
    with open("data/owners.json", "w") as f:
        json.dump(review, f, indent=2)
    with open("data/champions.json", "w") as f:
        json.dump({str(s["season"]): s["owner"] for s in
                   sorted(champion_rows(seasons), key=lambda s: s["season"])}, f, indent=2)
    with open("data/playoff_games.json", "w") as f:
        json.dump([dict(season=p["season"], week=p["week"], winner=p["owner"], loser=p["opp_owner"],
                        score=f"{p['pts']:.2f}-{p['opp_pts']:.2f}") for p in playoffs if p["won"]],
                  f, indent=2)
    summary = defaultdict(lambda: dict(pickups=0, drops=0, faab_bids=0, trades=0))
    for x in txns:
        sm = summary[x["season"]]
        if x["move"] == "ADD":
            sm["pickups"] += 1
            sm["faab_bids"] += 1 if x["bid"] > 0 else 0
        else:
            sm["drops"] += 1
    for tr in trades:
        summary[tr["season"]]["trades"] += 1
    with open("data/transactions_summary.json", "w") as f:
        json.dump({str(k): v for k, v in sorted(summary.items())}, f, indent=2)
    with open("data/trade_debug.json", "w") as f:
        json.dump(DEBUG, f, indent=2, sort_keys=True)
    with open("data/trades.json", "w") as f:
        json.dump([dict(season=t["season"], date=t["date"], teams=t["owners"], source=t.get("source")) for t in trades], f, indent=2)
    out = dict(generated=datetime.now(timezone.utc).isoformat(),
               seasons=sorted({s["season"] for s in seasons}),
               records=build(games, seasons, playoffs, txns, trades))
    with open("data/records.json", "w") as f:
        json.dump(out, f, indent=2)
    print("wrote data/records.json")
