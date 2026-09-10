# 掌柜管家 · 商户运营 LLM Agent
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY web/ ./web/
COPY config.yaml .

EXPOSE 8010
CMD ["python", "-m", "app.cli", "serve", "--host", "0.0.0.0", "--port", "8010"]
