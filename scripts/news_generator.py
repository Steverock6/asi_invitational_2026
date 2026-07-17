"""
Turns the structured event facts from news_events.py into short,
ESPN-style headline + blurb news stories using the Anthropic API.

The model is only ever given the facts dict for each event and explicit
instructions not to invent anything beyond them. This keeps the news
feed accurate even though the writing itself is generated.
"""

import json
import os
from anthropic import Anthropic

MODEL = "claude-haiku-4-5-20251001"

SYSTEM_PROMPT = """You are a fantasy football beat writer for a private home league. \
You write short, punchy news blurbs in the style of an NFL news ticker \
(think ESPN or NFL Network breaking news alerts), but scoped only to \
this fantasy league's own games, trades, and standings.

Rules you must follow:
- Use ONLY the facts given to you in each event. Never invent player names, \
injuries, stats, or details not present in the event data.
- Each story needs a short headline (under 12 words) and a 2-3 sentence body.
- Tone: energetic, a little playful/dramatic, like real sports news, but \
never mean-spirited toward any specific manager.
- Do not use emoji.
- Return ONLY valid JSON, no other text: a JSON array of objects, each with \
"headline" and "body" keys, in the same order as the events given to you.

Playoff context — pay close attention to these fields when present:
- If an event has "is_playoff": true, raise the stakes in your writing. Use \
the "playoff_round" field (quarterfinal, semifinal, championship) to frame \
what's on the line — a quarterfinal loss ends a season, a championship win \
crowns a winner.
- An event of type "playoff_picture" summarizes the playoff race using \
"teams_in" (currently holding a playoff spot) and "teams_on_bubble" (just \
outside, fighting for it). Write this as a playoff-race roundup story, \
building tension around the final stretch.
- Never write about, mention, or speculate on teams that are not present in \
the event data — if a team isn't mentioned in an event, they are not part \
of that story.

Custom prompts — an event of type "custom_prompt" contains a "note" field \
written by the league commissioner with a story idea (e.g. a rivalry, a \
running joke, a bold prediction). Unlike other events, you may write more \
freely and colorfully around this note, since it reflects real context the \
commissioner wants covered. Still keep it to a headline + 2-3 sentence body, \
and don't invent specific stats or scores that weren't in the note."""


def _build_user_prompt(events):
    return (
        "Write one news story for each of these fantasy football league events. "
        "Return a JSON array with one {\"headline\": ..., \"body\": ...} object "
        "per event, in the same order:\n\n" + json.dumps(events, indent=2)
    )


def generate_news_stories(events):
    """
    Takes a list of event fact dicts (from news_events.py) and returns a
    list of {"headline": ..., "body": ...} dicts, same length and order.
    Returns an empty list if there are no events to write about.
    """
    if not events:
        return []

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("Missing ANTHROPIC_API_KEY environment variable.")

    client = Anthropic(api_key=api_key)

    response = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _build_user_prompt(events)}],
    )

    raw_text = response.content[0].text.strip()

    # Defensive cleanup in case the model wraps the JSON in a code fence
    # despite instructions not to.
    if raw_text.startswith("```"):
        raw_text = raw_text.strip("`")
        if raw_text.startswith("json"):
            raw_text = raw_text[4:]
        raw_text = raw_text.strip()

    try:
        stories = json.loads(raw_text)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Could not parse AI response as JSON: {e}\nRaw response: {raw_text[:500]}")

    if len(stories) != len(events):
        raise RuntimeError(
            f"Expected {len(events)} stories back, got {len(stories)}. Raw response: {raw_text[:500]}"
        )

    return stories


if __name__ == "__main__":
    from espn_connector import get_league
    from power_rankings import calculate_power_rankings
    from news_events import get_weekly_events

    league = get_league()
    rankings = calculate_power_rankings(league)
    events = get_weekly_events(league, rankings)

    stories = generate_news_stories(events)
    for story in stories:
        print(story["headline"])
        print(story["body"])
        print()
