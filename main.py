import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from services.database import init_db, get_report, get_pending_tasks_for_user
from services.scheduler import start_background_scheduler
from handlers.telegram_handler import handle_telegram_update
from handlers.line_handler import handle_line_events

@asynccontextmanager
async def lifespan(app: FastAPI):
    # เริ่มต้นฐานข้อมูลและลูปติดตามงานอัตโนมัติ
    print("[INIT] Initializing Assistant Database...")
    init_db()
    print("[INIT] Starting Background Scheduler & Chaser Loop...")
    start_background_scheduler()
    yield
    print("[SHUTDOWN] Assistant Bot stopped.")

app = FastAPI(title="YES BOSS AI Assistant & Legal Screener", lifespan=lifespan)
templates = Jinja2Templates(directory="templates")

@app.get("/")
def root():
    return {
        "status": "online",
        "service": "YES BOSS AI Assistant & Legal Screener",
        "version": "1.0.0",
        "features": [
            "Executive Secretary Persona",
            "Persistent Task Chaser (Follow-up until done)",
            "Legal & Regulatory Document Screening",
            "Executive 1-Page Summary & Webview Dashboard",
            "Permanent Memory (Notes, Accounts, Files)",
            "Morning Briefing (07:30 AM)"
        ]
    }

@app.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    base_url = str(request.base_url).rstrip("/")
    update = await request.json()
    await handle_telegram_update(update, base_url)
    return {"ok": True}

@app.post("/webhook/line")
async def line_webhook(request: Request):
    base_url = str(request.base_url).rstrip("/")
    body = await request.json()
    events = body.get("events", [])
    await handle_line_events(events, base_url)
    return {"status": "ok"}

@app.get("/report/{report_id}", response_class=HTMLResponse)
async def view_report(request: Request, report_id: str):
    report = get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="ไม่พบบันทึกกลั่นกรองเอกสารนี้")
    return templates.TemplateResponse("report.html", {"request": request, "report": report})

@app.get("/api/tasks/{user_id}")
async def get_tasks(user_id: str):
    tasks = get_pending_tasks_for_user(user_id)
    return {"tasks": tasks}

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
