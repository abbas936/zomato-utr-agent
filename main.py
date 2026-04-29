"""
Zomato UTR Agent — FastAPI Backend
===================================
Run locally or on any server (Railway, Render, EC2).

Install:
    pip install fastapi uvicorn playwright nest_asyncio pyngrok

Run:
    uvicorn main:app --reload --port 8000

Endpoints:
    POST /api/start-session      → starts browser + noVNC + Cloudflare tunnel
    GET  /api/session-status     → check if logged in
    POST /api/confirm-login      → save session after customer logs in
    POST /api/start-download     → kick off UTR download loop
    GET  /api/download-status    → poll download progress
    GET  /api/data               → return downloaded UTR records
    POST /api/stop               → close browser
"""

import asyncio
import json
import os
import subprocess
import threading
import time
import calendar as cal
import re
from datetime import datetime
from pathlib import Path
from contextlib import asynccontextmanager

import nest_asyncio
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from playwright.async_api import async_playwright

nest_asyncio.apply()

# ── Config ──────────────────────────────────────────────────
SESSION_FILE  = "./zomato_session.json"
DOWNLOAD_DIR  = "./zomato_downloads"
PARTNER_URL   = "https://www.zomato.com/partners/login"
UTR_URL       = "https://www.zomato.com/partners/onlineordering/finance/utr"
MONTH_NAMES   = ["January","February","March","April","May","June",
                 "July","August","September","October","November","December"]

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ── Global state ─────────────────────────────────────────────
state = {
    "playwright": None,
    "browser": None,
    "context": None,
    "page": None,
    "tunnel_url": None,
    "tunnel_proc": None,
    "vnc_procs": [],
    "logged_in": False,
    "downloading": False,
    "download_progress": {"current": 0, "total": 0, "current_month": "", "done": False},
    "logs": [],
    "data_file": None,
}


def log(msg, level="info"):
    entry = {"ts": datetime.now().strftime("%H:%M:%S"), "msg": msg, "level": level}
    state["logs"].append(entry)
    print(f"[{entry['ts']}] {msg}")


# ── FastAPI app ───────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start tunnel immediately so frontend can auto-detect it
    start_cloudflare_tunnel()
    log("Starting Cloudflare tunnel...")
    try:
        start_novnc()
        log("noVNC started on port 6080")
    except Exception as e:
        log(f"noVNC start skipped: {e}", "error")
    yield
    try:
        await stop_browser()
    except:
        pass

