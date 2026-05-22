"""
Telegram auto-send: fires after every publish stage completes.

Message layout per job:
  1. Text message  — all copy-paste metadata (titles, description, tags).
                     No character limit; everything fits, nothing is cut.
  2. final.mp4     — raw document, force_document=True, minimal caption.
  3. final_long.mp4 — same, if produced by the pipeline.

Called synchronously from the orchestrator (background thread).
asyncio.run() is safe here because worker threads have no running event loop.

Enable in .env:
    SHORTS_TG_API_ID=12345678
    SHORTS_TG_API_HASH=abcdef...
    SHORTS_TG_TARGET_CHAT=123456789   (numeric user ID or @username)

First run only: Telethon will prompt for your phone + SMS code, then save
tg_session.session.  Every run after that logs in silently.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

_SEP = "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"


def _read_figure(job_dir: Path) -> str:
    """Pull historical_figure from plan.json — fallback to empty string."""
    plan_path = job_dir / "plan.json"
    if plan_path.is_file():
        try:
            data = json.loads(plan_path.read_text(encoding="utf-8"))
            return str(data.get("historical_figure", "")).strip()
        except Exception:
            pass
    return ""


def _metadata_message(figure: str, package: dict) -> str:
    """
    Full copy-paste block sent as a plain text message (no 1024-char limit).
    Designed to be read top-to-bottom and used directly for YouTube upload.
    """
    titles   = package.get("titles", {})
    main     = titles.get("main", "—")
    curiosity = titles.get("curiosity", "")
    seo      = titles.get("seo", "")
    desc     = package.get("description", "").strip()
    tags     = package.get("tags", [])

    label = f"🎬  {figure}  ·  Video Ready" if figure else "🎬  Video Ready"

    lines = [
        label,
        _SEP,
        "",
        "TITLES  — pick one for YouTube",
        f"  ①  {main}",
    ]
    if curiosity:
        lines.append(f"  ②  {curiosity}")
    if seo:
        lines.append(f"  ③  {seo}")

    lines += [
        "",
        _SEP,
        "",
        "DESCRIPTION  (copy → YouTube description box)",
        desc,
        "",
        _SEP,
    ]

    if tags:
        lines += [
            "",
            "TAGS  (copy → YouTube tags field)",
            ", ".join(tags),
            "",
            _SEP,
        ]

    has_long = True   # pipeline always tries to produce it; sender checks at send time
    version_hint = "📹 normal.mp4  ·  🐌 long.mp4  ↓ below" if has_long else "📹 normal.mp4  ↓ below"
    lines += ["", version_hint]

    return "\n".join(lines)


def _file_caption(figure: str, version: str) -> str:
    """Short caption attached directly to the video document."""
    tag = "📹 NORMAL" if version == "normal" else "🐌 LONG  (0.9×)"
    return f"{tag}  ·  {figure}" if figure else tag


def _progress(label: str):
    """Console upload-progress bar."""
    last: list[int] = [-1]

    def _cb(sent: int, total: int) -> None:
        pct = int(sent / total * 100) if total else 0
        if pct != last[0]:
            last[0] = pct
            bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
            print(f"\r  {label}  [{bar}] {pct:3d}%", end="", flush=True)
            if pct == 100:
                print()

    return _cb


async def _send(
    api_id: int,
    api_hash: str,
    session_path: str,
    target_chat: int | str,
    job_dir: Path,
    package: dict,
) -> None:
    from telethon import TelegramClient
    from telethon.tl.types import DocumentAttributeFilename

    final_mp4      = job_dir / "final.mp4"
    final_long_mp4 = job_dir / "final_long.mp4"
    figure         = _read_figure(job_dir)
    has_long       = final_long_mp4.is_file() and final_long_mp4.stat().st_size > 0

    async with TelegramClient(session_path, api_id, api_hash) as client:

        # ── 1. Metadata text message ──────────────────────────────────────────
        meta_text = _metadata_message(figure, package)
        log.info("tg_send_metadata")
        await client.send_message(target_chat, meta_text)

        # ── 2. Normal version ─────────────────────────────────────────────────
        log.info("tg_send_normal_start")
        await client.send_file(
            target_chat,
            final_mp4,
            caption=_file_caption(figure, "normal"),
            force_document=True,
            attributes=[DocumentAttributeFilename(file_name="final.mp4")],
            progress_callback=_progress(f"{job_dir.name[:8]}  final.mp4"),
        )
        log.info("tg_send_normal_done")

        # ── 3. Long version ───────────────────────────────────────────────────
        if has_long:
            log.info("tg_send_long_start")
            await client.send_file(
                target_chat,
                final_long_mp4,
                caption=_file_caption(figure, "long"),
                force_document=True,
                attributes=[DocumentAttributeFilename(file_name="final_long.mp4")],
                progress_callback=_progress(f"{job_dir.name[:8]}  final_long.mp4"),
            )
            log.info("tg_send_long_done")
        else:
            log.info("tg_send_long_skipped")


def send_job(
    *,
    api_id: int,
    api_hash: str,
    session_path: str,
    target_chat: str,
    job_dir: Path,
    package_json: Path,
) -> None:
    """Synchronous entry point — safe to call from any background thread."""
    if not api_id or not api_hash or not target_chat:
        log.debug("tg_notify_disabled: credentials not set in .env")
        return

    if not package_json.is_file():
        log.warning("tg_notify_skipped: publish_package.json not found at %s", package_json)
        return

    package = json.loads(package_json.read_text(encoding="utf-8"))

    chat: int | str = target_chat
    try:
        chat = int(target_chat)
    except (ValueError, TypeError):
        pass

    try:
        asyncio.run(
            _send(
                api_id=api_id,
                api_hash=api_hash,
                session_path=session_path,
                target_chat=chat,
                job_dir=job_dir,
                package=package,
            )
        )
    except Exception as exc:
        log.error("tg_notify_failed: %s — %s", type(exc).__name__, exc, exc_info=True)
