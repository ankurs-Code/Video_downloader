from pathlib import Path
import re
import shutil
import tempfile

import yt_dlp


def _format_sort_key(item):
    return -item["quality"]


def _format_score(fmt):
    return (
        fmt.get("tbr") or 0,
        fmt.get("filesize") or fmt.get("filesize_approx") or 0,
        fmt.get("fps") or 0,
    )


def _is_mp4_video_format(fmt):
    return (
        fmt.get("ext") == "mp4"
        and fmt.get("vcodec") not in (None, "none")
        and bool(fmt.get("height"))
        and bool(fmt.get("url"))
    )


def _safe_filename(value):
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "", value).strip().rstrip(".")
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


def get_video_info(url):
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    video_data = {
        "title": info.get("title") or "Untitled video",
        "thumbnail": info.get("thumbnail"),
        "formats": [],
    }

    formats_by_height = {}
    for fmt in info.get("formats", []):
        if not _is_mp4_video_format(fmt):
            continue

        height = int(fmt["height"])
        current_best = formats_by_height.get(height)
        if current_best is None or _format_score(fmt) > _format_score(current_best):
            formats_by_height[height] = fmt

    for height in formats_by_height:
        video_data["formats"].append(
            {
                "label": f"{height}p",
                "ext": "mp4",
                "quality": height,
            }
        )

    video_data["formats"].sort(key=_format_sort_key)

    if not video_data["formats"]:
        raise ValueError("No MP4 video formats were found for this URL.")

    return video_data


def download_video(url, height, progress_callback=None):
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg is not installed on this system.")

    temp_dir = Path(tempfile.mkdtemp(prefix="vclip-", dir="/tmp"))
    output_template = temp_dir / "video.%(ext)s"
    format_selector = (
        f"bestvideo[ext=mp4][height={height}]+bestaudio[ext=m4a]/"
        f"bestvideo[ext=mp4][height={height}]+bestaudio/"
        f"best[ext=mp4][height={height}]/"
        f"bestvideo[height={height}]+bestaudio[ext=m4a]/"
        f"bestvideo[height={height}]+bestaudio/"
        f"best[height={height}]"
    )
    completed_parts = {"count": 0}

    def progress_hook(data):
        status = data.get("status")
        if status == "downloading":
            total_bytes = data.get("total_bytes") or data.get("total_bytes_estimate")
            downloaded_bytes = data.get("downloaded_bytes") or 0
            ratio = (downloaded_bytes / total_bytes) if total_bytes else 0

            if completed_parts["count"] == 0:
                progress = 8 + (ratio * 52)
                message = f"Downloading {height}p video..."
            else:
                progress = 62 + (ratio * 28)
                message = "Downloading audio..."

            _notify_progress(progress_callback, progress=progress, message=message)
        elif status == "finished":
            completed_parts["count"] += 1
            if completed_parts["count"] == 1:
                _notify_progress(
                    progress_callback,
                    progress=62,
                    message="Video stream ready. Preparing remaining data...",
                )
            else:
                _notify_progress(
                    progress_callback,
                    progress=92,
                    message="Merging video and audio...",
                )

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "format": format_selector,
        "merge_output_format": "mp4",
        "outtmpl": str(output_template),
        "restrictfilenames": True,
        "nopart": True,
        "progress_hooks": [progress_hook],
    }

    try:
        _notify_progress(
            progress_callback,
            progress=3,
            message=f"Starting {height}p download...",
        )
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise

    _notify_progress(
        progress_callback,
        progress=97,
        message="Finalizing MP4 file...",
        state="processing",
    )

    download_path = temp_dir / "video.mp4"
    if not download_path.exists():
        mp4_candidates = sorted(temp_dir.glob("*.mp4"))
        if mp4_candidates:
            download_path = mp4_candidates[0]
        else:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise RuntimeError("Download finished, but no MP4 file was created.")

    title = info.get("title") or "video"
    filename = f"{_safe_filename(title)}-{height}p.mp4"
    _notify_progress(
        progress_callback,
        progress=100,
        message="Download is ready.",
        state="completed",
    )
    return download_path, filename, temp_dir


def cleanup_download(temp_dir):
    shutil.rmtree(temp_dir, ignore_errors=True)
