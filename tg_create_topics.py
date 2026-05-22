"""Auto-create Telegram forum topics for each pipeline niche.

Reads niche names from config/tg_topics.json (_suggested_topic_names),
creates any missing topics in the forum supergroup, then writes topic IDs
back into config/tg_topics.json.

Usage:
    python tg_create_topics.py
    python tg_create_topics.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from shorts_pipeline.config.settings import Settings  # noqa: E402
from shorts_pipeline.planner.niches import list_niches  # noqa: E402
from tg_forum import (  # noqa: E402
    list_forum_topics,
    peer_chat_id,
    resolve_forum_entity,
)

# Telegram forum topic icon colors (preset palette).
_ICON_COLORS = (0x6FB9F0, 0xFFD67E, 0xCB86DB, 0x8EEE98, 0xFF93B2, 0xFB6F5F)


def _load_config(path: Path) -> dict:
    if not path.is_file():
        raise SystemExit(f"Config not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _save_config(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _topic_title(cfg: dict, niche: str) -> str:
    suggested = cfg.get("_suggested_topic_names") or {}
    if niche in suggested and suggested[niche]:
        return str(suggested[niche]).strip()
    return niche.replace("_", " ").title()


def _normalize_title(text: str) -> str:
    return " ".join(text.casefold().split())


def _console(text: str) -> str:
    return text.encode("ascii", "replace").decode("ascii")


async def _create_topic(client, entity, title: str, *, color_idx: int) -> int | None:
    from telethon.tl.functions.messages import CreateForumTopicRequest

    try:
        await client(
            CreateForumTopicRequest(
                peer=entity,
                title=title,
                icon_color=_ICON_COLORS[color_idx % len(_ICON_COLORS)],
                random_id=secrets.randbits(63),
            )
        )
    except Exception as exc:
        err = str(exc).casefold()
        if "too many" in err:
            raise SystemExit(
                "Telegram topic limit reached for this group. "
                "Delete unused topics or create a second group."
            ) from exc
        print(f"  FAILED to create {title!r}: {exc}")
        return None

    existing = await list_forum_topics(client, entity)
    want = _normalize_title(title)
    for tid, name in existing:
        if _normalize_title(name) == want:
            return tid
    return existing[-1][0] if existing else None


async def _main(*, dry_run: bool) -> None:
    settings = Settings()
    if not settings.tg_api_id or not settings.tg_api_hash:
        raise SystemExit("Set SHORTS_TG_API_ID and SHORTS_TG_API_HASH in .env")

    try:
        from telethon import TelegramClient
    except ImportError as exc:
        raise SystemExit("pip install telethon") from exc

    topics_path = settings.tg_topics_path
    if not topics_path.is_absolute():
        topics_path = ROOT / topics_path

    cfg = _load_config(topics_path)
    topic_map: dict[str, int] = {}
    for niche, val in (cfg.get("topics") or {}).items():
        if str(niche).startswith("_"):
            continue
        try:
            topic_map[str(niche)] = int(val)
        except (TypeError, ValueError):
            topic_map[str(niche)] = 0

    async with TelegramClient(settings.tg_session_path, settings.tg_api_id, settings.tg_api_hash) as client:
        entity = await resolve_forum_entity(client, settings)
        print(f"Group: {getattr(entity, 'title', entity.id)}")
        print(f"Chat ID: {peer_chat_id(entity)}")
        print("-" * 60)

        existing = await list_forum_topics(client, entity)
        by_title = {_normalize_title(name): tid for tid, name in existing}
        print(f"Existing topics: {len(existing)}")
        for tid, name in existing:
            print(f"  {tid:>4}  {_console(name)}")

        created = 0
        reused = 0
        print("\nNiche topics:")
        labels = {slug: label for slug, label in list_niches()}
        niche_keys = sorted(set(topic_map.keys()) | set((cfg.get("topics") or {}).keys()))
        for idx, niche in enumerate(niche_keys):
            if str(niche).startswith("_"):
                continue
            title = _topic_title(cfg, niche)
            norm = _normalize_title(title)
            shown = _console(title)
            label = labels.get(niche, niche)

            if topic_map.get(niche, 0) > 0:
                print(f"  keep  {niche:<14} id={topic_map[niche]}  ({shown})")
                continue

            if norm in by_title:
                topic_map[niche] = by_title[norm]
                reused += 1
                print(f"  match {niche:<14} id={by_title[norm]}  ({shown})")
                continue

            if dry_run:
                print(f"  would create {niche:<14}  ({shown})")
                continue

            tid = await _create_topic(client, entity, title, color_idx=idx)
            if tid is not None:
                topic_map[niche] = tid
                by_title[norm] = tid
                created += 1
                print(f"  new   {niche:<14} id={tid}  ({shown})")

        if dry_run:
            print("\nDry run only - no topics created, config not changed.")
            return

        cfg["topics"] = topic_map
        if not cfg.get("_default_topic_id"):
            cfg["_default_topic_id"] = 1
        _save_config(topics_path, cfg)

        print("\n" + "=" * 60)
        print(f"Done - created {created}, matched {reused} existing.")
        print(f"Updated: {topics_path}")
        print("Run  python tg_list_topics.py  to verify.")


def main() -> None:
    ap = argparse.ArgumentParser(description="Create Telegram forum topics per niche.")
    ap.add_argument("--dry-run", action="store_true", help="Show actions without creating topics.")
    args = ap.parse_args()
    asyncio.run(_main(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
