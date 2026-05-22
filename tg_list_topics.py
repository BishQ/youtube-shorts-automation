"""List forum topics in your Telegram supergroup — paste IDs into config/tg_topics.json.

Setup (one time):
  1. Telegram -> New Group -> Convert to Supergroup
  2. Group Settings -> Topics -> Enable
  3. python tg_create_topics.py   (auto-create niche topics)
  4. python tg_list_topics.py     (verify IDs)

Reads SHORTS_TG_* from .env. First run prompts for phone + SMS code (same as pipeline).
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from shorts_pipeline.config.settings import Settings  # noqa: E402
from shorts_pipeline.planner.niches import list_niches  # noqa: E402
from tg_forum import list_forum_topics, peer_chat_id, resolve_forum_entity  # noqa: E402


async def _main() -> None:
    settings = Settings()
    if not settings.tg_api_id or not settings.tg_api_hash:
        raise SystemExit(
            "Set SHORTS_TG_API_ID and SHORTS_TG_API_HASH in .env "
            "(get them from https://my.telegram.org)"
        )

    try:
        from telethon import TelegramClient
    except ImportError as exc:
        raise SystemExit("pip install telethon") from exc

    topics_path = settings.tg_topics_path
    if not topics_path.is_absolute():
        topics_path = ROOT / topics_path

    async with TelegramClient(settings.tg_session_path, settings.tg_api_id, settings.tg_api_hash) as client:
        entity = await resolve_forum_entity(client, settings)
        title = getattr(entity, "title", None) or str(entity.id)
        chat_id = peer_chat_id(entity)

        print(f"\nGroup: {title}")
        print(f"Chat ID: {chat_id}")
        print()
        print(">>> Copy this into .env:")
        print(f"SHORTS_TG_TARGET_CHAT={chat_id}")
        print("-" * 60)

        rows = await list_forum_topics(client, entity)
        if not rows:
            print("No topics found. Run: python tg_create_topics.py")
            return

        print(f"{'ID':>6}  {'Topic name':<40}  Closed")
        print("-" * 60)
        for tid, name in rows:
            print(f"{tid:>6}  {name:<40}")

        existing: dict[str, int] = {}
        if topics_path.is_file():
            try:
                raw = json.loads(topics_path.read_text(encoding="utf-8"))
                for k, v in (raw.get("topics") or {}).items():
                    if not str(k).startswith("_"):
                        try:
                            existing[str(k)] = int(v)
                        except (TypeError, ValueError):
                            pass
            except Exception:
                pass

        print("\n" + "=" * 60)
        print("Suggested paste for config/tg_topics.json -> topics:")
        print("=" * 60)
        for slug, label in list_niches():
            matched = existing.get(slug, 0)
            print(f'    "{slug}": {matched},  // {label}')
        print()
        print("Replace 0 with the topic ID from the table above.")
        print("Set _default_topic_id to the General topic ID (usually 1).")
        print(f"\nConfig file: {topics_path}")


if __name__ == "__main__":
    asyncio.run(_main())
