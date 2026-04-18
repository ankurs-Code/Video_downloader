import os
from pathlib import Path
import re
import tempfile
import shutil
from contextlib import contextmanager

import yt_dlp


DEFAULT_COOKIEFILE_PATHS = (
    "/etc/secrets/youtube-cookies.txt",
    "/etc/secrets/youtube_cookies.txt",
    "/etc/secrets/cookies.txt",
)


def _resolve_cookiefile():
    configured_path = os.getenv("YTDLP_COOKIES_FILE", "").strip()
    if configured_path:
        cookiefile = Path(configured_path).expanduser()
        if cookiefile.is_file():
            return str(cookiefile)
        raise RuntimeError(f"Configured YTDLP_COOKIES_FILE was not found: {cookiefile}")

    for candidate in DEFAULT_COOKIEFILE_PATHS:
        cookiefile = Path(candidate)
        if cookiefile.is_file():
            return str(cookiefile)

    return None


def _build_ydl_opts(**overrides):
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }

    cookiefile = _resolve_cookiefile()
    if cookiefile is not None:
        ydl_opts["cookiefile"] = cookiefile

    ydl_opts.update(overrides)
    return ydl_opts


@contextmanager
def _with_writable_cookiefile():
    cookiefile = _resolve_cookiefile()
    if cookiefile is None:
        yield None
        return

    temp_cookie = tempfile.NamedTemporaryFile(
        prefix="vclip-cookies-",
        suffix=".txt",
        dir="/tmp",
        delete=False,
    )
    temp_cookie.close()
    temp_cookie_path = Path(temp_cookie.name)

    try:
        shutil.copyfile(cookiefile, temp_cookie_path)
        yield str(temp_cookie_path)
    finally:
        temp_cookie_path.unlink(missing_ok=True)


def _humanize_ydlp_error(exc):
    message = str(exc)
    normalized = message.lower().replace("\u2019", "'").replace("\u2018", "'")

    if "sign in to confirm you're not a bot" not in normalized:
        return message

    cookiefile = _resolve_cookiefile()
    if cookiefile is not None:
        return (
            "YouTube is blocking requests even with the current cookies. "
            "Your cookies have likely expired. Steps to fix: "
            "1) Open YouTube in your browser and make sure you're logged in. "
            "2) Use the 'Get cookies.txt LOCALLY' extension to re-export cookies. "
            "3) Update the secret file on Render and redeploy."
        )

    return (
        "YouTube is blocking requests from this server (bot detection). "
        "To fix this: "
        "1) Install the 'Get cookies.txt LOCALLY' browser extension. "
        "2) Go to youtube.com while logged in and export cookies as a .txt file. "
        "3) On Render, add a Secret File at /etc/secrets/youtube-cookies.txt "
        "with the exported cookie contents. "
        "4) Redeploy."
    )


def _format_quality(fmt):
    """Build a human-readable quality label like '720p' or '128kbps'."""
    height = fmt.get("height")
    if height:
        return f"{height}p"

    # Audio-only: show bitrate
    abr = fmt.get("abr")
    if abr:
        return f"{int(abr)}kbps"

    return fmt.get("format_note") or fmt.get("resolution") or "Unknown"


def get_video_info(url):
    """
    Extract video info and return ALL available formats with direct URLs.
    Each format is labelled as 'Video + Audio', 'Video only', or 'Audio only'.
    """
    try:
        with _with_writable_cookiefile() as cookiefile:
            ydl_opts = _build_ydl_opts()
            if cookiefile is not None:
                ydl_opts["cookiefile"] = cookiefile
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
    except Exception as exc:
        raise RuntimeError(_humanize_ydlp_error(exc)) from exc

    formats = []
    seen = set()

    for fmt in info.get("formats", []):
        stream_url = fmt.get("url")
        format_id = fmt.get("format_id")
        if not stream_url or not format_id or format_id in seen:
            continue
        seen.add(format_id)

        has_video = bool(fmt.get("vcodec") and fmt["vcodec"] != "none")
        has_audio = bool(fmt.get("acodec") and fmt["acodec"] != "none")

        if has_video and has_audio:
            kind = "video+audio"
            badge = "Video + Audio"
        elif has_video:
            kind = "video"
            badge = "Video only"
        elif has_audio:
            kind = "audio"
            badge = "Audio only"
        else:
            continue  # skip unknown streams

        quality = _format_quality(fmt)
        ext = fmt.get("ext") or "unknown"

        formats.append({
            "quality": quality,
            "ext": ext,
            "kind": kind,
            "badge": badge,
            "url": stream_url,
        })

    if not formats:
        raise ValueError("No downloadable formats were found for this URL.")

    return {
        "title": info.get("title", "Untitled video"),
        "thumbnail": info.get("thumbnail"),
        "formats": formats,
    }
