import os
import json
import httpx
import uuid
from datetime import datetime, timedelta
from typing import Dict, Any, Tuple, Optional
from services.database import (
    add_task, complete_task_by_title, save_memory, search_memories,
    get_pending_tasks_for_user, save_report
)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-1.5-flash")
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"

TYPHOON_API_KEY = os.environ.get("TYPHOON_API_KEY", "")
TYPHOON_MODEL = os.environ.get("TYPHOON_MODEL", "typhoon-2.5-30b-instruct")
TYPHOON_URL = "https://api.opentyphoon.ai/v1/chat/completions"

SECRETARY_PROMPT_TYPHOON = """
คุณคือ 'เลขาคู่ใจและที่ปรึกษาประจำตัวของบอส' (Personal Assistant & Executive Counsel)
บุคลิกภาพและการตอบสนอง:
1. ใช้ภาษาไทยที่สุภาพ อ่อนหวาน เป็นธรรมชาติ เรียกผู้ใช้ว่า "บอส" และแทนตัวเองว่า "น้อง" หรือ "เลขา"
2. มี Empathy สูงมาก: ถ้าบอสบ่นว่าเหนื่อย ท้อ หรือเครียด ให้ปลอบประโลม ให้กำลังใจด้วยภาษาไทยที่อบอุ่น
3. สรุปใจความและงานที่บอสสั่งอย่างรัดกุม
4. ตอบกลับเป็น JSON เพื่อให้ระบบจัดการงานต่อไป:
{
  "thought": "ความเห็นสั้นๆ",
  "reply_message": "ข้อความตอบบอส",
  "action": "none | add_task | complete_task | save_memory | recall_memory",
  "action_data": {
     "title": "ชื่องาน",
     "due_iso": "YYYY-MM-DDTHH:MM:SS",
     "key_name": "หัวข้อจำ",
     "content": "เนื้อหา",
     "query": "คำค้น"
  }
}
"""

LEGAL_PROMPT_TYPHOON = """
คุณคือ 'หัวหน้านิติกรและที่ปรึกษากฎหมายประจำตัวผู้บริหาร'
ภารกิจ: นำสาระสำคัญของเอกสารสัญญาหรือหนังสือราชการที่สกัดมาแล้ว มาตรวจสอบข้อกฎหมายไทย ระเบียบราชการ/รัฐวิสาหกิจ และวิเคราะห์จุดเสี่ยง

โปรดจัดทำรายงานในรูปแบบ JSON:
{
  "title": "ชื่อเรื่องเอกสาร",
  "risk_level": "ต่ำ (Low) | ปานกลาง (Medium) | สูง (High) | วิกฤต (Critical)",
  "executive_summary": "สรุป 1 หน้าผู้บริหาร: เรื่องเดิม, ข้อเท็จจริง, ข้อพิจารณา และข้อเสนอแนะเชิงบริหาร",
  "legal_analysis": "การตรวจสอบข้อกฎหมาย/ระเบียบไทย: อำนาจลงนาม, ระเบียบที่อ้างอิง, จุดเสี่ยงหรือข้อสัญญาที่เสียเปรียบ (Red Flags), พร้อมยกร่างข้อความแก้ไขภาษาไทยที่รัดกุม (Redline Replacement)",
  "action_items": "ข้อเสนอแนะที่บอสควรสั่งการหรือเซ็นข้อสังเกต",
  "chat_brief": "ข้อความแจ้งเตือนบอสในแชต สรุปประเด็นหลักและจุดระวัง ⚠️"
}
"""

async def call_gemini(prompt: str) -> Optional[str]:
    """เรียกใช้ Gemini 1.5 Flash (เด่นเรื่อง Long Context, Multimodal, และความเร็ว)"""
    if not GEMINI_API_KEY:
        return None
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            res = await client.post(
                GEMINI_URL,
                json={"contents": [{"role": "user", "parts": [{"text": prompt}]}]}
            )
            if res.status_code == 200:
                data = res.json()
                return data['candidates'][0]['content']['parts'][0]['text']
    except Exception as e:
        print(f"[GEMINI ERROR] {e}")
    return None

