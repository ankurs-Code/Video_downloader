import os
from pathlib import Path
import re
import shutil
import tempfile
from contextlib import contextmanager

import yt_dlp


DEFAULT_COOKIEFILE_PATHS = (
    "/etc/secrets/youtube-cookies.txt",
    "/etc/secrets/youtube_cookies.txt",
    "/etc/secrets/cookies.txt",
)


def _safe_filename(value):
    # Strip only characters that are unsafe for most filesystems.
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "", value).strip().rstrip(".")
    return cleaned or "video"


def _notify_progress(progress_callback, *, progress, message, state="downloading"):
    if progress_callback is None:
        return

    safe_progress = max(0, min(100, int(progress)))
    progress_callback(
        {
            "progress": safe_progress,
            "message": message,
            "state": state,
        }
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
    normalized_message = message.lower().replace("’", "'")

    if "sign in to confirm you're not a bot" not in normalized_message:
        return message

    cookiefile = _resolve_cookiefile()
    if cookiefile is not None:
        return (
            "YouTube is blocking requests from this server even with the configured "
            "cookies. Refresh the exported YouTube cookies, update your Render secret "
            "file, and redeploy."
        )

    return (
        "YouTube is blocking requests from this Render server. Add a Netscape-format "
        "YouTube cookies file as a Render secret file and expose it at "
        "`/etc/secrets/youtube-cookies.txt`, or set `YTDLP_COOKIES_FILE` to the secret "
        "file path, then redeploy."
    )


def get_video_info(url):
    try:
        with _with_writable_cookiefile() as cookiefile:
            ydl_opts = _build_ydl_opts()
            if cookiefile is not None:
                ydl_opts["cookiefile"] = cookiefile
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
    except Exception as exc:
        raise RuntimeError(_humanize_ydlp_error(exc)) from exc

    video_data = {
        "title": info.get("title") or "Untitled video",
        "thumbnail": info.get("thumbnail"),
        "formats": [],
    }

    seen_format_ids = set()
    for fmt in info.get("formats", []):
        format_id = fmt.get("format_id")
        if not format_id or not fmt.get("url") or format_id in seen_format_ids:
            continue

        seen_format_ids.add(format_id)

        has_video = bool(fmt.get("vcodec") and fmt["vcodec"] != "none")
        has_audio = bool(fmt.get("acodec") and fmt["acodec"] != "none")

        label = fmt.get("format") or f"Format {format_id}"
        if has_video and not has_audio:
            label += " (video only)"
        elif has_audio and not has_video:
            label += " (audio only)"

        video_data["formats"].append(
            {
                "id": format_id,
                "label": label,
                "ext": fmt.get("ext") or "unknown",
                "has_video": has_video,
                "has_audio": has_audio,
            }
        )

    if not video_data["formats"]:
        raise ValueError("No downloadable formats were found for this URL.")

    return video_data


def download_video(url, format_id, progress_callback=None):
    temp_dir = Path(tempfile.mkdtemp(prefix="vclip-", dir="/tmp"))
    output_template = temp_dir / "download.%(ext)s"

    def progress_hook(data):
        status = data.get("status")
        if status == "downloading":
            total_bytes = data.get("total_bytes") or data.get("total_bytes_estimate")
            downloaded_bytes = data.get("downloaded_bytes") or 0
            ratio = (downloaded_bytes / total_bytes) if total_bytes else 0
            progress = 8 + (ratio * 84)
            message = "Downloading selected format..."
            _notify_progress(progress_callback, progress=progress, message=message)
        elif status == "finished":
            _notify_progress(
                progress_callback,
                progress=94,
                message="Finalizing file...",
                state="processing",
            )

    try:
        _notify_progress(
            progress_callback,
            progress=3,
            message="Starting download...",
        )
        with _with_writable_cookiefile() as cookiefile:
            ydl_opts = _build_ydl_opts(
                format=format_id,
                outtmpl=str(output_template),
                restrictfilenames=True,
                nopart=True,
                progress_hooks=[progress_hook],
            )
            if cookiefile is not None:
                ydl_opts["cookiefile"] = cookiefile
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
    except Exception as exc:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise RuntimeError(_humanize_ydlp_error(exc)) from exc

    download_path = None
    for requested in info.get("requested_downloads", []) or []:
        candidate = requested.get("filepath") or requested.get("_filename")
        if candidate and Path(candidate).exists():
            download_path = Path(candidate)
            break

    if download_path is None:
        downloaded_files = sorted(path for path in temp_dir.iterdir() if path.is_file())
        if downloaded_files:
            download_path = downloaded_files[0]
        else:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise RuntimeError("Download finished, but no file was created.")

    title = info.get("title") or "video"
    extension = download_path.suffix or f".{info.get('ext') or 'bin'}"
    filename = f"{_safe_filename(title)}-{_safe_filename(str(format_id))}{extension}"
    return download_path, filename, temp_dir


def cleanup_download(temp_dir):
    shutil.rmtree(temp_dir, ignore_errors=True)
