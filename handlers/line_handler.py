import os
import httpx
from services.database import upsert_user
from services.dual_engine import dual_engine_chat as process_chat_message, dual_engine_screen_document as process_document_screening
from services.document_parser import extract_text_from_file

LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")

async def reply_line_message(reply_token: str, text: str):
    if not LINE_CHANNEL_ACCESS_TOKEN:
        print(f"[LINE SIMULATED REPLY]: {text}")
        return
    url = "https://api.line.me/v2/bot/message/reply"
    headers = {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        await client.post(url, headers=headers, json={
            "replyToken": reply_token,
            "messages": [{"type": "text", "text": text}]
        })

async def handle_line_events(events: list, base_url: str):
    for event in events:
        if event.get("type") != "message":
            continue
            
        reply_token = event.get("replyToken")
        source = event.get("source", {})
        user_id = source.get("userId", "unknown")
        platform_user_id = f"line:{user_id}"
        
        # บันทึกสถานะผู้ใช้
        upsert_user("line", user_id, "บอส")
        
        msg_obj = event.get("message", {})
        msg_type = msg_obj.get("type")
        
        if msg_type == "text":
            user_text = msg_obj.get("text", "").strip()
            reply = await process_chat_message(platform_user_id, user_text)
            await reply_line_message(reply_token, reply)
            
        elif msg_type == "file":
            file_name = msg_obj.get("fileName", "document.pdf")
            msg_id = msg_obj.get("id")
            
            # ดาวน์โหลดไฟล์จาก LINE Data Server
            file_text = ""
            if LINE_CHANNEL_ACCESS_TOKEN:
                try:
                    content_url = f"https://api-data.line.me/v2/bot/message/{msg_id}/content"
                    headers = {"Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"}
                    async with httpx.AsyncClient(timeout=30.0) as client:
                        res = await client.get(content_url, headers=headers)
                        file_text = extract_text_from_file(file_name, res.content)
                except Exception as e:
                    await reply_line_message(reply_token, f"เกิดข้อผิดพลาดในการโหลดไฟล์: {e}")
                    continue
            else:
                file_text = f"เนื้อหาเอกสารจำลอง: {file_name}"

            reply, _ = await process_document_screening(platform_user_id, file_name, file_text, base_url)
            await reply_line_message(reply_token, reply)
            
        elif msg_type == "image":
            reply, _ = await process_document_screening(platform_user_id, "line_photo.jpg", "ภาพถ่ายเอกสารราชการเสนอลงนาม", base_url)
            await reply_line_message(reply_token, reply)
