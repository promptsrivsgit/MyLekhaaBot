import os
import httpx
import asyncio
from datetime import datetime, timedelta
from services.database import (
    get_due_tasks, update_task_chaser, get_all_users, get_pending_tasks_for_user
)

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")

async def send_push_message(platform: str, platform_user_id: str, text: str):
    """ส่งข้อความ Push Message ไปหาบอสผ่าน Telegram หรือ LINE (ฟรี 100% บน Telegram)"""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            if platform == "telegram" and TELEGRAM_BOT_TOKEN:
                url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                await client.post(url, json={
                    "chat_id": platform_user_id,
                    "text": text,
                    "parse_mode": "Markdown"
                })
            elif platform == "line" and LINE_CHANNEL_ACCESS_TOKEN:
                url = "https://api.line.me/v2/bot/message/push"
                headers = {"Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"}
                await client.post(url, headers=headers, json={
                    "to": platform_user_id,
                    "messages": [{"type": "text", "text": text}]
                })
            else:
                print(f"[SIMULATED PUSH to {platform}:{platform_user_id}]\n{text}\n")
    except Exception as e:
        print(f"Failed to send push message: {e}")

async def run_task_chaser_once():
    """ตรวจสอบงานที่ถึงกำหนดเตือนและส่งข้อความตามจิก"""
    due_tasks = get_due_tasks()
    now = datetime.now()
    
    for task in due_tasks:
        task_id = task["id"]
        user_id = task["user_id"]
        title = task["title"]
        count = task["reminder_count"] + 1
        
        if count == 1:
            msg = (
                f"⏰ **บอสคะ ได้เวลาเริ่มทำเรื่องนี้แล้วนะคะ!**\n\n"
                f"📌 **งาน:** {title}\n\n"
                f"ถ้าจัดการเสร็จแล้ว อย่าลืมพิมพ์บอกน้องว่า 'เสร็จแล้ว' ด้วยนะคะ น้องรออัปเดตอยู่ค่ะ ✨"
            )
            next_time = now + timedelta(minutes=15)
        elif count == 2:
            msg = (
                f"📢 **บอสคะ (ตามรอบที่ 2)**\n\n"
                f"เรื่อง '{title}' คืบหน้าถึงไหนแล้วเอ่ย? เรียบร้อยแล้วแจ้งน้องได้เลยน้า ⏳"
            )
            next_time = now + timedelta(minutes=25)
        elif count == 3:
            msg = (
                f"🚨 **บอสคะ! (จิกรอบที่ 3 แล้วนะคะ)**\n\n"
                f"'{title}' ยังไม่เสร็จใช่ไหมคะ น้องขออนุญาตเตือนอีกรอบน้า เดี๋ยวงานล้นโต๊ะนะคะ! 💪"
            )
            next_time = now + timedelta(minutes=45)
        else:
            msg = (
                f"⚠️ **แจ้งเตือนพิเศษ:** บอสคะ งาน '{title}' ค้างเกินกำหนดแล้วนะคะ หากต้องการเลื่อนเวลา พิมพ์บอกน้องได้เลยนะคะ"
            )
            next_time = now + timedelta(hours=2)
            
        platform = "telegram" if ":" not in user_id else user_id.split(":")[0]
        actual_id = user_id.split(":")[-1]
        
        await send_push_message(platform, actual_id, msg)
        update_task_chaser(task_id, next_time, count)

async def check_morning_briefing_once():
    """ตรวจเวลา 07:30 น. เพื่อส่งสรุปเช้า"""
    now = datetime.now()
    # หากเวลาอยู่ในช่วง 07:30 - 07:31
    if now.hour == 7 and now.minute == 30:
        users = get_all_users()
        for u in users:
            p_user_id = u["platform_user_id"]
            platform = u["platform"]
            pending = get_pending_tasks_for_user(f"{platform}:{p_user_id}")
            
            if pending:
                task_lines = "\n".join([f"• {t['title']} (กำหนด: {t['due_datetime'][:16]})" for t in pending])
                brief = (
                    f"☀️ **อรุณสวัสดิ์ค่ะบอส!**\n\n"
                    f"เช้านี้พร้อมลุยไหมคะ น้องรวบรวมรายการงานและนัดหมายที่ต้องปิดวันนี้มาให้แล้วค่ะ:\n\n"
                    f"{task_lines}\n\n"
                    f"ขอให้เป็นวันที่ราบรื่นนะคะ น้องคอยช่วยอยู่ตรงนี้เสมอค่ะ 💙"
                )
            else:
                brief = (
                    f"☀️ **อรุณสวัสดิ์ค่ะบอส!**\n\n"
                    f"วันนี้ตารางงานโล่ง ไม่มีงานค้างเลยค่ะ มีอะไรอยากให้น้องช่วยร่างหนังสือ หรือวางแผนงานใหม่ เรียกได้ตลอดเลยนะคะ ☕"
                )
            await send_push_message(platform, p_user_id, brief)

async def background_scheduler_loop():
    """ลูปเบื้องหลังทำงานทุก 60 วินาที โดยไม่ต้องพึ่งแพ็กเกจภายนอก (Zero External Dependency)"""
    print("[SCHEDULER] Background Chaser & Scheduler started.")
    while True:
        try:
            await run_task_chaser_once()
            await check_morning_briefing_once()
        except Exception as e:
            print(f"[SCHEDULER ERROR] {e}")
        await asyncio.sleep(60)

def start_background_scheduler():
    asyncio.create_task(background_scheduler_loop())
