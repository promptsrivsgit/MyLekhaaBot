# ลำดับโมเดลในระบบ Waterfall ป้องกัน Error 404 และ 503 คิวเต็ม
GEMINI_MODELS = [
    "gemini-2.0-flash",
    "gemini-2.5-flash",
    "gemini-1.5-flash-latest",
    "gemini-1.5-flash-002",
    "gemini-1.5-pro"
]

TYPHOON_MODELS = [
    "typhoon-2.5-30b-instruct",
    "typhoon-2-1-gemma3-12b"
]

# ฟังก์ชันสอบถามประเด็นเจาะลึกจากเนื้อหาเอกสาร (Document Q&A)
async def answer_question_from_document(user_id: str, question: str, target_file_id: Optional[str] = None) -> str:
    doc = get_vault_file(target_file_id) if target_file_id else get_user_last_file(user_id)
    if not doc:
        files = list_vault_files(user_id, limit=1)
        if files:
            doc = get_vault_file(files[0]["id"])
            
    if not doc or not doc.get("file_text"):
        return "น้องยังไม่พบเอกสารที่บอสต้องการสอบถามค่ะ ส่งไฟล์เข้ามาใหม่หรือพิมพ์ /files ได้นะคะ"

    filename = doc["filename"]
    content = doc["file_text"][:35000]

    qa_prompt = f"""
คุณคือ 'เลขาคู่ใจและที่ปรึกษากฎหมายประจำตัวของผู้บริหาร'
หน้าที่ของคุณคือตอบคำถามของบอส โดยอ้างอิงจากเนื้อหาจริงของเอกสารฉบับนี้เท่านั้น:
[ชื่อเอกสาร]: {filename}
[เนื้อหาเอกสาร]:
{content}

[คำถามจากบอส]:
{question}
ตอบอย่างสุภาพ กระชับ ชัดเจน ระบุข้อความ/ข้อกำหนดที่เกี่ยวข้อง ห้ามคาดเดานอกเหนือจากเอกสาร
"""
    # วนลูปเรียก AI Waterfall (Gemini 2.0/2.5 -> Typhoon 2.5)
    answer = await call_gemini_waterfall_text(qa_prompt)
    if not answer:
        answer = await call_typhoon_waterfall([{"role": "user", "content": qa_prompt}], response_format_json=False)

    return f"📖 *ประเด็นจากเอกสาร '{filename}':*\n\n{answer}" if answer else "ระบบประมวลผลขัดข้องชั่วคราว ลองถามใหม่อีกครั้งนะคะ"
