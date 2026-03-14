"""API key authentication for FDA-watch."""

import os

from fastapi import Depends, HTTPException, Request


def verify_api_key(key: str) -> bool:
    """Check if the provided API key matches the configured one."""
    expected = os.environ.get("FDA_WATCH_API_KEY")
    if not expected:
        return True  # Auth disabled if env var not set
    return key == expected


async def require_auth(request: Request) -> None:
    """FastAPI dependency — checks X-API-Key header on write endpoints.

    If FDA_WATCH_API_KEY env var is not set, auth is disabled (local dev mode).
    """
    expected = os.environ.get("FDA_WATCH_API_KEY")
    if not expected:
        return  # Auth disabled

    api_key = request.headers.get("X-API-Key", "")
    if not api_key or api_key != expected:
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API key",
            headers={"WWW-Authenticate": "ApiKey"},
        )
