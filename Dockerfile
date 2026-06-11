FROM node:20-bookworm-slim AS web-build

WORKDIR /app/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MING_SIM_DATA_DIR=/app/data \
    MING_SIM_JSON_LOGS=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -r requirements.txt

COPY content ./content
COPY .agno_skills ./.agno_skills
COPY ming_sim ./ming_sim
COPY web_app.py main.py launcher.py ./
COPY --from=web-build /app/web/dist ./web/dist

RUN useradd --system --uid 10001 --create-home ming \
    && mkdir -p /app/data \
    && chown -R ming:ming /app/data

USER ming
EXPOSE 8010
VOLUME ["/app/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8010/healthz || exit 1

CMD ["python", "-m", "uvicorn", "web_app:app", "--host", "0.0.0.0", "--port", "8010"]
