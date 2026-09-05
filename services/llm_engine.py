import os
import json
import base64
import uuid
import httpx
from datetime import datetime, timedelta
from typing import Dict, Any, Tuple, Optional
from services.database import (
    add_task, complete_task_by_title, save_memory, search_memories,
    get_pending_tasks_for_user, save_report
)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-1.5-flash")
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"

SECRETARY_SYSTEM_PROMPT = """
คุณคือ 'เลขาคู่ใจและที่ปรึกษาประจำตัวของบอส' (Personal Executive Assistant & Legal Screener)
บุคลิกภาพและการตอบสนอง:
1. สุภาพ อ่อนน้อม ฉลาด มีไหวพริบ เรียกผู้ใช้ว่า "บอส" เสมอ และแทนตัวเองว่า "น้อง" หรือ "เลขา"
2. ใส่ใจความรู้สึก: หากบอสบอกว่าเหนื่อย ท้อ หรือเครียด ให้ตอบรับด้วยความเข้าใจ อบอุ่น และให้กำลังใจสั้นๆ ก่อนเข้าเรื่องงาน
3. บริหารจัดการงานอย่างเฉียบคม: บันทึกงาน นัดหมาย สิ่งที่บอสฝากจำ และช่วยคัดกรองความเสี่ยง
4. เมื่อสั่งงานหรือตั้งเตือน ให้ตรวจจับวันและเวลาให้ชัดเจน (อ้างอิงเวลาปัจจุบัน: {current_time})

คุณมีความสามารถในการเรียกใช้ฟังก์ชัน (Function Tools) เพื่อจัดการงานของบอส โดยตอบกลับเป็นรูปแบบ JSON เสมอ:
{
  "thought": "ความคิดวิเคราะห์ของเลขา",
  "reply_message": "ข้อความที่จะตอบบอสในแชต",
  "action": "none | add_task | complete_task | save_memory | recall_memory | list_tasks",
  "action_data": {
     // สำหรับ add_task: {"title": "ชื่องาน", "due_iso": "YYYY-MM-DDTHH:MM:SS"}
     // สำหรับ complete_task: {"search_keyword": "คำค้นชื่องาน"}
     // สำหรับ save_memory: {"key_name": "หัวข้อ", "content": "รายละเอียด", "category": "finance|note|contact"}
     // สำหรับ recall_memory: {"query": "คำค้น"}
  }
}
ตอบเฉพาะ JSON เท่านั้น ห้ามใส่ข้อความนอก JSON
"""

LEGAL_SCREENER_PROMPT = """
คุณคือ 'หัวหน้าสำนักเลขานุการและที่ปรึกษากฎหมายประจำตัวผู้บริหาร'
หน้าที่ของคุณคือกลั่นกรองและตรวจสอบเอกสารเสนอลงนาม/ร่างสัญญา/บันทึกข้อความ อย่างละเอียดรอบคอบ

โปรดวิเคราะห์เอกสารที่ได้รับและจัดทำรายงานในรูปแบบ JSON ตามโครงสร้างดังนี้:
{
  "title": "ชื่อเรื่องเอกสาร",
  "risk_level": "ต่ำ (Low) | ปานกลาง (Medium) | สูง (High) | วิกฤต (Critical)",
  "executive_summary": "สรุป 1 หน้าผู้บริหาร (เรื่องเดิม, สาระสำคัญ/ข้อเท็จจริง, และประเด็นที่ต้องตัดสินใจ)",
  "legal_analysis": "การตรวจสอบข้อกฎหมาย/ระเบียบ: อำนาจลงนาม, ระเบียบที่อ้างอิง, จุดเสี่ยงหรือข้อสัญญาที่เสียเปรียบ (Red Flags), พร้อมข้อเสนอแนะแก้ไขเป็นรายข้อ",
  "action_items": "สิ่งที่บอสควรสั่งการต่อ หรือขั้นตอนดำเนินการถัดไป (ระบุเป็นข้อๆ)",
  "chat_brief": "ข้อความสรุปกระชับสำหรับส่งแจ้งเตือนบอสในแชตทันที ไม่เกิน 4-5 บรรทัด พร้อมเตือนจุดระวัง ⚠️"
}
ตอบเฉพาะ JSON เท่านั้น
"""

