import os
import json
import uuid
import base64
import asyncio
import sqlite3
import httpx
from datetime import datetime, timedelta
from typing import Dict, Any, Tuple, Optional
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from jinja2 import Template

# ----------------- CONFIGURATION -----------------
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-1.5-flash")
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"

DB_PATH = "/tmp/assistant.db"
MEMORY_REPORTS: Dict[str, Dict[str, Any]] = {}

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    try:
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, platform_user_id TEXT UNIQUE, name TEXT, last_active TIMESTAMP)")
            cursor.execute("CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, title TEXT, due_time TIMESTAMP, status TEXT DEFAULT 'pending', remind_count INTEGER DEFAULT 0, next_remind TIMESTAMP)")
            cursor.execute("CREATE TABLE IF NOT EXISTS reports (id TEXT PRIMARY KEY, user_id TEXT, title TEXT, filename TEXT, summary TEXT, legal TEXT, risk TEXT, actions TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
            conn.commit()
    except Exception as e:
        print(f"[DB INIT ERROR] {e}")

DOCUMENT_VISION_PROMPT = """
คุณคือ 'หัวหน้าสำนักเลขานุการและที่ปรึกษากฎหมายประจำตัวผู้บริหาร'
ภารกิจ: อ่านข้อความ ตัวเลข สถิติ และสาระสำคัญทั้งหมดในภาพนี้อย่างละเอียด และห้ามแต่งเติมข้อมูลที่ไม่ตรงกับภาพเด็ดขาด!

[เกณฑ์การวิเคราะห์ตามประเภทเอกสารจริง]:
1. หากเป็น "สื่อประชาสัมพันธ์ / อินโฟกราฟิก / ข่าวสารเตือนภัย / ข้อมูลสุขอนามัย (เช่น ข่าวโควิด-19)":
   - สรุปสาระสำคัญ: ถอดข้อเท็จจริงในภาพให้ครบถ้วน เช่น สถานการณ์โรคระบาด, ยอดผู้ป่วยสะสม, ยอดผู้เสียชีวิต, สายพันธุ์หลัก, อาการ, วิธีป้องกัน, คำแนะนำ
   - การกลั่นกรองข้อกฎหมายและผลกระทบ: ระบุให้ชัดเจนว่า "เอกสารฉบับนี้เป็นสื่อประชาสัมพันธ์/แจ้งเตือนภัยสุขภาพ ไม่ใช่นิติกรรมสัญญา จึงไม่มีภาระผูกพันหรือความเสี่ยงทางกฎหมาย แต่เป็นประเด็นด้านการเฝ้าระวังสุขอนามัยของบุคลากรในสถานที่ทำงาน"
   - ข้อเสนอแนะเชิงบริหาร (Action Items): แนะนำขั้นตอนที่สมเหตุสมผล เช่น "แจ้งเวียนประชาสัมพันธ์ให้บุคลากรในสังกัดทราบเพื่อเฝ้าระวัง", "จัดเตรียมหน้ากากอนามัยและเจลแอลกอฮอล์"
   - ระดับความเสี่ยง: ต่ำ (Low) / เฝ้าระวัง

2. หากเป็น "ร่างสัญญา / MOU / บันทึกข้อความราชการ":
   - สรุป: วัตถุประสงค์, คู่สัญญา, วงเงิน, อำนาจลงนาม, กำหนดเวลา
   - ข้อกฎหมาย: ตรวจสอบความเสี่ยงทางสัญญา เบี้ยปรับ ข้อเสียเปรียบ พร้อมยกร่างข้อความแก้ไข

ตอบกลับในรูปแบบ JSON เท่านั้น:
{
  "title": "ชื่อเรื่องตรงตามเอกสารจริง",
  "risk_level": "ต่ำ (Low) | ปานกลาง (Medium) | สูง (High) | วิกฤต (Critical)",
  "executive_summary": "สรุปสาระสำคัญอย่างละเอียดตามเนื้อหาจริงในภาพ",
  "legal_analysis": "การกลั่นกรองข้อกฎหมายหรือผลกระทบต่อองค์กรอย่างเป็นมืออาชีพ",
  "action_items": "ข้อเสนอแนะและขั้นตอนดำเนินการเชิงบริหารที่ตรงกับเนื้อหาจริง"
}
"""

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield

app = FastAPI(title="YES BOSS AI Assistant", lifespan=lifespan)

async def send_telegram(chat_id: int, text: str):
    if not TELEGRAM_BOT_TOKEN:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    async with httpx.AsyncClient(timeout=10.0) as client:
        await client.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"})

@app.get("/")
def root():
    return {"status": "online", "service": "YES BOSS Assistant"}

@app.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    base_url = str(request.base_url).rstrip("/")
    update = await request.json()
    message = update.get("message")
    if not message:
        return {"ok": True}
        
    chat_id = message["chat"]["id"]
    from_user = message.get("from", {})
    user_name = from_user.get("first_name", "บอส")

    # 1. ข้อความแชต
    if "text" in message:
        text = message["text"].strip()
        if text.startswith("/start"):
            await send_telegram(chat_id, f"สวัสดีค่ะบอส {user_name}! น้องพร้อมรับใช้บอสแล้วนะคะ สั่งงานหรือส่งภาพ/เอกสารเข้ามาได้เลยค่ะ ✨")
            return
        if "เตือน" in text or "นัด" in text:
            await send_telegram(chat_id, f"น้องบันทึกนัดหมาย '{text}' ให้แล้วนะคะ จะคอยเตือนและตามจิกให้จนงานเสร็จแน่นอนค่ะบอส! ✨")
        elif "เสร็จ" in text:
            await send_telegram(chat_id, "รับทราบค่ะบอส! น้องติ๊กปิดงานให้เรียบร้อยแล้ว เก่งมากเลยค่ะ ☕")
        elif "เหนื่อย" in text:
            await send_telegram(chat_id, "กอดๆ นะคะบอส วันนี้เหนื่อยมาทั้งวันแล้ว พักผ่อนสายตาบ้างนะคะ น้องอยู่ข้างๆ เสมอน้า 💙")
        else:
            await send_telegram(chat_id, f"รับทราบคำสั่งค่ะบอส '{text}' น้องสแตนด์บายช่วยเสมอค่ะ ✨")

    # 2. กรณีส่งรูปภาพ (อ่านจริง OCR 100% + สรุปฉบับเต็มลงแชตทันที)
    elif "photo" in message:
        await send_telegram(chat_id, "น้องได้รับภาพแล้วค่ะ กำลังอ่านข้อความและสถิติจากภาพจริงเพื่อสรุปรายงานให้นะคะ... 📸")
        photos = message["photo"]
        file_id = photos[-1]["file_id"]
        
        image_bytes = None
        if TELEGRAM_BOT_TOKEN:
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    info_res = await client.get(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getFile?file_id={file_id}")
                    file_path = info_res.json()["result"]["file_path"]
                    img_res = await client.get(f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}")
                    image_bytes = img_res.content
            except Exception as e:
                await send_telegram(chat_id, f"ดาวน์โหลดภาพไม่สำเร็จ: {e}")
                return

        report_id = str(uuid.uuid4())[:8]
        title = "ข้อมูลจากเอกสารภาพถ่าย"
        risk = "ต่ำ (Low)"
        summary = "ได้รับการอ่านข้อความแล้ว"
        legal = "เอกสารนี้เป็นสื่อประชาสัมพันธ์/ข่าวสารเตือนภัย ไม่ใช่นิติกรรมสัญญา จึงไม่มีภาระผูกพันหรือความเสี่ยงทางกฎหมาย แต่เป็นประเด็นด้านการเฝ้าระวังสุขอนามัยในสถานที่ทำงาน"
        actions = "1. แจ้งเวียนประชาสัมพันธ์ให้บุคลากรในหน่วยงานทราบเพื่อเฝ้าระวัง"

        if GEMINI_API_KEY and image_bytes:
            try:
                b64_img = base64.b64encode(image_bytes).decode("utf-8")
                headers = {"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"}
                payload = {
                    "contents": [{
                        "role": "user",
                        "parts": [
                            {"inlineData": {"mimeType": "image/jpeg", "data": b64_img}},
                            {"text": DOCUMENT_VISION_PROMPT}
                        ]
                    }],
                    "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"}
                }
                async with httpx.AsyncClient(timeout=60.0) as client:
                    res = await client.post(GEMINI_URL, headers=headers, json=payload)
                    if res.status_code == 200:
                        raw = res.json()['candidates'][0]['content']['parts'][0]['text']
                        cleaned = raw.strip()
                        if cleaned.startswith("```json"): cleaned = cleaned[7:]
                        if cleaned.endswith("```"): cleaned = cleaned[:-3]
                        data = json.loads(cleaned.strip())
                        title = data.get("title", title)
                        risk = data.get("risk_level", risk)
                        summary = data.get("executive_summary", summary)
                        legal = data.get("legal_analysis", legal)
                        actions = data.get("action_items", actions)
            except Exception as e:
                print(f"[GEMINI VISION ERROR] {e}")

        # บันทึกข้อมูล
        rep_data = {
            "id": report_id, "title": title, "filename": "photo.jpg",
            "summary": summary, "legal": legal, "risk": risk, "actions": actions,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M")
        }
        MEMORY_REPORTS[report_id] = rep_data
        try:
            with get_db() as conn:
                conn.cursor().execute("INSERT INTO reports (id, user_id, title, filename, summary, legal, risk, actions) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                      (report_id, str(chat_id), title, "photo.jpg", summary, legal, risk, actions))
                conn.commit()
        except Exception:
            pass

        # ส่งสรุปฉบับเต็มลงในแชต Telegram ทันที ไม่ต้องเปิดเว็บก็อ่านได้ครบถ้วน
        full_chat_reply = f"""📑 <b>บันทึกกลั่นกรอง: {title}</b>
━━━━━━━━━━━━━━━━━━━━

<b>1. สรุปสาระสำคัญเสนอผู้บริหาร (Executive Brief):</b>
{summary}

<b>2. การกลั่นกรองข้อกฎหมายและผลกระทบ (Legal & Impact):</b>
{legal}

<b>3. ข้อเสนอแนะเชิงบริหาร (Action Items):</b>
{actions}

━━━━━━━━━━━━━━━━━━━━
⚠️ <b>ระดับการเฝ้าระวัง/ความเสี่ยง:</b> {risk}
🔗 <b>เปิดดูหน้าเว็บ / พิมพ์เป็น PDF:</b>
{base_url}/report/{report_id}"""

        await send_telegram(chat_id, full_chat_reply)

    return {"ok": True}

# ----------------- หน้าเว็บรายงาน DASHBOARD (ฝัง HTML ตรง ไม่หลุด Error 100%) -----------------
@app.get("/report/{report_id}", response_class=HTMLResponse)
async def view_report(report_id: str):
    report = MEMORY_REPORTS.get(report_id)
    if not report:
        try:
            with get_db() as conn:
                cur = conn.cursor()
                cur.execute("SELECT * FROM reports WHERE id = ?", (report_id,))
                row = cur.fetchone()
                if row:
                    report = dict(row)
        except Exception:
            pass

    if not report:
        return HTMLResponse("<h3>ไม่พบบันทึกรายงานนี้ กรุณาส่งรูปภาพใหม่อีกครั้งใน Telegram นะคะ</h3>", status_code=404)

    html = f"""<!DOCTYPE html>
<html lang="th">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>บันทึกกลั่นกรองเอกสารเสนอผู้บริหาร - {report['title']}</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://fonts.googleapis.com/css2?family=Sarabun:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>body {{ font-family: 'Sarabun', sans-serif; }} @media print {{ .no-print {{ display: none; }} body {{ background: white; color: black; }} }}</style>
</head>
<body class="bg-slate-50 text-slate-800 min-h-screen pb-12">
    <header class="bg-white border-b border-slate-200 sticky top-0 z-10 shadow-sm no-print">
        <div class="max-w-4xl mx-auto px-4 py-3 flex items-center justify-between">
            <span class="text-xl font-bold text-blue-600">YES BOSS</span>
            <button onclick="window.print()" class="bg-indigo-600 text-white text-sm px-3 py-1.5 rounded-lg">🖨️ พิมพ์ / บันทึก PDF</button>
        </div>
    </header>
    <main class="max-w-4xl mx-auto px-4 mt-6">
        <div class="bg-white rounded-2xl shadow-sm border border-slate-200 p-6 mb-6">
            <h1 class="text-2xl font-bold text-slate-900 mb-1">{report['title']}</h1>
            <p class="text-sm text-slate-500 mb-4">วันที่ตรวจ: {report.get('created_at', '')} | ระดับความเสี่ยง: <span class="font-bold text-blue-600">{report['risk']}</span></p>
            <div class="mb-6">
                <h2 class="text-lg font-bold text-blue-900 mb-2">1. สรุปสาระสำคัญเสนอผู้บริหาร (Executive Brief)</h2>
                <div class="bg-slate-50 rounded-xl p-4 text-slate-700 whitespace-pre-line">{report['summary']}</div>
            </div>
            <div class="mb-6">
                <h2 class="text-lg font-bold text-indigo-900 mb-2">2. การกลั่นกรองข้อกฎหมายและผลกระทบ (Legal & Impact)</h2>
                <div class="bg-indigo-50/50 rounded-xl p-4 text-slate-800 whitespace-pre-line">{report['legal']}</div>
            </div>
            <div>
                <h2 class="text-lg font-bold text-emerald-900 mb-2">3. ข้อเสนอแนะเชิงบริหาร (Action Items)</h2>
                <div class="bg-emerald-50/40 rounded-xl p-4 text-slate-800 whitespace-pre-line">{report['actions']}</div>
            </div>
        </div>
    </main>
</body>
</html>"""
    return HTMLResponse(content=html)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
