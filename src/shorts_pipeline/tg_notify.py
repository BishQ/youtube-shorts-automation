"""
Telegram auto-send: fires after every publish stage completes.

Human-readable layout per job — each block is a separate message group:

  BLOCK A — SHORTS
    1. Text: YouTube title / description / tags (copy-paste)
    2. File: final.mp4

  BLOCK B — LONG  (if rendered)
    3. Text: optional long-upload note
    4. File: final_long.mp4

  BLOCK C — OPERATOR ONLY  (if archive enabled)
    5. Text: backup note
    6. File: project zip (json, images, wav — no mp4 dupes)

Forum topic routing via config/tg_topics.json (niche slug -> topic id).
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import re
import tempfile
import threading
import zipfile
from pathlib import Path
from typing import Any

from shorts_pipeline.tg_topics import load_topic_map, resolve_topic_id

log = logging.getLogger(__name__)

_send_queue: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=64)
_worker_lock = threading.Lock()
_worker_started = False

# Idempotency: written into job_dir after Block A (shorts text+video) succeeds.
# A second send_job call with the marker present is a no-op.
_SENT_MARKER = ".tg_sent"

# Operator backup zip skips logs/caches AND rendered deliverables (sent separately).
_ARCHIVE_SKIP_SUFFIXES = {".log", ".tmp", ".part"}
_ARCHIVE_SKIP_DIRS = {"__pycache__", ".cache"}
_ARCHIVE_SKIP_FILES = {"final.mp4", "final_long.mp4", "final_wan.mp4", "final_wan_long.mp4"}

_HDR = "══════════════════════════════════"
_SEP = "──────────────────────────────────"
_BLOCK_PAUSE_S = 2.0   # gap between shorts / long / operator blocks


def _read_figure(job_dir: Path) -> str:
    plan_path = job_dir / "plan.json"
    if plan_path.is_file():
        try:
            data = json.loads(plan_path.read_text(encoding="utf-8"))
            return str(data.get("historical_figure", "")).strip()
        except Exception:
            pass
    return ""


def _figure_slug(figure: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", figure).strip("_")
    return (slug[:40] or "video")


def _niche_label(niche: str | None) -> str:
    if not niche:
        return ""
    try:
        from shorts_pipeline.planner.niches import niche_label

        return niche_label(niche)
    except Exception:
        return niche.replace("_", " ").title()


def _header_line(figure: str, *, niche: str | None) -> str:
    niche_tag = _niche_label(niche)
    if figure and niche_tag:
        return f"🎬  {niche_tag.upper()}  ·  {figure}"
    if figure:
        return f"🎬  {figure}"
    if niche_tag:
        return f"🎬  {niche_tag.upper()}"
    return "🎬  VIDEO READY"


def _shorts_metadata_message(figure: str, package: dict, *, niche: str | None) -> str:
    """YouTube copy-paste block — sent on its own before the shorts video."""
    titles = package.get("titles", {})
    main = titles.get("main", "—")
    curiosity = titles.get("curiosity", "")
    seo = titles.get("seo", "")
    desc = package.get("description", "").strip()
    tags = package.get("tags", [])

    lines = [
        _header_line(figure, niche=niche),
        _HDR,
        "",
        "✅  SHORTS UPLOAD",
        _SEP,
        "Video file comes in the NEXT message.",
        "Download it and upload to YouTube Shorts as-is.",
        "",
        "📋  COPY TO YOUTUBE",
        "",
        "TITLE  (pick one)",
        f"  A)  {main}",
    ]
    if curiosity:
        lines.append(f"  B)  {curiosity}")
    if seo:
        lines.append(f"  C)  {seo}")

    lines += ["", "DESCRIPTION", desc]

    if tags:
        lines += ["", "TAGS", ", ".join(tags)]

    return "\n".join(lines)


def _long_header_message(figure: str, *, niche: str | None) -> str:
    who = _header_line(figure, niche=niche)
    return "\n".join([
        who,
        _HDR,
        "",
        "✅  LONG UPLOAD  (optional)",
        _SEP,
        "Video file comes in the NEXT message.",
        "Long version ~66 s  (0.9× speed).",
        "Use for a second upload or keep as backup.",
    ])


def _operator_header_message(figure: str, job_name: str) -> str:
    who = figure or job_name
    return "\n".join([
        _HDR,
        "",
        "🔒  OPERATOR ONLY",
        _SEP,
        f"Project: {who}",
        "Zip file comes in the NEXT message.",
        "",
        "Contains: plan.json · images/ · clips/ · narration.wav · json",
        "NOT for YouTube upload — backup / re-edit only.",
    ])


def _video_caption(*, slot: str, label: str, filename: str) -> str:
    return f"✅ {slot}\n{label}\n{filename}"


def _archive_caption(*, figure: str, job_name: str) -> str:
    who = figure or job_name
    return (
        "🔒 OPERATOR ONLY · project backup\n"
        f"{job_name}.zip\n"
        f"{who}\n"
        "Not for YouTube — json, images, audio, clips"
    )


def _upload_filename(figure: str, kind: str) -> str:
    return f"{_figure_slug(figure)}_{kind}_upload.mp4"


def _forum_reply_to(topic_id: int | None):
    """Topic thread id for forum supergroups (plain int for Telethon 1.x)."""
    if topic_id and topic_id > 0:
        return topic_id
    return None


def _progress(label: str):
    last: list[int] = [-1]

    def _cb(sent: int, total: int) -> None:
        pct = int(sent / total * 100) if total else 0
        if pct != last[0]:
            last[0] = pct
            bar = "#" * (pct // 5) + "-" * (20 - pct // 5)
            print(f"\r  {label}  [{bar}] {pct:3d}%", end="", flush=True)
            if pct == 100:
                print()

    return _cb


def _build_archive(job_dir: Path, dest_zip: Path) -> int:
    """Zip source materials only (rendered mp4s are sent separately as uploads)."""
    total = 0
    with zipfile.ZipFile(dest_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in job_dir.rglob("*"):
            if path.is_dir():
                continue
            if path.name in _ARCHIVE_SKIP_FILES:
                continue
            if any(part in _ARCHIVE_SKIP_DIRS for part in path.parts):
                continue
            if path.suffix.lower() in _ARCHIVE_SKIP_SUFFIXES:
                continue
            arcname = path.relative_to(job_dir.parent)
            zf.write(path, arcname=str(arcname))
            total += path.stat().st_size
    return total


async def _send(
    api_id: int,
    api_hash: str,
    session_path: str,
    target_chat: int | str,
    job_dir: Path,
    package: dict,
    send_archive: bool,
    archive_max_mb: int,
    *,
    niche: str | None = None,
    topic_id: int | None = None,
    topics_path: Path | None = None,
    forum_topics_enabled: bool = True,
    force_resend: bool = False,
) -> None:
    from telethon import TelegramClient
    from telethon.tl.types import DocumentAttributeFilename

    marker = job_dir / _SENT_MARKER
    if marker.is_file() and not force_resend:
        log.info("tg_send_skipped_already_sent: %s", job_dir.name)
        return

    final_mp4 = job_dir / "final.mp4"
    final_long_mp4 = job_dir / "final_long.mp4"
    figure = _read_figure(job_dir)
    has_long = final_long_mp4.is_file() and final_long_mp4.stat().st_size > 0

    reply_to = None
    if forum_topics_enabled and topics_path is not None:
        default_topic_id, topic_map = load_topic_map(topics_path)
        resolved_topic = resolve_topic_id(
            niche,
            default_topic_id=default_topic_id,
            topic_map=topic_map,
        )
        topic_id = resolved_topic if topic_id is None else topic_id
        reply_to = _forum_reply_to(topic_id)
        log.info(
            "tg_topic_routed",
            niche=niche or "",
            topic_id=topic_id or 0,
            has_map=bool(topic_map),
        )

    shorts_name = _upload_filename(figure, "SHORTS")
    long_name = _upload_filename(figure, "LONG")

    async with TelegramClient(session_path, api_id, api_hash) as client:

        # ── BLOCK A: SHORTS (text → video) ───────────────────────────────────
        log.info("tg_send_shorts_text")
        await client.send_message(
            target_chat,
            _shorts_metadata_message(figure, package, niche=niche),
            reply_to=reply_to,
        )
        await asyncio.sleep(_BLOCK_PAUSE_S)

        log.info("tg_send_shorts_start")
        await client.send_file(
            target_chat,
            final_mp4,
            caption=_video_caption(
                slot="SHORTS · YouTube upload",
                label="~60 s  ·  download & upload as-is",
                filename=shorts_name,
            ),
            force_document=True,
            attributes=[DocumentAttributeFilename(file_name=shorts_name)],
            progress_callback=_progress(f"{job_dir.name[:8]}  shorts"),
            reply_to=reply_to,
        )
        log.info("tg_send_shorts_done")

        # Mark as sent immediately after the primary upload (shorts video).
        # Long/operator blocks below are best-effort; if they fail we don't
        # want to re-send the shorts video on retry.
        try:
            marker.write_text("ok\n", encoding="utf-8")
        except OSError as exc:
            log.warning("tg_marker_write_failed: %s — %s", marker, exc)

        # ── BLOCK B: LONG (text → video) ─────────────────────────────────────
        if has_long:
            await asyncio.sleep(_BLOCK_PAUSE_S)
            log.info("tg_send_long_text")
            await client.send_message(
                target_chat,
                _long_header_message(figure, niche=niche),
                reply_to=reply_to,
            )
            await asyncio.sleep(_BLOCK_PAUSE_S)

            log.info("tg_send_long_start")
            await client.send_file(
                target_chat,
                final_long_mp4,
                caption=_video_caption(
                    slot="LONG · optional upload",
                    label="~66 s  ·  0.9× speed",
                    filename=long_name,
                ),
                force_document=True,
                attributes=[DocumentAttributeFilename(file_name=long_name)],
                progress_callback=_progress(f"{job_dir.name[:8]}  long"),
                reply_to=reply_to,
            )
            log.info("tg_send_long_done")
        else:
            log.info("tg_send_long_skipped")

        # ── BLOCK C: OPERATOR (text → zip) ───────────────────────────────────
        if not send_archive:
            log.info("tg_send_archive_disabled")
            return

        await asyncio.sleep(_BLOCK_PAUSE_S)
        log.info("tg_send_operator_text")
        await client.send_message(
            target_chat,
            _operator_header_message(figure, job_dir.name),
            reply_to=reply_to,
        )
        await asyncio.sleep(_BLOCK_PAUSE_S)

        with tempfile.TemporaryDirectory(prefix="tg_archive_") as tmp:
            zip_path = Path(tmp) / f"{job_dir.name}.zip"
            log.info("tg_archive_building", job=job_dir.name)
            total_in = _build_archive(job_dir, zip_path)
            zip_mb = zip_path.stat().st_size / (1024 * 1024)
            log.info(
                "tg_archive_built",
                input_mb=round(total_in / (1024 * 1024), 1),
                zip_mb=round(zip_mb, 1),
            )

            if archive_max_mb > 0 and zip_mb > archive_max_mb:
                log.warning(
                    "tg_archive_too_large",
                    zip_mb=round(zip_mb, 1),
                    cap_mb=archive_max_mb,
                )
                return

            await client.send_file(
                target_chat,
                str(zip_path),
                caption=_archive_caption(figure=figure, job_name=job_dir.name),
                force_document=True,
                attributes=[DocumentAttributeFilename(file_name=f"{job_dir.name}_OPERATOR.zip")],
                progress_callback=_progress(f"{job_dir.name[:8]}  backup"),
                reply_to=reply_to,
            )
            log.info("tg_send_archive_done")


def send_job(
    *,
    api_id: int,
    api_hash: str,
    session_path: str,
    target_chat: str,
    job_dir: Path,
    package_json: Path,
    send_archive: bool = True,
    archive_max_mb: int = 1800,
    niche: str | None = None,
    topics_path: Path | None = None,
    forum_topics_enabled: bool = True,
    max_retries: int = 3,
    force_resend: bool = False,
) -> None:
    """Synchronous entry point — safe to call from the background upload worker.

    Retries transient failures with exponential backoff. ``FloodWaitError``
    pauses for the exact server-requested duration regardless of retry count.
    """
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

    # Lazy import so the module loads without telethon installed.
    try:
        from telethon.errors import FloodWaitError  # type: ignore
    except ImportError:
        FloodWaitError = ()  # type: ignore[assignment]

    attempts = max(1, max_retries + 1)
    backoff = 5.0
    for attempt in range(1, attempts + 1):
        try:
            asyncio.run(
                _send(
                    api_id=api_id,
                    api_hash=api_hash,
                    session_path=session_path,
                    target_chat=chat,
                    job_dir=job_dir,
                    package=package,
                    send_archive=send_archive,
                    archive_max_mb=archive_max_mb,
                    niche=niche,
                    topics_path=topics_path,
                    forum_topics_enabled=forum_topics_enabled,
                    force_resend=force_resend,
                )
            )
            return
        except FloodWaitError as exc:  # type: ignore[misc]
            wait_s = int(getattr(exc, "seconds", 30)) + 1
            log.warning("tg_flood_wait: sleeping %ss (job=%s)", wait_s, job_dir.name)
            import time as _time
            _time.sleep(wait_s)
            # FloodWait doesn't count against retry budget.
            continue
        except Exception as exc:
            if attempt >= attempts:
                log.error(
                    "tg_notify_failed_giving_up: attempt %d/%d — %s: %s",
                    attempt, attempts, type(exc).__name__, exc,
                    exc_info=True,
                )
                return
            log.warning(
                "tg_notify_retry: attempt %d/%d failed (%s: %s) — sleeping %.1fs",
                attempt, attempts, type(exc).__name__, exc, backoff,
            )
            import time as _time
            _time.sleep(backoff)
            backoff = min(backoff * 2, 120.0)


def _upload_worker() -> None:
    while True:
        item = _send_queue.get()
        try:
            if item is None:
                return
            send_job(**item)
        except Exception as exc:
            job = item.get("job_dir") if item else None
            log.error(
                "tg_worker_failed: %s — %s",
                type(exc).__name__,
                exc,
                exc_info=True,
                extra={"job": str(job) if job else ""},
            )
        finally:
            _send_queue.task_done()


def _ensure_upload_worker() -> None:
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        thread = threading.Thread(
            target=_upload_worker,
            name="tg-upload-worker",
            daemon=True,
        )
        thread.start()
        _worker_started = True


def enqueue_send_job(
    *,
    api_id: int,
    api_hash: str,
    session_path: str,
    target_chat: str,
    job_dir: Path,
    package_json: Path,
    send_archive: bool = True,
    archive_max_mb: int = 1800,
    niche: str | None = None,
    topics_path: Path | None = None,
    forum_topics_enabled: bool = True,
    max_retries: int = 3,
    force_resend: bool = False,
) -> None:
    """Queue a Telegram upload — returns immediately; worker uploads in background.

    The queue is bounded; if it is full, this call blocks briefly (up to 30s)
    then logs a warning and drops the request rather than blocking the pipeline.
    """
    if not api_id or not api_hash or not target_chat:
        log.debug("tg_notify_disabled: credentials not set in .env")
        return

    _ensure_upload_worker()
    payload = {
        "api_id": api_id,
        "api_hash": api_hash,
        "session_path": session_path,
        "target_chat": target_chat,
        "job_dir": job_dir,
        "package_json": package_json,
        "send_archive": send_archive,
        "archive_max_mb": archive_max_mb,
        "niche": niche,
        "topics_path": topics_path,
        "forum_topics_enabled": forum_topics_enabled,
        "max_retries": max_retries,
        "force_resend": force_resend,
    }
    try:
        _send_queue.put(payload, timeout=30.0)
    except queue.Full:
        log.error(
            "tg_send_queue_full: dropping job %s (queue=%d). Telegram likely down.",
            job_dir.name, _send_queue.qsize(),
        )
        return
    log.info(
        "tg_send_queued: job=%s niche=%s queue=%d",
        job_dir.name, niche or "", _send_queue.qsize(),
    )
