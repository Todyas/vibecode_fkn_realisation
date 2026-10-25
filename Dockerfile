FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data \
    PORT=8000

WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt && playwright install --with-deps chromium

COPY backend backend
COPY frontend/dist frontend/dist
COPY frontend/src/assets frontend/src/assets

WORKDIR /app/backend
EXPOSE 8000
CMD ["python", "-m", "app"]
