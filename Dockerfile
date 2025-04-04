FROM python:3.11-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ python3-dev libpq-dev cmake rustc cargo libssl-dev libffi-dev zlib1g-dev libjpeg-dev git \
    && pip install --upgrade pip \
    && pip install numpy \
    && pip install pyarrow \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /install
ENV PYTHONPATH=/install
COPY requirements.txt .

ENV PYTHONDONTWRITEBYTECODE=1
RUN pip install --prefix=/install --no-warn-script-location --no-cache-dir -r requirements.txt

FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 libstdc++6 libssl3 libffi8 zlib1g libjpeg62-turbo \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    ARROW_LIB_HOME=/usr/local/lib

WORKDIR /app

COPY . .

RUN useradd -m -r myuser && chown -R myuser:myuser /app

RUN find /usr/local -type d -name '__pycache__' -exec rm -rf {} + \
    && find /usr/local -type f -name '*.pyc' -delete

USER myuser
EXPOSE 8000