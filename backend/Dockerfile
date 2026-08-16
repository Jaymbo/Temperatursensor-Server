FROM python:3.11-slim

# Git und Docker-CLI installieren
RUN apt-get update && apt-get install -y git curl && rm -rf /var/lib/apt/lists/* && \
    git config --global --add safe.directory '*' && \
    curl -fsSL https://get.docker.com | sh

WORKDIR /project/backend

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["sh", "-c", "find /project/backend -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true; exec uvicorn main:app --host 0.0.0.0 --port 8000"]