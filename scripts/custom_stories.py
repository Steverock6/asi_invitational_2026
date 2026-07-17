"""
Lets you feed your own story ideas into the news generator — for things
the automated detectors can't know about, like a group chat rivalry or a
manager's bold prediction.

How to use it: open data/custom_story_ideas.txt in your GitHub repo (or
locally), add one idea per line in plain English, and save/commit. The
next time the weekly job runs, each line becomes a news event the AI
writer turns into a headline + blurb, same as the automated events.
Lines starting with # are treated as comments and ignored.

After a run, the file is cleared automatically so ideas don't repeat
week after week.
"""

import os

DEFAULT_PATH = "data/custom_story_ideas.txt"

FILE_HEADER = (
    "# Add one story idea per line below, in plain English.\n"
    "# Example: Team Gamble and Ice Cold Blooded have a running feud and\n"
    "# face off this week — write it up as a grudge match.\n"
    "#\n"
    "# Lines starting with # are ignored. This file clears itself after\n"
    "# each weekly run, so add new ideas any time before Tuesday.\n"
)


def load_custom_events(path=DEFAULT_PATH):
    """
    Reads the custom story idea file and returns a list of event dicts
    (type "custom_prompt") ready to hand to the news generator. Returns
    an empty list if the file doesn't exist or has no ideas in it.
    """
    if not os.path.exists(path):
        return []

    with open(path, "r") as f:
        lines = f.readlines()

    events = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        events.append({"type": "custom_prompt", "note": line})

    return events


def clear_custom_events(path=DEFAULT_PATH):
    """Resets the file back to just the instructional header/comments."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(FILE_HEADER)


def ensure_file_exists(path=DEFAULT_PATH):
    """Creates the file with instructions if it doesn't exist yet."""
    if not os.path.exists(path):
        clear_custom_events(path)


if __name__ == "__main__":
    ensure_file_exists()
    events = load_custom_events()
    print(f"Found {len(events)} custom story idea(s).")
    for e in events:
        print(e)
