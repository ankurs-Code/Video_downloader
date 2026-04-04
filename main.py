from pathlib import Path
import threading
import uuid

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask
import uvicorn

from downloader import cleanup_download, download_video, get_video_info


app = FastAPI(title="vclip")
app.mount("/static", StaticFiles(directory="static"), name="static")

templates = Jinja2Templates(directory="templates")
DOWNLOAD_JOBS = {}
DOWNLOAD_LOCK = threading.Lock()


def _set_job_state(job_id, **updates):
    with DOWNLOAD_LOCK:
        job = DOWNLOAD_JOBS.get(job_id)
        if job is not None:
            job.update(updates)


def _get_job(job_id):
    with DOWNLOAD_LOCK:
        job = DOWNLOAD_JOBS.get(job_id)
        return dict(job) if job is not None else None


def _finalize_job(job_id, temp_dir):
    cleanup_download(Path(temp_dir))
    with DOWNLOAD_LOCK:
        DOWNLOAD_JOBS.pop(job_id, None)


def _run_download_job(job_id, url, height):
    _set_job_state(
        job_id,
        state="starting",
        progress=1,
        message=f"Preparing {height}p download...",
    )
    try:
        file_path, filename, temp_dir = download_video(
            url,
            height,
            progress_callback=lambda payload: _set_job_state(job_id, **payload),
        )
    except Exception as exc:
        _set_job_state(
            job_id,
            state="error",
            progress=0,
            message=str(exc),
        )
        return

    _set_job_state(
        job_id,
        state="completed",
        progress=100,
        message="Download is ready.",
        filename=filename,
        file_path=str(file_path),
        temp_dir=str(temp_dir),
        download_url=f"/download-file/{job_id}",
    )


def render_home(request: Request, *, url: str = "", video=None, error: str | None = None):
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "request": request,
            "url": url,
            "video": video,
            "error": error,
        },
    )


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return render_home(request)


@app.post("/fetch", response_class=HTMLResponse)
def fetch_video(request: Request, url: str = Form(...)):
    url = url.strip()
    try:
        video = get_video_info(url)
        return render_home(request, url=url, video=video)
    except Exception as exc:
        return render_home(request, url=url, error=str(exc))


@app.post("/start-download")
def start_download(url: str = Form(...), height: int = Form(...)):
    cleaned_url = url.strip()
    job_id = uuid.uuid4().hex
    with DOWNLOAD_LOCK:
        DOWNLOAD_JOBS[job_id] = {
            "id": job_id,
            "state": "queued",
            "progress": 0,
            "message": "Queued...",
            "height": height,
        }

    worker = threading.Thread(
        target=_run_download_job,
        args=(job_id, cleaned_url, height),
        daemon=True,
    )
    worker.start()
    return {"job_id": job_id}


@app.get("/download-status/{job_id}")
def download_status(job_id: str):
    job = _get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Download job not found.")

    job.pop("file_path", None)
    job.pop("temp_dir", None)
    return job


@app.get("/download-file/{job_id}")
def download_job_file(job_id: str):
    job = _get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Download job not found.")
    if job.get("state") != "completed":
        raise HTTPException(status_code=409, detail="Download is not ready yet.")

    return FileResponse(
        path=job["file_path"],
        filename=job["filename"],
        media_type="video/mp4",
        background=BackgroundTask(_finalize_job, job_id, job["temp_dir"]),
    )


@app.get("/download")
def download_file(url: str, height: int):
    try:
        file_path, filename, temp_dir = download_video(url.strip(), height)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return FileResponse(
        path=file_path,
        filename=filename,
        media_type="video/mp4",
        background=BackgroundTask(cleanup_download, temp_dir),
    )


if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)
