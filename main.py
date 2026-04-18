from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from downloader import get_video_info


app = FastAPI(title="vclip")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


def _render(request, *, url="", video=None, error=None):
    return templates.TemplateResponse(
        request,
        "index.html",
        {"url": url, "video": video, "error": error},
    )


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return _render(request)


@app.post("/fetch", response_class=HTMLResponse)
def fetch_video(request: Request, url: str = Form(...)):
    cleaned = url.strip()
    if not cleaned:
        return _render(request, error="Please enter a video URL.")

    try:
        video = get_video_info(cleaned)
    except Exception as exc:
        return _render(request, url=cleaned, error=str(exc))

    return _render(request, url=cleaned, video=video)
