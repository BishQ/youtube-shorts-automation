"""Shared Telethon helpers for forum supergroup setup."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from telethon import TelegramClient

    from shorts_pipeline.config.settings import Settings


def peer_chat_id(entity) -> int:
    from telethon import utils

    return utils.get_peer_id(entity)


def is_forum_supergroup(entity) -> bool:
    from telethon.tl.types import Channel

    return (
        isinstance(entity, Channel)
        and bool(entity.megagroup)
        and bool(getattr(entity, "forum", False))
    )


async def find_forum_group(client: TelegramClient, *, title_hint: str = ""):
    from telethon.tl.types import Channel, Chat

    hint = title_hint.lower()
    fallback = None
    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        title = getattr(entity, "title", "") or ""
        if hint and hint not in title.lower():
            continue
        if is_forum_supergroup(entity):
            return entity
        if isinstance(entity, (Channel, Chat)):
            fallback = entity
    return fallback


def parse_chat(raw: str) -> int | str | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return raw


async def resolve_forum_entity(client: TelegramClient, settings: Settings):
    from telethon.tl.functions.messages import ImportChatInviteRequest

    chat = parse_chat(settings.tg_target_chat)
    if chat is not None:
        entity = await client.get_entity(chat)
        if not is_forum_supergroup(entity):
            raise SystemExit(
                "SHORTS_TG_TARGET_CHAT is not a forum supergroup.\n"
                "Enable Topics in Telegram: Manage -> Topics -> Enable -> Save."
            )
        return entity

    invite = (settings.tg_invite_link or "").strip()
    if not invite:
        raise SystemExit(
            "Set SHORTS_TG_TARGET_CHAT or SHORTS_TG_INVITE_LINK in .env."
        )

    m = re.search(r"(?:t\.me/\+|\+)([A-Za-z0-9_-]+)", invite)
    if not m:
        raise SystemExit(f"Invalid invite link: {invite!r}")

    hint = (getattr(settings, "tg_forum_title_hint", "") or "").strip()

    hash_part = m.group(1)
    try:
        updates = await client(ImportChatInviteRequest(hash_part))
    except Exception as exc:
        err = str(exc).lower()
        if "already" in err or "participant" in err:
            entity = await find_forum_group(client, title_hint=hint) if hint else None
            if entity is not None:
                return entity
            raise SystemExit(
                "Already in group but forum supergroup not found.\n"
                "Set SHORTS_TG_TARGET_CHAT to the group ID, or set "
                "SHORTS_TG_FORUM_TITLE_HINT to a substring of the group title, "
                "then enable Topics in Telegram and retry."
            ) from exc
        raise

    if hasattr(updates, "chats") and updates.chats:
        entity = updates.chats[0]
        if not is_forum_supergroup(entity) and hint:
            found = await find_forum_group(client, title_hint=hint)
            if found is not None:
                return found
        return entity
    raise SystemExit("Joined group but could not resolve chat entity.")


async def list_forum_topics(client: TelegramClient, entity) -> list[tuple[int, str]]:
    from telethon.tl.functions.messages import GetForumTopicsRequest

    result = await client(
        GetForumTopicsRequest(
            peer=entity,
            offset_date=None,
            offset_id=0,
            offset_topic=0,
            limit=100,
        )
    )
    rows: list[tuple[int, str]] = []
    for topic in result.topics:
        tid = int(topic.id)
        name = getattr(topic, "title", None) or f"topic-{tid}"
        rows.append((tid, name))
    rows.sort(key=lambda r: r[0])
    return rows