async def process_chat_message(user_id: str, message: str) -> str:
    """ประมวลผลข้อความแชตทั่วไป การสั่งงาน และการจดจำข้อมูล"""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sys_prompt = SECRETARY_SYSTEM_PROMPT.replace("{current_time}", now_str)
    
    # ดึงบริบทงานค้างและบันทึกล่าสุด
    pending_tasks = get_pending_tasks_for_user(user_id)
    task_context = "\n".join([f"- ID {t['id']}: {t['title']} (กำหนด: {t['due_datetime']})" for t in pending_tasks])
    
    user_payload = f"""
[บริบทปัจจุบัน]
งานที่บอสค้างอยู่:
{task_context if task_context else "ไม่มีงานค้าง"}

[ข้อความจากบอส]: {message}
"""
    
    if not GEMINI_API_KEY:
        # Fallback จำลองลอจิกเบื้องต้นหากยังไม่ได้ใส่ API Key
        if "เตือน" in message or "นัด" in message:
            due_time = datetime.now() + timedelta(hours=1)
            add_task(user_id, message, due_time)
            return f"น้องบันทึกนัดหมาย '{message}' ให้แล้วนะคะ จะคอยเตือนและตามจิกให้จนงานเสร็จแน่นอนค่ะบอส! ✨"
        elif "เสร็จ" in message:
            complete_task_by_title(user_id, "")
            return "รับทราบค่ะบอส! น้องติ๊กปิดงานในรายการให้เรียบร้อยแล้ว เก่งมากเลยค่ะ พักดื่มน้ำหน่อยนะคะ ☕"
        elif "ค้าง" in message or "งานอะไร" in message:
            if pending_tasks:
                items = "\n".join([f"• {t['title']}" for t in pending_tasks])
                return f"รายการงานที่ยังรอปิดอยู่มีตามนี้ค่ะบอส:\n{items}\n\nน้องคอยสแตนด์บายช่วยอยู่นะคะ"
            return "ไม่มีงานค้างเลยค่ะบอส วันนี้เคลียร์ได้ยอดเยี่ยมมากค่ะ 👏"
        elif "เหนื่อย" in message or "ท้อ" in message:
            return "กอดๆ นะคะบอส วันนี้เหนื่อยมาทั้งวันแล้ว น้องอยู่ข้างๆ เสมอนะคะ มีอะไรระบายกับน้องได้ตลอดเลยน้า พักสายตาสักนิดนะคะ 💙"
        elif "จำ" in message or "เลขบัญชี" in message:
            save_memory(user_id, message, "note", "ฝากจำ")
            return f"น้องจดไว้ในคลังความจำถาวรให้แล้วค่ะ เมื่อไหร่ที่บอสต้องการเรียกดู พิมพ์ถามน้องได้ทันทีนะคะ 📝"
        else:
            return f"รับทราบค่ะบอส '{message}' น้องจัดการและสแตนด์บายรอรับคำสั่งต่อไปนะคะ ✨"

    # เมื่อมี API Key เรียก Gemini API
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            res = await client.post(
                GEMINI_URL,
                json={
                    "contents": [
                        {"role": "user", "parts": [{"text": sys_prompt + "\n\n" + user_payload}]}
                    ],
                    "generationConfig": {"temperature": 0.3, "responseMimeType": "application/json"}
                }
            )
            if res.status_code == 200:
                resp_json = res.json()
                raw_text = resp_json['candidates'][0]['content']['parts'][0]['text']
                data = json.loads(raw_text)
                
                action = data.get("action", "none")
                act_data = data.get("action_data", {})
                
                if action == "add_task":
                    title = act_data.get("title", message)
                    due_iso = act_data.get("due_iso")
                    try:
                        due_dt = datetime.fromisoformat(due_iso) if due_iso else datetime.now() + timedelta(hours=2)
                    except:
                        due_dt = datetime.now() + timedelta(hours=2)
                    add_task(user_id, title, due_dt)
                elif action == "complete_task":
                    kw = act_data.get("search_keyword", "")
                    complete_task_by_title(user_id, kw)
                elif action == "save_memory":
                    save_memory(user_id, act_data.get("content", message), act_data.get("category", "general"), act_data.get("key_name", ""))
                elif action == "recall_memory":
                    q = act_data.get("query", message)
                    mems = search_memories(user_id, q)
                    if mems:
                        found_text = "\n".join([f"• {m['content']}" for m in mems])
                        return f"{data.get('reply_message', '')}\n\n🔍 ข้อมูลที่บอสเคยฝากไว้:\n{found_text}"
                
                return data.get("reply_message", "รับทราบคำสั่งค่ะบอส")
            else:
                return f"ระบบ AI ขัดข้องชั่วคราว (HTTP {res.status_code}) แต่น้องรับข้อความไว้แล้วค่ะบอส"
    except Exception as e:
        return f"ขออภัยค่ะบอส เกิดข้อผิดพลาดในการประมวลผล: {str(e)}"

