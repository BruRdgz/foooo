"""Optional local inspection API; importing it does not require FastAPI."""

from .app import create_app

__all__ = ["create_app"]
