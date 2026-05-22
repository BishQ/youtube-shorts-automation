"""
Telegram uploader for YouTube Shorts pipeline.

Watches ./data/jobs/ for completed renders and sends both final.mp4
and final_long.mp4 (when present) to a target Telegram chat as raw
documents (force_document=True — zero quality loss, no Telegram compression).

The caption includes the title, description, and tags from publish_package.json.

Usage:
    pip install telethon watchdog
    python tg_uploader.py

On first run Telethon will prompt for your phone number and a login code.
A session file (uploader.session) is saved so subsequent runs log in silently.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from telethon import TelegramClient, errors
from telethon.tl.types import DocumentAttributeFilename
from watchdog.events import FileCreatedEvent, FileSystemEventHandler
from watchdog.observers import Observer

# ── Configuration ─────────────────────────────────────────────────────────────

API_ID: int   = 0           # ← your api_id from https://my.telegram.org
API_HASH: str = ""          # ← your api_hash from https://my.telegram.org
TARGET_CHAT: int | str = 0  # ← numeric chat_id (e.g. 123456789) or "@username"

# Root of the pipeline data directory — matches settings.data_dir
JOBS_DIR = Path("./data/jobs")

# Persists which job directories have already been uploaded (survives restarts).
SENT_LOG = Path("./tg_uploader_sent.json")

# How long (seconds) to wait for publish_package.json / final_long.mp4 to appear
# after final.mp4 is detected before giving up and sending without them.
PACKAGE_WAIT_S = 120
LONG_WAIT_S    = 60

# Session file name (Telethon stores login credentials here).
SESSION_NAME = "uploader"

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("tg_uploader")

# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_sent() -> set[str]:
    if SENT_LOG.is_file():
        try:
            return set(json.loads(SENT_LOG.read_text()))
        except Exception:
            pass
    return set()


def _mark_sent(job_id: str) -> None:
    sent = _load_sent()
    sent.add(job_id)
    SENT_LOG.write_text(json.dumps(sorted(sent), indent=2))


def _build_caption(package: dict, label: str) -> str:
    """Format the Telegram message from a publish_package.json dict."""
    titles  = package.get("titles", {})
    main    = titles.get("main", "—")
    curiosity = titles.get("curiosity", "")
    seo     = titles.get("seo", "")
    desc    = package.get("description", "").strip()
    tags    = package.get("tags", [])
    hashtags = package.get("hashtags", [])

    tags_line     = ", ".join(tags[:20]) if tags else "—"
    hashtag_line  = " ".join(hashtags)  if hashtags else ""

    lines = [
        f"{'📹 NORMAL VERSION' if label == 'normal' else '🐌 LONG VERSION (0.9×)'}",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"🎬 Main:      {main}",
    ]
    if curiosity:
        lines.append(f"🔮 Curiosity: {curiosity}")
    if seo:
        lines.append(f"🔍 SEO:       {seo}")
    lines += [
        "",
        "📝 Description:",
        desc,
        "",
        f"🏷 Tags: {tags_line}",
    ]
    if hashtag_line:
        lines += ["", hashtag_line]

    caption = "\n".join(lines)
    # Telegram caption cap is 1024 chars for documents
    if len(caption) > 1020:
        caption = caption[:1020] + "…"
    return caption


async def _wait_for_file(path: Path, timeout_s: int) -> bool:
    """Poll until *path* exists and is non-empty, or timeout expires."""
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        if path.is_file() and path.stat().st_size > 0:
            return True
        await asyncio.sleep(3)
    return False


def _progress_callback(label: str):
    """Returns a Telethon progress callback that prints upload % to the console."""
    last: list[int] = [0]

    def _cb(sent: int, total: int) -> None:
        pct = int(sent / total * 100) if total else 0
        if pct != last[0]:
            last[0] = pct
            bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
            print(f"\r  {label}  [{bar}] {pct:3d}%", end="", flush=True)
            if pct == 100:
                print()  # newline after completion

    return _cb


# ── Core upload logic ─────────────────────────────────────────────────────────

async def upload_job(client: TelegramClient, job_dir: Path) -> None:
    job_id = job_dir.name
    final_mp4      = job_dir / "final.mp4"
    final_long_mp4 = job_dir / "final_long.mp4"
    package_json   = job_dir / "publish_package.json"

    log.info("[%s] Waiting for publish_package.json …", job_id[:8])
    if not await _wait_for_file(package_json, PACKAGE_WAIT_S):
        log.warning("[%s] publish_package.json never appeared — sending without metadata", job_id[:8])
        package = {}
    else:
        package = json.loads(package_json.read_text(encoding="utf-8"))

    # ── Send normal version ───────────────────────────────────────────────────
    log.info("[%s] Uploading final.mp4 …", job_id[:8])
    caption_normal = _build_caption(package, "normal")
    await client.send_file(
        TARGET_CHAT,
        final_mp4,
        caption=caption_normal,
        force_document=True,
        attributes=[DocumentAttributeFilename(file_name=final_mp4.name)],
        progress_callback=_progress_callback(f"[{job_id[:8]}] final.mp4"),
    )
    log.info("[%s] final.mp4 sent.", job_id[:8])

    # ── Send long version (if produced) ──────────────────────────────────────
    log.info("[%s] Waiting for final_long.mp4 …", job_id[:8])
    if await _wait_for_file(final_long_mp4, LONG_WAIT_S):
        caption_long = _build_caption(package, "long")
        log.info("[%s] Uploading final_long.mp4 …", job_id[:8])
        await client.send_file(
            TARGET_CHAT,
            final_long_mp4,
            caption=caption_long,
            force_document=True,
            attributes=[DocumentAttributeFilename(file_name=final_long_mp4.name)],
            progress_callback=_progress_callback(f"[{job_id[:8]}] final_long.mp4"),
        )
        log.info("[%s] final_long.mp4 sent.", job_id[:8])
    else:
        log.info("[%s] No final_long.mp4 found — skipping long version.", job_id[:8])

    _mark_sent(job_id)
    log.info("[%s] ✓ Done.", job_id[:8])


# ── Watchdog handler ──────────────────────────────────────────────────────────

class JobWatcher(FileSystemEventHandler):
    """Fires an asyncio task whenever a new final.mp4 is written."""

    def __init__(self, loop: asyncio.AbstractEventLoop, client: TelegramClient, sent: set[str]) -> None:
        self._loop   = loop
        self._client = client
        self._sent   = sent
        self._queued: set[str] = set()

    def on_created(self, event: FileCreatedEvent) -> None:
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.name != "final.mp4":
            return
        job_dir = path.parent
        job_id  = job_dir.name
        if job_id in self._sent or job_id in self._queued:
            return
        self._queued.add(job_id)
        log.info("[%s] New final.mp4 detected — queuing upload.", job_id[:8])
        asyncio.run_coroutine_threadsafe(self._upload(job_dir), self._loop)

    async def _upload(self, job_dir: Path) -> None:
        try:
            await upload_job(self._client, job_dir)
            self._sent.add(job_dir.name)
        except Exception as exc:
            log.error("[%s] Upload failed: %s", job_dir.name[:8], exc, exc_info=True)
        finally:
            self._queued.discard(job_dir.name)


# ── Startup scan ──────────────────────────────────────────────────────────────

async def scan_existing(client: TelegramClient, sent: set[str]) -> None:
    """On startup, upload any completed jobs that were missed while offline."""
    for job_dir in sorted(JOBS_DIR.iterdir()):
        if not job_dir.is_dir():
            continue
        job_id = job_dir.name
        if job_id in sent:
            continue
        final = job_dir / "final.mp4"
        if final.is_file() and final.stat().st_size > 0:
            log.info("[%s] Backfilling missed job …", job_id[:8])
            try:
                await upload_job(client, job_dir)
                sent.add(job_id)
            except Exception as exc:
                log.error("[%s] Backfill failed: %s", job_id[:8], exc, exc_info=True)


# ── Main ──────────────────────────────────────────────────────────────────────

async def main() -> None:
    if not API_ID or not API_HASH or not TARGET_CHAT:
        sys.exit(
            "ERROR: Set API_ID, API_HASH, and TARGET_CHAT at the top of this script."
        )

    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    sent = _load_sent()

    async with TelegramClient(SESSION_NAME, API_ID, API_HASH) as client:
        me = await client.get_me()
        log.info("Logged in as %s (@%s)", me.first_name, me.username)

        # Backfill any jobs that finished while the uploader was offline
        await scan_existing(client, sent)

        # Start watchdog observer on the jobs directory
        loop    = asyncio.get_event_loop()
        handler = JobWatcher(loop, client, sent)
        observer = Observer()
        observer.schedule(handler, str(JOBS_DIR), recursive=True)
        observer.start()
        log.info("Watching %s for new renders …  (Ctrl-C to stop)", JOBS_DIR.resolve())

        try:
            while True:
                await asyncio.sleep(1)
                if not observer.is_alive():
                    log.error("Watchdog observer died — restarting …")
                    observer.stop()
                    observer = Observer()
                    observer.schedule(handler, str(JOBS_DIR), recursive=True)
                    observer.start()
        except (KeyboardInterrupt, SystemExit):
            log.info("Shutting down …")
        finally:
            observer.stop()
            observer.join()


if __name__ == "__main__":
    asyncio.run(main())
