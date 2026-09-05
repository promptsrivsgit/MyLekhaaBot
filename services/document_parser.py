import io
import pypdf
import docx

def extract_text_from_file(filename: str, content_bytes: bytes) -> str:
    """สกัดข้อความจากไฟล์ประเภทต่างๆ (PDF, DOCX, TXT)"""
    ext = filename.lower().split('.')[-1]
    
    if ext == 'pdf':
        try:
            reader = pypdf.PdfReader(io.BytesIO(content_bytes))
            text = ""
            for i, page in enumerate(reader.pages):
                page_text = page.extract_text() or ""
                text += f"\n--- หน้า {i+1} ---\n" + page_text
            return text.strip()
        except Exception as e:
            return f"Error reading PDF: {e}"
            
    elif ext in ['docx', 'doc']:
        try:
            doc = docx.Document(io.BytesIO(content_bytes))
            text = "\n".join([p.text for p in doc.paragraphs if p.text.strip()])
            return text.strip()
        except Exception as e:
            return f"Error reading DOCX: {e}"
            
    else: # txt or other text formats
        try:
            return content_bytes.decode('utf-8', errors='ignore')
        except Exception as e:
            return f"Error reading text file: {e}"
