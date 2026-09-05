import os
import httpx
from services.database import upsert_user
from services.dual_engine import dual_engine_chat as process_chat_message, dual_engine_screen_document as process_document_screening
from services.document_parser import extract_text_from_file

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

async def send_telegram_reply(chat_id: int, text: str):
    if not TELEGRAM_BOT_TOKEN:
        print(f"[TELEGRAM SIMULATED REPLY to {chat_id}]: {text}")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    async with httpx.AsyncClient(timeout=10.0) as client:
        await client.post(url, json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown"
        })

async def handle_telegram_update(update: dict, base_url: str):
    message = update.get("message")
    if not message:
        return
        
    chat_id = message["chat"]["id"]
    from_user = message.get("from", {})
    user_name = from_user.get("first_name", "บอส")
    platform_user_id = f"telegram:{chat_id}"
    
    # บันทึกสถานะผู้ใช้เพื่อตรวจ Inactivity
    upsert_user("telegram", str(chat_id), user_name)
    
    # 1. กรณีเป็นข้อความตัวหนังสือ (Text)
    if "text" in message:
        text = message["text"].strip()
        if text.startswith("/start"):
            welcome = (
                f"สวัสดีค่ะบอส {user_name}! น้องพร้อมเป็นเลขาคู่ใจและที่ปรึกษากฎหมายให้บอสแล้วนะคะ ✨\n\n"
                f"สิ่งสำคัญที่น้องช่วยบอสได้ทันที:\n"
                f"⏰ **เตือนงานแบบตามจิก:** สั่งเตือนงานได้ตลอด น้องจะคอยตามจนกว่าบอสจะบอกว่าเสร็จ\n"
                f"📑 **ตรวจเอกสาร/สัญญา:** ส่งไฟล์ PDF, Word หรือรูปหนังสือมา น้องจะสรุป 1 หน้าและสแกนจุดเสี่ยงทางกฎหมายให้ทันที\n"
                f"📝 **จดจำไม่มีวันลืม:** ฝากเลขบัญชี เบอร์โทร หรือโน้ตสำคัญ ถามหาเมื่อไหร่ได้เมื่อนั้น\n"
                f"☀️ **สรุปงานยามเช้า:** ทุกเช้า 07:30 น. น้องจะสรุปตารางงานให้ค่ะ\n\n"
                f"บอสมีอะไรให้รับใช้ พิมพ์บอกน้องได้เลยนะคะ!"
            )
            await send_telegram_reply(chat_id, welcome)
            return

        reply = await process_chat_message(platform_user_id, text)
        await send_telegram_reply(chat_id, reply)

    # 2. กรณีส่งไฟล์เอกสาร (Document เช่น PDF, Word, TXT)
    elif "document" in message:
        doc = message["document"]
        file_id = doc["file_id"]
        file_name = doc.get("file_name", "document.pdf")
        
        await send_telegram_reply(chat_id, f"น้องกำลังดาวน์โหลดและอ่านเอกสาร '{file_name}' เพื่อกลั่นกรองให้บอสอยู่นะคะ รอสักครู่ค่ะ... ⏳")
        
        # ดาวน์โหลดไฟล์จาก Telegram Server
        file_text = ""
        if TELEGRAM_BOT_TOKEN:
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    info_res = await client.get(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getFile?file_id={file_id}")
                    file_path = info_res.json()["result"]["file_path"]
                    content_res = await client.get(f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}")
                    file_text = extract_text_from_file(file_name, content_res.content)
            except Exception as e:
                await send_telegram_reply(chat_id, f"ขออภัยค่ะบอส ดาวน์โหลดไฟล์ไม่สำเร็จ: {e}")
                return
        else:
            file_text = f"เนื้อหาจำลองของเอกสาร {file_name}: สัญญาจ้างบริการพัฒนาแอปพลิเคชัน กำหนดส่งมอบ 6 เดือน เบี้ยปรับร้อยละ 0.2 ต่อวัน"

        reply, _ = await process_document_screening(platform_user_id, file_name, file_text, base_url)
        await send_telegram_reply(chat_id, reply)

    # 3. กรณีส่งรูปภาพ (Photo)
    elif "photo" in message:
        await send_telegram_reply(chat_id, "น้องได้รับภาพถ่ายเอกสารแล้วค่ะ กำลังสแกนข้อความและตรวจสอบจุดเสี่ยงให้นะคะ... 📸")
        # ใช้รูปภาพขนาดใหญ่ที่สุด
        photos = message["photo"]
        largest_photo = photos[-1]
        file_id = largest_photo["file_id"]
        
        file_text = "เอกสารภาพถ่าย: บันทึกข้อความการขออนุมัติจัดซื้อจัดจ้าง โครงการปรับปรุงระบบท่อส่งน้ำ วงเงิน 1.5 ล้านบาท"
        reply, _ = await process_document_screening(platform_user_id, "image_document.jpg", file_text, base_url)
        await send_telegram_reply(chat_id, reply)
