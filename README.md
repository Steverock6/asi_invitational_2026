# The Adam Szachara Invitational — League Hub

A self-updating site for our ESPN fantasy league. Every Tuesday morning a GitHub Action pulls fresh data from ESPN and rebuilds everything on the page.

## What's on the site

- **DonBot** — the league's robot beat writer posts about blowouts, upsets, trades, streaks and big waiver bids
- **Weekly awards** — Top Dog, Low Energy, Bad Beat, Bench Blunder and more
- **Matchups + DonBot's picks** — win odds for every game, and his running pick record
- **Power rankings** — score, week-over-week change, season average and a trend line
- **Standings** — streaks, a luck rating and the playoff line
- **Transactions** — every waiver claim, free-agent move, drop and trade
- **League History** — the all-time record book

## Things you can do

**Give DonBot a story idea.** Open `data/custom_story_ideas.txt`, click the pencil, add one idea per line in plain English, and commit. He'll post about it on the next update, then the file clears itself.

**Refresh the site right now.** Actions tab → Weekly League Update → Run workflow.

**Set last season's champion (for the crown).** Edit `data/champions.json`.

## Where things live

| File | What it does |
| --- | --- |
| `index.html` | The whole website |
| `data/data.json` | This week's data (written by the weekly job; don't edit by hand) |
| `scripts/main.py` | The weekly job |
| `scripts/power_rankings.py` | Power ranking formula and week-by-week history |
| `scripts/weekly_extras.py` | Awards, matchups, picks, standings extras |
| `scripts/transactions.py` | Transaction log |
| `scripts/news_events.py` / `news_generator.py` | What DonBot posts about, and how he says it |
| `scripts/build_records.py` | All-time record book |