app = FastAPI(title="Zomato UTR Agent", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Browser helpers ───────────────────────────────────────────
async def start_browser(headless=False):
    state["playwright"] = await async_playwright().start()
    state["browser"] = await state["playwright"].chromium.launch(
        headless=headless,
        downloads_path=DOWNLOAD_DIR,
        args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"]
    )
    if os.path.exists(SESSION_FILE):
        log("Loading saved session...")
        with open(SESSION_FILE) as f:
            storage = json.load(f)
        state["context"] = await state["browser"].new_context(
            storage_state=storage,
            accept_downloads=True
        )
        state["logged_in"] = True
    else:
        state["context"] = await state["browser"].new_context(
            accept_downloads=True,
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    state["page"] = await state["context"].new_page()
    log("Browser started", "success")


async def save_session():
    storage = await state["context"].storage_state()
    with open(SESSION_FILE, "w") as f:
        json.dump(storage, f)
    log("Session saved", "success")


async def stop_browser():
    try:
        if state["browser"]:
            await state["browser"].close()
        if state["playwright"]:
            await state["playwright"].stop()
    except:
        pass
    for proc in state["vnc_procs"]:
        try:
            proc.terminate()
        except:
            pass
    if state["tunnel_proc"]:
        try:
            state["tunnel_proc"].terminate()
        except:
            pass
    log("Browser closed")


def start_novnc():
    """Start Xvfb + fluxbox + x11vnc + websockify for noVNC."""
    procs = []
    os.environ["DISPLAY"] = ":99"

    p1 = subprocess.Popen(["Xvfb", ":99", "-screen", "0", "1280x800x24"])
    procs.append(p1)
    time.sleep(2)

    p2 = subprocess.Popen(["fluxbox"], env=os.environ)
    procs.append(p2)
    time.sleep(1)

    p3 = subprocess.Popen(["x11vnc", "-display", ":99", "-nopw", "-listen", "localhost", "-xkb", "-forever"])
    procs.append(p3)
    time.sleep(1)

    p4 = subprocess.Popen(["websockify", "--web=/usr/share/novnc/", "6080", "localhost:5900"])
    procs.append(p4)
    time.sleep(1)

    state["vnc_procs"] = procs
    log("noVNC started on port 6080")


def start_cloudflare_tunnel():
    """Start Cloudflare tunnel and extract the public URL."""
    def run():
        proc = subprocess.Popen(
            ["cloudflared", "tunnel", "--url", "http://localhost:8000",
             "--metrics", "localhost:2999"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT
        )
        state["tunnel_proc"] = proc
        for line in proc.stdout:
            line = line.decode()
            if "trycloudflare.com" in line:
                urls = re.findall(r"https://[^\s]+trycloudflare\.com", line)
                if urls:
                    state["tunnel_url"] = urls[0]
                    log(f"Tunnel ready → {urls[0]}", "success")
                    # Register with Railway so frontend can auto-detect
                    try:
                        import urllib.request as urlreq
                        payload = json.dumps({"tunnel_url": urls[0]}).encode()
                        req = urlreq.Request(
                            "https://zomato-utr-agent-production.up.railway.app/api/register-tunnel",
                            data=payload,
                            headers={"Content-Type": "application/json"},
                            method="POST"
                        )
                        urlreq.urlopen(req, timeout=5)
                        log("Tunnel registered with Railway", "success")
                    except Exception as e:
                        log(f"Railway registration skipped: {e}", "error")
                    break

    threading.Thread(target=run, daemon=True).start()


# ── UTR download helpers ──────────────────────────────────────
async def set_date_range(start_str, end_str):
    page = state["page"]
    start = datetime.strptime(start_str, "%Y-%m-%d")
    end   = datetime.strptime(end_str,   "%Y-%m-%d")

    await page.click(".cursor-pointer.gap-2.px-2.rounded-lg.h-9")
    await page.wait_for_timeout(1000)

    selects = await page.query_selector_all("select")
    await selects[1].select_option(label=str(start.year))
    await page.wait_for_timeout(300)
    await selects[0].select_option(label=MONTH_NAMES[start.month - 1])
    await page.wait_for_timeout(500)

    # Click start day (first active)
    day_buttons = await page.query_selector_all("button.rdrDay")
    for btn in day_buttons:
        if await btn.get_attribute("disabled") is not None:
            continue
        cls = await btn.get_attribute("class") or ""
        if "rdrDayPassive" in cls:
            continue
        span = await btn.query_selector(".rdrDayNumber span")
        if span and (await span.inner_text()).strip() == str(start.day):
            await btn.click()
            break
    await page.wait_for_timeout(500)

    # Click end day (first active after shift)
    day_buttons = await page.query_selector_all("button.rdrDay")
    for btn in day_buttons:
        if await btn.get_attribute("disabled") is not None:
            continue
        cls = await btn.get_attribute("class") or ""
        if "rdrDayPassive" in cls:
            continue
        span = await btn.query_selector(".rdrDayNumber span")
        if span and (await span.inner_text()).strip() == str(end.day):
            await btn.click()
            break
    await page.wait_for_timeout(500)

    await page.click("text=Apply")
    await page.wait_for_timeout(2000)


async def download_rows_on_page(range_label):
    page = state["page"]
    await page.evaluate("window.scrollTo(0, 0)")
    await page.wait_for_timeout(500)

    rows = await page.query_selector_all("tbody tr")
    for row in rows:
        await row.scroll_into_view_if_needed()
        await page.wait_for_timeout(100)

    svgs, utr_nums = [], []
    for i, row in enumerate(rows):
        td = await row.query_selector("td:first-child")
        utr = (await td.inner_text()).strip() if td else f"row_{i}"
        svg = await row.query_selector("td:last-child svg")
        if svg:
            svgs.append(svg)
            utr_nums.append(utr)

    async def click_and_save(svg, utr):
        try:
            async with page.expect_download(timeout=60000) as dl:
                await svg.click()
            download = await dl.value
            await download.path()
            ext = download.suggested_filename.split(".")[-1] if "." in download.suggested_filename else "csv"
            save_path = f"{DOWNLOAD_DIR}/{range_label}_{utr}.{ext}"
            await download.save_as(save_path)
            return 1
        except:
            return 0

    results = await asyncio.gather(*[click_and_save(s, u) for s, u in zip(svgs, utr_nums)])
    return sum(results)


async def go_to_next_page():
    page = state["page"]
    try:
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(500)
        btns = await page.query_selector_all(".flex.gap-4.items-center.justify-end button")
        if not btns:
            return False
        next_btn = btns[-1]
        if await next_btn.get_attribute("disabled") is not None:
            return False
        await next_btn.click()
        await page.wait_for_timeout(1500)
        return True
    except:
        return False


def month_ranges(from_year, from_month, to_year, to_month):
    ranges = []
    y, m = from_year, from_month
    while (y, m) <= (to_year, to_month):
        last_day = cal.monthrange(y, m)[1]
        ranges.append((f"{y}-{m:02d}-01", f"{y}-{m:02d}-{last_day:02d}"))
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return ranges


async def run_download_loop(from_year, from_month, to_year, to_month):
    page = state["page"]
    ranges = month_ranges(from_year, from_month, to_year, to_month)
    state["download_progress"] = {"current": 0, "total": len(ranges), "current_month": "", "done": False}

    total = 0
    for i, (start, end) in enumerate(ranges):
        label = f"{start}_to_{end}"
        month_name = f"{MONTH_NAMES[int(start[5:7])-1]} {start[:4]}"
        state["download_progress"]["current_month"] = month_name
        log(f"Downloading {month_name}...")

        await page.goto(UTR_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(2000)
        await set_date_range(start, end)

        page_num = 1
        while True:
            count = await download_rows_on_page(label)
            total += count
            log(f"  {month_name} page {page_num}: {count} UTRs")
            has_next = await go_to_next_page()
            if not has_next:
                break
            page_num += 1

        state["download_progress"]["current"] = i + 1

    state["download_progress"]["done"] = True
    state["downloading"] = False
    log(f"All done — {total} UTR files downloaded", "success")

    # Merge all CSVs
    try:
        import pandas as pd
        import glob
        files = glob.glob(f"{DOWNLOAD_DIR}/**/*", recursive=True)
        files = [f for f in files if os.path.isfile(f) and not f.endswith("merged.csv")]
        if files:
            dfs = [pd.read_csv(f) for f in files]
            merged = pd.concat(dfs, ignore_index=True)
            merged_path = f"{DOWNLOAD_DIR}/utr_merged.csv"
            merged.to_csv(merged_path, index=False)
            state["data_file"] = merged_path
            log(f"Merged {len(files)} files → {len(merged)} rows", "success")
    except Exception as e:
        log(f"Merge failed: {e}", "error")


# ── API Routes ────────────────────────────────────────────────

class DownloadRequest(BaseModel):
    from_year: int = 2025
    from_month: int = 4
    to_year: int = 2026
    to_month: int = 4


@app.post("/api/start-session")
async def start_session():
    """Start browser + noVNC + Cloudflare tunnel."""
    if state["browser"]:
        return {"status": "already_running", "tunnel_url": state["tunnel_url"], "logged_in": state["logged_in"]}

    log("Starting session...")

    # Start noVNC virtual display
    try:
        start_novnc()
    except Exception as e:
        log(f"noVNC start failed (may not be on Linux): {e}", "error")

    # Start browser (headless=False so noVNC can see it)
    await start_browser(headless=False)

    # Start Cloudflare tunnel (only if not already running)
    if not state["tunnel_proc"]:
        start_cloudflare_tunnel()

    # Navigate to login
    await state["page"].goto(PARTNER_URL, wait_until="domcontentloaded", timeout=60000)
    log("Navigated to Zomato partner login")

    # Wait up to 10s for tunnel URL
    for _ in range(20):
        if state["tunnel_url"]:
            break
        await asyncio.sleep(0.5)

    if state["logged_in"]:
        return {"status": "already_logged_in", "tunnel_url": state["tunnel_url"], "logged_in": True}

    return {"status": "browser_ready", "tunnel_url": state["tunnel_url"], "logged_in": False}


@app.get("/api/session-status")
async def session_status():
    """Poll this to check if customer has completed login."""
    if not state["page"]:
        return {"status": "no_browser"}

    current_url = state["page"].url
    logged_in = "/login" not in current_url and "partners" in current_url

    if logged_in and not state["logged_in"]:
        state["logged_in"] = True
        await save_session()

    return {
        "status": "logged_in" if logged_in else "waiting",
        "logged_in": logged_in,
        "tunnel_url": state["tunnel_url"],
        "current_url": current_url,
    }


@app.post("/api/confirm-login")
async def confirm_login():
    """Called when customer clicks 'Done — I've logged in'."""
    if not state["page"]:
        raise HTTPException(status_code=400, detail="No browser running")

    current_url = state["page"].url
    if "/login" not in current_url and "partners" in current_url:
        state["logged_in"] = True
        await save_session()
        return {"status": "confirmed", "logged_in": True}

    return {"status": "not_logged_in", "current_url": current_url}


@app.post("/api/start-download")
async def start_download(req: DownloadRequest):
    """Kick off the UTR download loop."""
    if not state["logged_in"]:
        raise HTTPException(status_code=401, detail="Not logged in")
    if state["downloading"]:
        return {"status": "already_downloading", "progress": state["download_progress"]}

    state["downloading"] = True
    log(f"Starting download: {req.from_year}-{req.from_month:02d} → {req.to_year}-{req.to_month:02d}")

    # Run in background
    asyncio.create_task(run_download_loop(req.from_year, req.from_month, req.to_year, req.to_month))

    return {"status": "started"}


@app.get("/api/download-status")
async def download_status():
    """Poll this during download to get progress."""
    return {
        "downloading": state["downloading"],
        "progress": state["download_progress"],
        "logs": state["logs"][-20:],  # last 20 log entries
    }


@app.get("/api/data")
async def get_data():
    """Return merged CSV as JSON after download completes."""
    if not state["data_file"] or not os.path.exists(state["data_file"]):
        raise HTTPException(status_code=404, detail="No data available — run download first")

    try:
        import pandas as pd
        df = pd.read_csv(state["data_file"])
        return {
            "total": len(df),
            "columns": df.columns.tolist(),
            "data": df.to_dict(orient="records"),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/download-csv")
async def download_csv():
    """Download the merged CSV file."""
    if not state["data_file"] or not os.path.exists(state["data_file"]):
        raise HTTPException(status_code=404, detail="No data file available")
    return FileResponse(state["data_file"], filename="utr_full_year.csv", media_type="text/csv")


@app.get("/api/logs")
async def get_logs():
    return {"logs": state["logs"]}


@app.post("/api/stop")
async def stop():
    """Close browser and clean up."""
    await stop_browser()
    state["browser"] = None
    state["playwright"] = None
    state["context"] = None
    state["page"] = None
    state["tunnel_url"] = None
    state["logged_in"] = False
    state["downloading"] = False
    return {"status": "stopped"}


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "browser_running": state["browser"] is not None,
        "logged_in": state["logged_in"],
        "tunnel_url": state["tunnel_url"],
        "downloading": state["downloading"],
    }


import httpx

@app.get("/novnc/{path:path}")
async def proxy_novnc(path: str):
    async with httpx.AsyncClient() as client:
        try:
            res = await client.get(f"http://localhost:6080/{path}")
            return HTMLResponse(content=res.text, status_code=res.status_code)
        except Exception as e:
            return HTMLResponse(content=f"noVNC not ready: {e}", status_code=503)



@app.get("/api/tunnel-url")
async def get_tunnel_url():
    """Return the current tunnel URL — polled by frontend on load."""
    return {"tunnel_url": state["tunnel_url"]}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
