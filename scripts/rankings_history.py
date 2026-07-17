"""
Saves each week's power rankings to disk so next week's run has
something to compare against (for the rank_jump / rank_drop news events).

Only the most recent snapshot is kept — this isn't a full history log,
just a one-week memory.
"""

import os
import pandas as pd

DEFAULT_PATH = "data/previous_rankings.json"


def load_previous_rankings(path=DEFAULT_PATH):
    """Returns the last saved rankings DataFrame, or None if there isn't one yet."""
    if not os.path.exists(path):
        return None
    try:
        return pd.read_json(path, orient="records")
    except (ValueError, FileNotFoundError):
        return None


def save_current_rankings(df, path=DEFAULT_PATH):
    """Saves this week's rankings so next week's run can diff against it."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    df.to_json(path, orient="records")