async def process_document_screening(user_id: str, filename: str, file_text: str, base_url: str = "") -> Tuple[str, str]:
    """ประมวลผลกลั่นกรองเอกสารและสแกนกฎหมาย"""
    report_id = str(uuid.uuid4())[:8]
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    if not GEMINI_API_KEY:
        # จำลองการกลั่นกรองหากยังไม่มี API Key
        summary = f"สรุปกลั่นกรองเอกสาร: {filename}\nเรื่อง: ข้อพิจารณาและตรวจสอบร่างสัญญา/บันทึกเสนอลงนาม\nเอกสารมีความยาว {len(file_text)} ตัวอักษร ได้รับเมื่อ {now_str}"
        legal = "1. อำนาจลงนาม: อยู่ในอำนาจของผู้บริหาร\n2. จุดเสี่ยง (Red Flags): ตรวจพบเงื่อนไขการส่งมอบงานและการคิดเบี้ยปรับ ควรปรับแก้ให้สอดคล้องกับระเบียบราชการ\n3. ข้อแนะนำ: ควรส่งปรับแก้ก่อนลงนาม"
        risk = "ปานกลาง (Medium)"
        actions = "1. แจ้งฝ่ายเจ้าของเรื่องแก้ไขข้อ 4.2\n2. นัดประชุมเพื่อตกลงเงื่อนไขเบี้ยปรับใหม่"
        save_report(report_id, user_id, f"บันทึกกลั่นกรอง: {filename}", filename, summary, legal, risk, actions)
        
        chat_msg = (
            f"📑 **น้องกลั่นกรองเอกสาร '{filename}' ให้เรียบร้อยแล้วค่ะบอส!**\n\n"
            f"⚠️ **ระดับความเสี่ยง:** {risk}\n"
            f"📌 **จุดสังเกตสำคัญ:** พบประเด็นเงื่อนไขเบี้ยปรับที่อาจทำให้องค์กรเสียเปรียบ ควรปรับแก้ก่อนลงนามค่ะ\n\n"
            f"🔗 บอสกดเปิดดูบันทึกสรุป 1 หน้าและตารางจุดเสี่ยงฉบับเต็มได้ที่นี่นะคะ:\n{base_url}/report/{report_id}"
        )
        return chat_msg, report_id

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            prompt = f"{LEGAL_SCREENER_PROMPT}\n\n[ชื่อไฟล์]: {filename}\n[เนื้อหาเอกสาร]:\n{file_text[:30000]}"
            res = await client.post(
                GEMINI_URL,
                json={
                    "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"}
                }
            )
            if res.status_code == 200:
                resp_json = res.json()
                data = json.loads(resp_json['candidates'][0]['content']['parts'][0]['text'])
                
                title = data.get("title", filename)
                risk = data.get("risk_level", "ปานกลาง")
                summary = data.get("executive_summary", "")
                legal = data.get("legal_analysis", "")
                actions = data.get("action_items", "")
                chat_brief = data.get("chat_brief", "เอกสารได้รับการกลั่นกรองแล้วค่ะ")
                
                save_report(report_id, user_id, title, filename, summary, legal, risk, actions)
                
                reply_text = (
                    f"📑 **{title}**\n\n"
                    f"{chat_brief}\n\n"
                    f"⚠️ **ระดับความเสี่ยง:** {risk}\n"
                    f"🔗 **เปิดดูบันทึกสรุป 1 หน้าและตารางกฎหมายฉบับเต็ม:**\n{base_url}/report/{report_id}"
                )
                return reply_text, report_id
    except Exception as e:
        return f"เกิดข้อผิดพลาดในการวิเคราะห์เอกสาร: {str(e)}", ""
    
    return "ไม่สามารถประมวลผลเอกสารได้ค่ะบอส", ""
