# ProcureAI API / dashboard image.
#
# One image, three roles (see docker-compose.yml):
#   api       -> uvicorn api.main:app
#   dashboard -> streamlit dashboard/app.py
#   init-db   -> one-shot DB init + policy seeding
#
# Heavy note: requirements.txt includes torch/transformers for the
# document_ai embedding pipeline, so this image is large (~3GB). That is
# the honest cost of shipping the real MiniLM embedding path; the
# retrieval tools degrade gracefully if weights can't download, but the
# image installs the dependencies regardless.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# tesseract binary for pytesseract (document_ai OCR path)
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY . .

EXPOSE 8000 8501

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
