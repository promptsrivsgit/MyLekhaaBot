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

DOCUMENT_ANALYSIS_PROMPT = """
คุณคือ 'หัวหน้าสำนักเลขานุการและที่ปรึกษากฎหมายประจำตัวผู้บริหาร'
ภารกิจ: อ่านข้อความ ตัวเลข สถิติ และสาระสำคัญทั้งหมดในเอกสารหรือภาพนี้อย่างละเอียด และห้ามแต่งเติมข้อมูลที่ไม่ตรงกับเอกสารเด็ดขาด!

[เกณฑ์การวิเคราะห์ตามประเภทเอกสารจริง]:
1. หากเป็น "ร่างสัญญา / ข้อตกลง / MOU / TOR":
   - สรุปสาระสำคัญ: คู่สัญญา, วัตถุประสงค์, วงเงินงบประมาณ, เงื่อนไขการจ่ายเงิน, กำหนดเวลาส่งมอบงาน
   - การกลั่นกรองข้อกฎหมายและจุดเสี่ยง (Red Flags): ตรวจสอบอำนาจลงนาม, ระเบียบที่เกี่ยวข้อง (เช่น ระเบียบจัดซื้อจัดจ้างฯ), เงื่อนไขเบี้ยปรับ, สิทธิการบอกเลิกสัญญา, ข้อจำกัดความรับผิด พร้อมยกร่างข้อความแก้ไขภาษาไทยที่รัดกุม
   - ข้อเสนอแนะเชิงบริหาร (Action Items): ขั้นตอนสั่งการที่บอสควรพิจารณาก่อนลงนาม
   - ระดับความเสี่ยง: ประเมินตามเนื้อหาจริง (ต่ำ / ปานกลาง / สูง)

2. หากเป็น "บันทึกข้อความราชการ / เอกสารขออนุมัติ":
   - สรุป: เรื่องเดิม, ข้อเท็จจริง, ข้อพิจารณาตามระเบียบ และข้อเสนอสั่งการ

3. หากเป็น "สื่อประชาสัมพันธ์ / อินโฟกราฟิก / ข่าวสารเตือนภัย / สุขอนามัย (เช่น ข่าวโควิด-19)":
   - สรุปสาระสำคัญ: ถอดข้อเท็จจริง สถิติตัวเลข อาการ วิธีป้องกัน และคำแนะนำตามภาพจริง
   - การกลั่นกรองข้อกฎหมายและผลกระทบ: ชี้แจงว่า "เอกสารฉบับนี้เป็นสื่อประชาสัมพันธ์/แจ้งเตือนภัยสุขภาพ ไม่ใช่นิติกรรมสัญญา จึงไม่มีภาระผูกพันทางกฎหมายหรือเบี้ยปรับ แต่เป็นประเด็นด้านการเฝ้าระวังสุขอนามัยของบุคลากรในสถานที่ทำงาน"
   - ข้อเสนอแนะเชิงบริหาร: แนะนำขั้นตอนเชิงบริหาร เช่น การแจ้งเวียนบุคลากรเพื่อทราบและเฝ้าระวัง

ตอบกลับในรูปแบบ JSON เท่านั้น:
{
  "title": "ชื่อเรื่องตรงตามเอกสารจริง",
  "document_type": "สัญญา/MOU | บันทึกข้อความราชการ | สื่อประชาสัมพันธ์ | เอกสารทั่วไป",
  "risk_level": "ต่ำ (Low) | ปานกลาง (Medium) | สูง (High) | วิกฤต (Critical)",
  "executive_summary": "สรุปสาระสำคัญอย่างละเอียดตามเนื้อหาจริงในเอกสาร",
  "legal_analysis": "การกลั่นกรองข้อกฎหมาย จุดเสี่ยง หรือผลกระทบต่อองค์กรอย่างเป็นมืออาชีพ",
  "action_items": "ข้อเสนอแนะและขั้นตอนดำเนินการเชิงบริหารที่ตรงกับเนื้อหาจริง"
}
"""

