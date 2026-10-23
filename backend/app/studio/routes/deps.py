"""FastAPI dependencies shared by the Studio routes."""

from fastapi import Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.studio.services.serpapi import SerpApiClient


def get_serpapi(db: Session = Depends(get_db)) -> SerpApiClient:
    """One SerpApi client per request; tests override this to inject a fake HTTP layer."""
    return SerpApiClient(db)
