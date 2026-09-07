import os
import json
import uuid
import base64
import asyncio
import sqlite3
import httpx
import pypdf
import io
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
คุณคือ 'หัวหน้าสำนักเลขานุการและที่ปรึกษากฎหมายประจำตัวผู้บริหารระดับสูง'
หน้าที่: วิเคราะห์และกลั่นกรองเอกสารนี้อย่างละเอียด ถูกต้อง แม่นยำ ตรงตามข้อเท็จจริงในเอกสาร 100% ห้ามตอบแบบกว้างๆ หรือแต่งเติมเด็ดขาด!

โปรดจัดทำบทวิเคราะห์ในรูปแบบ JSON ตามโครงสร้างนี้:
{
  "title": "ชื่อประกาศ/สัญญา/ระเบียบ ตามที่ระบุจริงในเอกสาร",
  "document_type": "ประกาศ/นโยบาย | ร่างสัญญา/MOU | บันทึกข้อความราชการ | เอกสารทั่วไป",
  "risk_level": "ต่ำ (Low) | ปานกลาง (Medium) | สูง (High)",
  "executive_summary": "สรุปสาระสำคัญเสนอผู้บริหาร: สรุปความเป็นมา, วัตถุประสงค์, โครงสร้างการกำกับดูแล/คู่สัญญา, ขอบเขตนโยบาย, ข้อกำหนดและข้อห้ามสำคัญ โดยระบุข้อเท็จจริงและตัวเลขที่ชัดเจน",
  "legal_analysis": "การกลั่นกรองข้อกฎหมาย ระเบียบ และผลกระทบต่อองค์กร: ฐานอำนาจตามกฎหมายที่ใช้ออกเอกสาร, กฎหมายและระเบียบที่ต้องปฏิบัติตาม, การจัดระดับชั้นความลับ, และจุดเสี่ยง (Red Flags) ที่ต้องระมัดระวังในทางปฏิบัติ",
  "action_items": "ข้อเสนอแนะเชิงบริหารและขั้นตอนสั่งการ: ลิสต์เป็นข้อๆ ชัดเจน ว่าผู้บริหารควรสั่งการให้สายงานใดดำเนินการเรื่องใดต่ออย่างเป็นรูปธรรม"
}
"""

HTML_REPORT_TEMPLATE = """<!DOCTYPE html>
<html lang="th">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ report.title }} - บันทึกกลั่นกรองเสนอผู้บริหาร</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://fonts.googleapis.com/css2?family=Sarabun:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>body { font-family: 'Sarabun', sans-serif; } @media print { .no-print { display: none; } body { background: white; color: black; } }</style>
</head>
<body class="bg-slate-50 text-slate-800 min-h-screen pb-12">
    <header class="bg-white border-b border-slate-200 sticky top-0 z-10 shadow-sm no-print">
        <div class="max-w-4xl mx-auto px-4 py-3 flex items-center justify-between">
            <span class="text-xl font-bold text-blue-700">YES BOSS</span>
            <button onclick="window.print()" class="bg-blue-600 hover:bg-blue-700 text-white text-sm px-4 py-2 rounded-lg font-medium shadow-sm transition">🖨️ พิมพ์ / บันทึกเป็น PDF</button>
        </div>
    </header>
    <main class="max-w-4xl mx-auto px-4 mt-6">
        <div class="bg-white rounded-2xl shadow-sm border border-slate-200 p-6 md:p-8 mb-6">
            <h1 class="text-2xl md:text-3xl font-bold text-slate-900 mb-2">{{ report.title }}</h1>
            <p class="text-sm text-slate-500 mb-6 border-b border-slate-100 pb-4">
                ไฟล์: <span class="font-medium text-slate-700">{{ report.filename }}</span> | วันที่ตรวจ: {{ report.created_at }} | 
                ระดับความเสี่ยง: <span class="font-semibold text-blue-700">{{ report.risk }}</span>
            </p>
            <div class="mb-8">
                <h2 class="text-lg font-bold text-blue-900 mb-3 flex items-center">
                    <span class="w-2.5 h-6 bg-blue-600 rounded-full mr-2.5"></span>
                    1. สรุปสาระสำคัญเสนอผู้บริหาร (Executive Brief)
                </h2>
                <div class="bg-slate-50 rounded-xl p-5 border border-slate-100 text-slate-700 whitespace-pre-line leading-relaxed text-base">{{ report.summary }}</div>
            </div>
            <div class="mb-8">
                <h2 class="text-lg font-bold text-indigo-900 mb-3 flex items-center">
                    <span class="w-2.5 h-6 bg-indigo-600 rounded-full mr-2.5"></span>
                    2. การกลั่นกรองข้อกฎหมายและผลกระทบ (Legal & Impact Analysis)
                </h2>
                <div class="bg-indigo-50/50 rounded-xl p-5 border border-indigo-100/70 text-slate-800 whitespace-pre-line leading-relaxed text-base">{{ report.legal }}</div>
            </div>
            <div>
                <h2 class="text-lg font-bold text-emerald-900 mb-3 flex items-center">
                    <span class="w-2.5 h-6 bg-emerald-600 rounded-full mr-2.5"></span>
                    3. ข้อเสนอแนะเชิงบริหารและขั้นตอนสั่งการ (Action Items)
                </h2>
                <div class="bg-emerald-50/40 rounded-xl p-5 border border-emerald-100 text-slate-800 whitespace-pre-line leading-relaxed text-base">{{ report.actions }}</div>
            </div>
        </div>
    </main>