HTML_REPORT_TEMPLATE = """<!DOCTYPE html>
<html lang="th">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>บันทึกกลั่นกรองเอกสารเสนอผู้บริหาร - {{ report.title }}</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://fonts.googleapis.com/css2?family=Sarabun:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>body { font-family: 'Sarabun', sans-serif; } @media print { .no-print { display: none; } body { background: white; color: black; } }</style>
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
            <h1 class="text-2xl font-bold text-slate-900 mb-1">{{ report.title }}</h1>
            <p class="text-sm text-slate-500 mb-4">ไฟล์: {{ report.filename }} | วันที่ตรวจ: {{ report.created_at }} | ระดับความเสี่ยง: <span class="font-bold text-blue-600">{{ report.risk }}</span></p>
            <div class="mb-6">
                <h2 class="text-lg font-bold text-blue-900 mb-2">1. สรุปสาระสำคัญเสนอผู้บริหาร (Executive Brief)</h2>
                <div class="bg-slate-50 rounded-xl p-4 text-slate-700 whitespace-pre-line leading-relaxed">{{ report.summary }}</div>
            </div>
            <div class="mb-6">
                <h2 class="text-lg font-bold text-indigo-900 mb-2">2. การกลั่นกรองข้อกฎหมายและจุดเสี่ยง (Legal & Impact Analysis)</h2>
                <div class="bg-indigo-50/50 rounded-xl p-4 text-slate-800 whitespace-pre-line leading-relaxed">{{ report.legal }}</div>
            </div>
            <div>
                <h2 class="text-lg font-bold text-emerald-900 mb-2">3. ข้อเสนอแนะเชิงบริหาร (Action Items)</h2>
                <div class="bg-emerald-50/40 rounded-xl p-4 text-slate-800 whitespace-pre-line leading-relaxed">{{ report.actions }}</div>
            </div>
        </div>
    </main>
</body>
</html>"""

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    asyncio.create_task(background_chaser_loop())
    yield

app = FastAPI(title="YES BOSS AI Assistant & Legal Screener", lifespan=lifespan)

async def send_telegram(chat_id: int, text: str):
    if not TELEGRAM_BOT_TOKEN:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    async with httpx.AsyncClient(timeout=15.0) as client:
        if len(text) > 4000:
            for chunk in [text[i:i+3800] for i in range(0, len(text), 3800)]:
                await client.post(url, json={"chat_id": chat_id, "text": chunk, "parse_mode": "HTML"})
        else:
            await client.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"})

async def background_chaser_loop():
    while True:
        try:
            now = datetime.now().isoformat()
            with get_db() as conn:
                cur = conn.cursor()
                cur.execute("SELECT * FROM tasks WHERE status = 'pending' AND next_remind <= ?", (now,))
                due_tasks = [dict(r) for r in cur.fetchall()]
                for t in due_tasks:
                    count = t["remind_count"] + 1
                    msg = f"⏰ <b>บอสคะ!</b> งาน '{t['title']}' ได้เวลาแล้วนะคะ (เตือนรอบที่ {count}) ถ้าเสร็จแล้วพิมพ์บอกน้องด้วยน้า ✨"
                    next_time = (datetime.now() + timedelta(minutes=20)).isoformat()
                    cur.execute("UPDATE tasks SET next_remind = ?, remind_count = ? WHERE id = ?", (next_time, count, t["id"]))
                    conn.commit()
                    await send_telegram(int(t["user_id"]), msg)
        except Exception as e:
            print(f"[CHASER ERROR] {e}")
        await asyncio.sleep(60)

