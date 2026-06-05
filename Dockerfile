FROM python:3.11-slim

WORKDIR /app

# ติดตั้ง dependency ระบบ (สำหรับ torch CPU)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# ติดตั้ง Python packages
COPY requirements-api.txt .
RUN pip install --no-cache-dir -r requirements-api.txt

# Copy source code
COPY app/ ./app/
COPY rbac_config.py .

# Model จะถูก mount หรือ download ตอน startup
# ไม่ embed ใน image เพราะ ~570MB

ENV QDRANT_HOST=qdrant
ENV QDRANT_PORT=6333
ENV COLLECTION_NAME=company_docs

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
