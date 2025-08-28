import asyncio
import os
import time
from typing import List, Dict, Any
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import yaml

CONFIG_PATH = os.getenv("SERVICES_CONFIG", os.path.join(os.path.dirname(__file__), "..", "config", "services.yaml"))
CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL_SECONDS", "60"))
GLOBAL_TIMEOUT_SECONDS = float(os.getenv("GLOBAL_TIMEOUT_SECONDS", "8.0"))
CONCURRENCY_LIMIT = int(os.getenv("CONCURRENCY_LIMIT", "10"))

app = FastAPI(title="FastAPI Uptime Dashboard", version="1.1.0")
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))
app.mount('/static', StaticFiles(directory=os.path.join(os.path.dirname(__file__), 'static')), name='static')

_status: Dict[str, Dict[str, Any]] = {}
_services: List[Dict[str, Any]] = []
_stop_event = asyncio.Event()

def load_services() -> List[Dict[str, Any]]:
    if not os.path.exists(CONFIG_PATH):
        raise FileNotFoundError(f"Config not found: {CONFIG_PATH}")
    with open(CONFIG_PATH, "r") as f:
        data = yaml.safe_load(f) or {}
    services = data.get("services", [])
    for s in services:
        s.setdefault("method", "GET")
        s.setdefault("timeout", GLOBAL_TIMEOUT_SECONDS)
        s.setdefault("expect_status", 200)
        s.setdefault("headers", {})
        s.setdefault("body", None)
        s.setdefault("wake_url", s.get("url"))
        s.setdefault("wake_method", "GET")
        s.setdefault("wake_body", None)
        s.setdefault("wake_headers", {})
    return services

async def check_one(client: httpx.AsyncClient, svc: Dict[str, Any]) -> Dict[str, Any]:
    url = svc["url"]
    method = svc["method"].upper()
    started = time.time()
    ok = False
    err = None
    status_code = None
    try:
        resp = await client.request(
            method, url,
            timeout=svc.get("timeout", GLOBAL_TIMEOUT_SECONDS),
            headers=svc.get("headers") or {},
            json=svc.get("body") if svc.get("body") is not None else None,
        )
        status_code = resp.status_code
        ok = (status_code == svc.get("expect_status", 200))
    except Exception as e:
        err = str(e)
    latency_ms = int((time.time() - started) * 1000)
    return {
        "name": svc.get("name") or url,
        "url": url,
        "ok": ok,
        "status_code": status_code,
        "latency_ms": latency_ms,
        "checked_at": int(time.time()),
        "error": err,
        "region": svc.get("region"),
        "tags": svc.get("tags", []),
        "expect_status": svc.get("expect_status", 200)
    }

async def check_all_once() -> Dict[str, Dict[str, Any]]:
    results: Dict[str, Dict[str, Any]] = {}
    sem = asyncio.Semaphore(CONCURRENCY_LIMIT)
    async with httpx.AsyncClient(follow_redirects=True) as client:
        async def _wrapped(svc):
            async with sem:
                return await check_one(client, svc)
        tasks = [asyncio.create_task(_wrapped(s)) for s in _services]
        for t in asyncio.as_completed(tasks):
            r = await t
            results[r["url"]] = r
    return results

async def background_loop():
    while not _stop_event.is_set():
        try:
            results = await check_all_once()
            _status.update(results)
        except Exception as e:
            _status["__error__"] = {"ok": False, "error": f"Background loop error: {e}", "checked_at": int(time.time())}
        try:
            await asyncio.wait_for(_stop_event.wait(), timeout=CHECK_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            pass

@app.on_event("startup")
async def on_startup():
    global _services
    _services = load_services()
    try:
        initial = await check_all_once()
        _status.update(initial)
    except Exception as e:
        _status["__error__"] = {"ok": False, "error": f"Initial check error: {e}", "checked_at": int(time.time())}
    app.state.bg_task = asyncio.create_task(background_loop())

@app.on_event("shutdown")
async def on_shutdown():
    _stop_event.set()
    task = getattr(app.state, "bg_task", None)
    if task:
        task.cancel()
        try:
            await task
        except Exception:
            pass

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "title": "Uptime Dashboard"})

@app.get("/api/status")
async def api_status():
    data = list(_status.values())
    data = [d for d in data if d.get("url")]
    data.sort(key=lambda d: d.get("name") or d.get("url"))
    return {"services": data, "last_updated": int(time.time())}

class WakeRequest(BaseModel):
    url: str

@app.post("/api/wake")
async def api_wake(body: WakeRequest):
    svc = next((s for s in _services if s.get("url") == body.url), None)
    if not svc:
        raise HTTPException(status_code=404, detail="Service not found for given URL")
    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.request(
                svc.get("wake_method","GET"),
                svc.get("wake_url", svc["url"]),
                headers=svc.get("wake_headers") or {},
                json=svc.get("wake_body") if svc.get("wake_body") is not None else None,
                timeout=svc.get("timeout", GLOBAL_TIMEOUT_SECONDS),
            )
            one_status = await check_one(client, svc)
            _status[one_status["url"]] = one_status
            return {"ok": True, "wake_status": resp.status_code, "service_ok": one_status["ok"], "service_status": one_status["status_code"]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Wake failed: {e}")

@app.post("/api/refresh")
async def api_refresh():
    try:
        results = await check_all_once()
        _status.update(results)
        return {"ok": True, "refreshed": len(results)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/reload")
async def api_reload():
    global _services
    _services = load_services()
    results = await check_all_once()
    _status.update(results)
    return {"ok": True, "services": len(_services)}