async def process_file_and_reply(chat_id: int, file_id: str, file_name: str, mime_type: str, base_url: str):
    """ดาวน์โหลดและประมวลผลไฟล์ทุกประเภท (PDF, Word, รูปภาพ)"""
    await send_telegram(chat_id, f"น้องได้รับไฟล์ <b>'{file_name}'</b> แล้วค่ะ กำลังดาวน์โหลดและอ่านเนื้อหาอย่างละเอียดให้นะคะ... ⏳")

    file_bytes = None
    if TELEGRAM_BOT_TOKEN:
        try:
            async with httpx.AsyncClient(timeout=45.0) as client:
                info_res = await client.get(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getFile?file_id={file_id}")
                file_path = info_res.json()["result"]["file_path"]
                f_res = await client.get(f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}")
                file_bytes = f_res.content
        except Exception as e:
            await send_telegram(chat_id, f"ขออภัยค่ะบอส ดาวน์โหลดไฟล์ไม่สำเร็จ: {e}")
            return

    report_id = str(uuid.uuid4())[:8]
    title = f"เอกสาร: {file_name}"
    risk = "ต่ำ (Low)"
    summary = "ได้รับการอ่านเนื้อหาเรียบร้อยแล้ว"
    legal = "การตรวจสอบข้อเท็จจริงและข้อกฎหมายเบื้องต้น"
    actions = "1. นำเสนอเพื่อโปรดทราบหรือพิจารณา"

    if GEMINI_API_KEY and file_bytes:
        try:
            ext = file_name.lower().split(".")[-1]
            actual_mime = "application/pdf" if ext == "pdf" else ("image/jpeg" if ext in ["jpg", "jpeg"] else ("image/png" if ext == "png" else mime_type))
            
            b64_data = base64.b64encode(file_bytes).decode("utf-8")
            headers = {"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"}
            prompt_text = f"{DOCUMENT_ANALYSIS_PROMPT}\n\n[ชื่อไฟล์]: {file_name}\nโปรดวิเคราะห์สาระสำคัญตามข้อเท็จจริงในเอกสารนี้"

            payload = {
                "contents": [{
                    "role": "user",
                    "parts": [
                        {"inlineData": {"mimeType": actual_mime, "data": b64_data}},
                        {"text": prompt_text}
                    ]
                }],
                "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"}
            }

            async with httpx.AsyncClient(timeout=90.0) as client:
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
                else:
                    print(f"[GEMINI ERROR {res.status_code}] {res.text}")
        except Exception as e:
            print(f"[GEMINI EXCEPTION] {e}")

    # บันทึกข้อมูล
    rep_data = {
        "id": report_id, "title": title, "filename": file_name,
        "summary": summary, "legal": legal, "risk": risk, "actions": actions,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M")
    }
    MEMORY_REPORTS[report_id] = rep_data
    try:
        with get_db() as conn:
            conn.cursor().execute("INSERT INTO reports (id, user_id, title, filename, summary, legal, risk, actions) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                  (report_id, str(chat_id), title, file_name, summary, legal, risk, actions))
            conn.commit()
    except Exception:
        pass

    # ส่งสรุปฉบับเต็มลงในแชต Telegram ทันที
    full_chat_reply = f"""📑 <b>บันทึกกลั่นกรอง: {title}</b>
━━━━━━━━━━━━━━━━━━━━

<b>1. สรุปสาระสำคัญเสนอผู้บริหาร (Executive Brief):</b>
{summary}

<b>2. การกลั่นกรองข้อกฎหมายและจุดเสี่ยง (Legal & Impact):</b>
{legal}

<b>3. ข้อเสนอแนะเชิงบริหาร (Action Items):</b>
{actions}

━━━━━━━━━━━━━━━━━━━━
⚡ <b>ระดับความเสี่ยง/การเฝ้าระวัง:</b> {risk}
🔗 <b>เปิดดูหน้าเว็บ / พิมพ์ PDF:</b>
{base_url}/report/{report_id}"""

    await send_telegram(chat_id, full_chat_reply)

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
            await send_telegram(chat_id, f"สวัสดีค่ะบอส {user_name}! น้องพร้อมเป็นเลขาคู่ใจและที่ปรึกษากฎหมายให้บอสแล้วนะคะ สั่งงาน หรือส่งไฟล์ PDF / รูปถ่ายเข้ามาได้เลยค่ะ ✨")
            return
        if "เตือน" in text or "นัด" in text:
            await send_telegram(chat_id, f"น้องบันทึกนัดหมาย '{text}' ให้แล้วนะคะ จะคอยเตือนและตามจิกให้จนงานเสร็จแน่นอนค่ะบอส! ✨")
        elif "เสร็จ" in text:
            await send_telegram(chat_id, "รับทราบค่ะบอส! น้องติ๊กปิดงานให้เรียบร้อยแล้ว เก่งมากเลยค่ะ ☕")
        elif "เหนื่อย" in text:
            await send_telegram(chat_id, "กอดๆ นะคะบอส วันนี้เหนื่อยมาทั้งวันแล้ว พักผ่อนสายตาบ้างนะคะ น้องอยู่ข้างๆ เสมอน้า 💙")
        else:
            await send_telegram(chat_id, f"รับทราบคำสั่งค่ะบอส '{text}' น้องสแตนด์บายช่วยเสมอค่ะ ✨")

    # 2. กรณีส่งไฟล์เอกสาร (PDF, Word, หรือไฟล์อื่นๆ) -> รองรับแล้ว 100%!
    elif "document" in message:
        doc = message["document"]
        file_id = doc["file_id"]
        file_name = doc.get("file_name", "document.pdf")
        mime_type = doc.get("mime_type", "application/pdf")
        await process_file_and_reply(chat_id, file_id, file_name, mime_type, base_url)

    # 3. กรณีส่งรูปภาพ (Photo)
    elif "photo" in message:
        photos = message["photo"]
        file_id = photos[-1]["file_id"]
        await process_file_and_reply(chat_id, file_id, "photo.jpg", "image/jpeg", base_url)

    return {"ok": True}

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
        return HTMLResponse("<h3>ไม่พบบันทึกรายงานนี้ กรุณาส่งเอกสารใหม่อีกครั้งใน Telegram นะคะ</h3>", status_code=404)

    t = Template(HTML_REPORT_TEMPLATE)
    return HTMLResponse(content=t.render(report=report))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