async def call_typhoon(messages: list, response_format_json: bool = False) -> Optional[str]:
    """เรียกใช้ Typhoon 2.5 (เด่นเรื่องความลึกซึ้งของภาษาไทย, วัฒนธรรมองค์กรไทย, และกฎหมายไทย)"""
    if not TYPHOON_API_KEY:
        return None
    try:
        headers = {
            "Authorization": f"Bearer {TYPHOON_API_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": TYPHOON_MODEL,
            "messages": messages,
            "temperature": 0.3
        }
        if response_format_json:
            payload["response_format"] = {"type": "json_object"}
            
        async with httpx.AsyncClient(timeout=45.0) as client:
            res = await client.post(TYPHOON_URL, headers=headers, json=payload)
            if res.status_code == 200:
                data = res.json()
                return data["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"[TYPHOON ERROR] {e}")
    return None

async def dual_engine_chat(user_id: str, message: str) -> str:
    """
    ยุทธศาสตร์ที่ 1: การสนทนาประจำวันและการดูแลใจ
    - ให้ Typhoon 2.5 เป็นหน้าด่านหลักเพื่อภาษาไทยที่นุ่มนวลและเป็นธรรมชาติ
    - หาก Typhoon ไม่พร้อม ให้ Fallback ไปที่ Gemini 1.5 Flash
    """
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    pending_tasks = get_pending_tasks_for_user(user_id)
    task_context = "\n".join([f"- {t['title']} (กำหนด: {t['due_datetime']})" for t in pending_tasks])
    
    prompt = f"{SECRETARY_PROMPT_TYPHOON}\n\nเวลาปัจจุบัน: {now_str}\nงานค้าง:\n{task_context}\n\nข้อความจากบอส: {message}"
    
    # 1. ลองเรียก Typhoon ก่อน
    resp_text = await call_typhoon([{"role": "user", "content": prompt}], response_format_json=True)
    
    # 2. ถ้า Typhoon ไม่มี Key หรือขัดข้อง สลับไป Gemini
    if not resp_text:
        resp_text = await call_gemini(prompt)
        
    if resp_text:
        try:
            # ทำความสะอาด JSON ถ้ามี Markdown block
            cleaned = resp_text.strip()
            if cleaned.startswith("```json"):
                cleaned = cleaned[7:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            data = json.loads(cleaned.strip())
            
            action = data.get("action", "none")
            act_data = data.get("action_data", {})
            if action == "add_task":
                due = datetime.now() + timedelta(hours=2)
                if act_data.get("due_iso"):
                    try: due = datetime.fromisoformat(act_data["due_iso"])
                    except: pass
                add_task(user_id, act_data.get("title", message), due)
            elif action == "complete_task":
                complete_task_by_title(user_id, act_data.get("title", ""))
            elif action == "save_memory":
                save_memory(user_id, act_data.get("content", message), "note", act_data.get("key_name", ""))
            elif action == "recall_memory":
                mems = search_memories(user_id, act_data.get("query", message))
                if mems:
                    found = "\n".join([f"• {m['content']}" for m in mems])
                    return f"{data.get('reply_message', '')}\n\n🔍 ข้อมูลที่เคยฝากไว้:\n{found}"
                    
            return data.get("reply_message", "รับทราบค่ะบอส")
        except Exception:
            return resp_text
            
    # Fallback กรณีจำลอง (ยังไม่ได้ใส่ API Key)
    if "เตือน" in message or "นัด" in message:
        due_time = datetime.now() + timedelta(hours=1)
        add_task(user_id, message, due_time)
        return f"น้องบันทึกนัดหมาย '{message}' ให้แล้วนะคะ จะคอยเตือนและตามจิกให้จนงานเสร็จแน่นอนค่ะบอส! ✨"
    elif "เสร็จ" in message:
        complete_task_by_title(user_id, "")
        return "รับทราบค่ะบอส! น้องติ๊กปิดงานในรายการให้เรียบร้อยแล้ว เก่งมากเลยค่ะ พักดื่มน้ำหน่อยนะคะ ☕"
    elif "เหนื่อย" in message or "ท้อ" in message:
        return "กอดๆ นะคะบอส วันนี้เหนื่อยมาทั้งวันแล้ว น้องอยู่ข้างๆ เสมอนะคะ มีอะไรระบายกับน้องได้ตลอดเลยน้า พักสายตาสักนิดนะคะ 💙"
    return f"รับทราบค่ะบอส '{message}' น้องเลขาบันทึกไว้ให้แล้วนะคะ ✨"

async def dual_engine_screen_document(user_id: str, filename: str, file_text: str, base_url: str) -> Tuple[str, str]:
    """
    ยุทธศาสตร์ที่ 2: งานกลั่นกรองเอกสาร & สัญญาทางกฎหมาย (Two-Stage Pipeline)
    - ขั้นที่ 1: ให้ Gemini 1.5 Flash รับหน้าที่ "Ingestion & Extraction" (อ่านเอกสารยาวๆ ดึงข้อเท็จจริง)
    - ขั้นที่ 2: ให้ Typhoon 2.5 รับหน้าที่ "Legal Vetting & Thai Synthesis" (วิเคราะห์ระเบียบไทย จุดเสี่ยง และเรียบเรียงร่างแก้ไข)
    """
    report_id = str(uuid.uuid4())[:8]
    
    # 1. ขั้นตอน Gemini: สกัดโครงสร้างและข้อสัญญาสำคัญจากเอกสารดิบ
    extract_prompt = f"""
วิเคราะห์และสกัดข้อเท็จจริงสำคัญจากเอกสารต่อไปนี้:
ชื่อไฟล์: {filename}
เนื้อหา:
{file_text[:50000]}

โปรดสกัดออกมาเป็นหัวข้อ:
1. คู่สัญญาและวัตถุประสงค์
2. วงเงิน งบประมาณ และเงื่อนไขการจ่ายเงิน
3. กำหนดเวลาและเงื่อนไขการส่งมอบงาน
4. ข้อกำหนดเบี้ยปรับและความรับผิดชอบ
5. ข้อกำหนดการบอกเลิกสัญญาและการระงับข้อพิพาท
"""
    extracted_facts = await call_gemini(extract_prompt) or file_text[:10000]
    
    # 2. ขั้นตอน Typhoon: นำข้อเท็จจริงมากลั่นกรองกฎหมายไทยและจัดทำตาราง Redlines
    vetting_prompt = f"""
{LEGAL_PROMPT_TYPHOON}

[ชื่อเอกสาร]: {filename}
[สาระสำคัญที่สกัดได้จากสัญญา]:
{extracted_facts}
"""
    legal_resp = await call_typhoon([{"role": "user", "content": vetting_prompt}], response_format_json=True)
    
    # ถ้าไม่มี Typhoon ให้ Fallback ใช้ Gemini วิเคราะห์ต่อ
    if not legal_resp:
        legal_resp = await call_gemini(vetting_prompt)
        
    risk = "ปานกลาง (Medium)"
    title = f"บันทึกกลั่นกรอง: {filename}"
    summary = f"สรุปสาระสำคัญของเอกสาร {filename}\n{extracted_facts[:500]}"
    legal = "การตรวจสอบข้อสัญญาและจุดเสี่ยงเบื้องต้น"
    actions = "1. นำเสนอผู้บริหารพิจารณา"
    chat_brief = f"กลั่นกรองเอกสาร {filename} เรียบร้อยแล้วค่ะ"
    
    if legal_resp:
        try:
            cleaned = legal_resp.strip()
            if cleaned.startswith("```json"): cleaned = cleaned[7:]
            if cleaned.endswith("```"): cleaned = cleaned[:-3]
            data = json.loads(cleaned.strip())
            title = data.get("title", title)
            risk = data.get("risk_level", risk)
            summary = data.get("executive_summary", summary)
            legal = data.get("legal_analysis", legal)
            actions = data.get("action_items", actions)
            chat_brief = data.get("chat_brief", chat_brief)
        except Exception:
            legal = legal_resp

    save_report(report_id, user_id, title, filename, summary, legal, risk, actions)
    
    reply_text = (
        f"📑 **{title}**\n\n"
        f"{chat_brief}\n\n"
        f"⚠️ **ระดับความเสี่ยง:** {risk}\n"
        f"🔗 **เปิดดูบันทึกสรุป 1 หน้าและตารางจุดเสี่ยงฉบับเต็ม:**\n{base_url}/report/{report_id}"
    )
    return reply_text, report_id
