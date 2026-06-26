FROM mcr.microsoft.com/playwright/python:v1.50.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN playwright install chromium

COPY . .

ENV PORT=8000

CMD ["sh", "-c", "uvicorn api:app --host 0.0.0.0 --port $PORT"]
