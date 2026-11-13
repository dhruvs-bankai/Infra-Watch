FROM python:3.11.18-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN apt-get update \
    && apt-get upgrade -y \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir --upgrade pip setuptools wheel \
    && pip install --no-cache-dir -r requirements.txt \
    && rm -rf /usr/local/lib/python3.11/ensurepip/_bundled/*.whl \
    && rm -f /usr/local/lib/python3.11/site-packages/pip/_vendor/bom.cdx.json

COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./alembic.ini

RUN mkdir -p /var/lib/infrawatch/file_sd \
    && chown -R 65534:65534 /app /var/lib/infrawatch

USER 65534:65534
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