</body>
</html>"""

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield

app = FastAPI(title="YES BOSS Assistant", lifespan=lifespan)

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

def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """พยายามสกัดข้อความ (กรณีเป็น PDF ดิจิทัล)"""
    try:
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        extracted = []
        for idx, page in enumerate(reader.pages):
            txt = page.extract_text()
            if txt and len(txt.strip()) > 0:
                extracted.append(f"--- หน้า {idx+1} ---\n{txt}")
        return "\n\n".join(extracted).strip()
    except Exception as e:
        return ""

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

    if "text" in message:
        text = message["text"].strip()
        if text.startswith("/start"):
            await send_telegram(chat_id, f"สวัสดีค่ะบอส {user_name}! น้องพร้อมเป็นเลขาคู่ใจและที่ปรึกษากฎหมายให้บอสแล้วนะคะ สั่งงาน หรือส่งไฟล์ PDF / รูปถ่ายเข้ามาได้เลยค่ะ ✨")
            return
        if "เตือน" in text or "นัด" in text:
            await send_telegram(chat_id, f"น้องบันทึกนัดหมาย '{text}' ให้แล้วนะคะ ✨")
        elif "เสร็จ" in text:
            await send_telegram(chat_id, "รับทราบค่ะบอส! น้องติ๊กปิดงานเรียบร้อยแล้วค่ะ ☕")
        elif "เหนื่อย" in text:
            await send_telegram(chat_id, "กอดๆ นะคะบอส วันนี้เหนื่อยมาทั้งวันแล้ว พักผ่อนบ้างนะคะ 💙")
        else:
            await send_telegram(chat_id, f"รับทราบคำสั่งค่ะบอส '{text}' ✨")

    # กรณีส่งไฟล์เอกสาร (PDF, Word, ภาพสแกน)
    elif "document" in message or "photo" in message:
        if "document" in message:
            doc = message["document"]
            file_id = doc["file_id"]
            file_name = doc.get("file_name", "document.pdf")
            mime_type = doc.get("mime_type", "application/pdf")
        else:
            photos = message["photo"]
            file_id = photos[-1]["file_id"]
            file_name = "photo.jpg"
            mime_type = "image/jpeg"

        await send_telegram(chat_id, f"น้องได้รับเอกสาร <b>'{file_name}'</b> แล้วค่ะ กำลังอ่านข้อความและกลั่นกรองวิเคราะห์ให้อย่างละเอียดนะคะ... ⏳")

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

        # 1. ตรวจสอบว่าเป็น PDF ที่มีตัวหนังสือ หรือเป็นภาพสแกน
        pdf_text = ""
        ext = file_name.lower().split(".")[-1]
        if ext == "pdf" and file_bytes:
            pdf_text = extract_text_from_pdf(file_bytes)

        report_id = str(uuid.uuid4())[:8]
        title = file_name
        risk = "ปานกลาง (Medium)"
        summary = "กำลังประมวลผล..."
        legal = "กำลังประมวลผล..."
        actions = "กำลังประมวลผล..."

        # 2. ส่งให้ Gemini 1.5 Flash วิเคราะห์
        if GEMINI_API_KEY and file_bytes:
            headers = {"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"}
            
            # กรณีที่ 1: เป็น PDF ดิจิทัล มีข้อความตัวหนังสือชัดเจน
            if len(pdf_text) > 100:
                prompt_content = f"{DOCUMENT_ANALYSIS_PROMPT}\n\n[ชื่อไฟล์]: {file_name}\n[เนื้อหาจากเอกสาร]:\n{pdf_text[:50000]}"
                parts = [{"text": prompt_content}]
            # กรณีที่ 2: เป็น PDF ภาพสแกน (เช่น ประกาศ กปน.) หรือเป็นไฟล์รูปภาพ ให้ใช้ Gemini Vision อ่าน OCR ทุกหน้าโดยตรง
            else:
                actual_mime = "application/pdf" if ext == "pdf" else ("image/jpeg" if ext in ["jpg", "jpeg"] else ("image/png" if ext == "png" else mime_type))
                b64_data = base64.b64encode(file_bytes).decode("utf-8")
                prompt_content = f"{DOCUMENT_ANALYSIS_PROMPT}\n\n[ชื่อไฟล์]: {file_name}\nโปรดอ่านข้อความทุกหน้าจากภาพสแกนในเอกสารนี้อย่างละเอียด และจัดทำรายงานสรุปตามเกณฑ์"
                parts = [
                    {"inlineData": {"mimeType": actual_mime, "data": b64_data}},
                    {"text": prompt_content}
                ]

            payload = {
                "contents": [{"role": "user", "parts": parts}],
                "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"}
            }

            try:
                async with httpx.AsyncClient(timeout=120.0) as client:
                    res = await client.post(GEMINI_URL, headers=headers, json=payload)
                    if res.status_code == 200:
                        raw = res.json()['candidates'][0]['content']['parts'][0]['text']
                        data = json.loads(raw.strip())
                        title = data.get("title", title)
                        risk = data.get("risk_level", risk)
                        summary = data.get("executive_summary", summary)
                        legal = data.get("legal_analysis", legal)
                        actions = data.get("action_items", actions)
                    else:
                        summary = f"API ตอบกลับสถานะ {res.status_code}: {res.text[:300]}"
            except Exception as e:
                summary = f"เกิดข้อผิดพลาดในการวิเคราะห์ AI: {str(e)}"

        rep_data = {
            "id": report_id, "title": title, "filename": file_name,
            "summary": summary, "legal": legal, "risk": risk, "actions": actions,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M")
        }
        MEMORY_REPORTS[report_id] = rep_data

        chat_reply = f"""📑 <b>{title}</b>
━━━━━━━━━━━━━━━━━━━━

<b>1. สรุปสาระสำคัญเสนอผู้บริหาร (Executive Brief):</b>
{summary}

<b>2. การกลั่นกรองข้อกฎหมายและผลกระทบ (Legal & Impact):</b>
{legal}

<b>3. ข้อเสนอแนะเชิงบริหาร (Action Items):</b>
{actions}

━━━━━━━━━━━━━━━━━━━━
⚡ <b>ระดับความเสี่ยง/การเฝ้าระวัง:</b> {risk}
🔗 <b>เปิดดูหน้าเว็บ / พิมพ์เป็น PDF:</b>
{base_url}/report/{report_id}"""

        await send_telegram(chat_id, chat_reply)

    return {"ok": True}

@app.get("/report/{report_id}", response_class=HTMLResponse)
async def view_report(report_id: str):
    report = MEMORY_REPORTS.get(report_id)
    if not report:
        return HTMLResponse("<h3>ไม่พบรายงาน หรือระบบเพิ่งอัปเดต กรุณาส่งเอกสารใหม่อีกครั้งนะคะ</h3>", status_code=404)
    t = Template(HTML_REPORT_TEMPLATE)
    return HTMLResponse(content=t.render(report=report))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
