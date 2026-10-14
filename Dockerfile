FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb \
    xauth \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN apt-get update && playwright install-deps chromium && rm -rf /var/lib/apt/lists/*
RUN playwright install chromium
RUN patchright install chromium || true

COPY . .

EXPOSE 8080

CMD ["python3", "entrypoint.py"]
