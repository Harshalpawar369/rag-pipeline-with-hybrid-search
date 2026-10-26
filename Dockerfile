FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY db ./db
COPY scripts ./scripts
COPY static ./static
RUN useradd --create-home appuser && mkdir -p /app/data/blobs && chown -R appuser /app/data
USER appuser
EXPOSE 8000
CMD ["sh", "-c", "python -m scripts.migrate && uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers"]
