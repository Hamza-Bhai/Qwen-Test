"""Small text helpers for Growth Studio."""

import re

# A '#' at the start or after whitespace (so URL fragments and "C#" are not
# tags), followed by word characters (unicode letters, digits, underscore).
_HASHTAG = re.compile(r"(?:(?<=\s)|^)#(\w+)", re.MULTILINE)


def extract_hashtags(*texts: str | None) -> list[str]:
    """Lowercase hashtags (without '#'), de-duplicated in first-seen order.

    All-digit tags such as ``#2024`` are ignored, as YouTube does not treat
    them as hashtags.
    """
    seen: list[str] = []
    for text in texts:
        if not text:
            continue
        for match in _HASHTAG.finditer(text):
            tag = match.group(1).lower()
            if tag.isdigit() or tag in seen:
                continue
            seen.append(tag)
    return seen
