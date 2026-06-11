"""Seed the single MVP user (CLAUDE.md §14). Run once: python -m app.seed"""

import asyncio

from sqlalchemy import select

from app.config import get_settings
from app.db import get_session_factory
from app.spine.models import User


async def seed() -> None:
    settings = get_settings()
    async with get_session_factory()() as session:
        existing = (await session.execute(select(User).limit(1))).scalar_one_or_none()
        if existing is not None:
            print(f"user already seeded: {existing.display_name} <{existing.email}>")
            return
        user = User(display_name=settings.seed_user_display_name, email=settings.seed_user_email)
        session.add(user)
        await session.commit()
        print(f"seeded user: {user.display_name} <{user.email}>")


if __name__ == "__main__":
    asyncio.run(seed())
