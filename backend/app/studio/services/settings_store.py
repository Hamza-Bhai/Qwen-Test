"""Studio settings kept in the ``studio_setting`` key/value table."""

from sqlalchemy.orm import Session

from app.studio.models import StudioSetting

KEYWORDS_KEY = "niche_keywords"
DEFAULT_KEYWORDS = ["sunday reset", "discipline", "morning routine", "self improvement"]
MAX_KEYWORDS = 20
MAX_KEYWORD_LENGTH = 60


AUTO_DISCOVERY_KEY = "auto_discovery"


def get_auto_discovery(db: Session) -> bool:
    row = db.get(StudioSetting, AUTO_DISCOVERY_KEY)
    return bool(row.value) if row is not None else False


def set_auto_discovery(db: Session, enabled: bool) -> bool:
    row = db.get(StudioSetting, AUTO_DISCOVERY_KEY)
    if row is None:
        db.add(StudioSetting(key=AUTO_DISCOVERY_KEY, value=bool(enabled)))
    else:
        row.value = bool(enabled)
    db.commit()
    return bool(enabled)


def get_keywords(db: Session) -> list[str]:
    row = db.get(StudioSetting, KEYWORDS_KEY)
    if row is None or not row.value:
        return list(DEFAULT_KEYWORDS)
    return list(row.value)


def set_keywords(db: Session, phrases: list[str]) -> list[str]:
    """Normalise (trim, collapse spaces, lowercase, drop blanks/duplicates) and save.

    Raises ValueError with a readable message; a rejected save changes nothing.
    """
    cleaned: list[str] = []
    for item in phrases:
        if not isinstance(item, str):
            raise ValueError("Keywords must be text.")
        phrase = " ".join(item.split()).lower()
        if phrase and phrase not in cleaned:
            cleaned.append(phrase)
    if not cleaned:
        raise ValueError("Add at least one keyword.")
    if len(cleaned) > MAX_KEYWORDS:
        raise ValueError(f"At most {MAX_KEYWORDS} keywords are allowed.")
    too_long = next((p for p in cleaned if len(p) > MAX_KEYWORD_LENGTH), None)
    if too_long is not None:
        raise ValueError(f"Each keyword must be at most {MAX_KEYWORD_LENGTH} characters.")

    row = db.get(StudioSetting, KEYWORDS_KEY)
    if row is None:
        db.add(StudioSetting(key=KEYWORDS_KEY, value=cleaned))
    else:
        row.value = cleaned
    db.commit()
    return cleaned
