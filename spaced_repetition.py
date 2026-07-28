"""Simplified SM-2 style spaced repetition: self-rated Again/Hard/Good/Easy grading.

This app runs on a daily study cadence, not sub-day Anki-style requeuing, so intervals
are measured in whole days. One entry per StableID is stored in progress['srs']:
{"interval": int, "ease": float, "reps": int, "due": "YYYY-MM-DD", "last_reviewed": str|None}
"""

from datetime import date, timedelta

DEFAULT_EASE = 2.5
MIN_EASE = 1.3
RATINGS = ("Again", "Hard", "Good", "Easy")


def new_entry():
    """A freshly-noted card that's never been through a graded review: due immediately."""
    return {"interval": 0, "ease": DEFAULT_EASE, "reps": 0, "due": date.today().isoformat(), "last_reviewed": None}


def is_due(entry, today=None):
    """Whether a card should show up in today's review queue."""
    today = today or date.today().isoformat()
    return entry["due"] <= today


def grade(entry, rating):
    """Return a new srs entry after grading a review with one of RATINGS.

    Cards with reps == 0 are still in initial learning and use fixed short steps rather
    than the ease-based formula, matching how Anki-style schedulers treat first exposure.
    """
    if rating not in RATINGS:
        raise ValueError(f"unknown rating: {rating!r}, expected one of {RATINGS}")

    today = date.today()
    ease = entry.get("ease", DEFAULT_EASE)
    reps = entry.get("reps", 0)
    interval = entry.get("interval", 0)

    if reps == 0:
        interval_days = {"Again": 0, "Hard": 1, "Good": 1, "Easy": 4}[rating]
        new_reps = 0 if rating == "Again" else 1
    elif rating == "Again":
        interval_days = 1
        ease = max(MIN_EASE, ease - 0.2)
        new_reps = 0
    elif rating == "Hard":
        interval_days = max(1, round(interval * 1.2))
        ease = max(MIN_EASE, ease - 0.15)
        new_reps = reps + 1
    elif rating == "Good":
        interval_days = max(1, round(interval * ease))
        new_reps = reps + 1
    else:  # Easy
        interval_days = max(1, round(interval * ease * 1.3))
        ease = ease + 0.15
        new_reps = reps + 1

    return {
        "interval": interval_days,
        "ease": round(ease, 2),
        "reps": new_reps,
        "due": (today + timedelta(days=interval_days)).isoformat(),
        "last_reviewed": today.isoformat(),
    }
