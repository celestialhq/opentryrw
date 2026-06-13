FROM node:24-alpine AS frontend-build

WORKDIR /frontend

COPY frontend/package*.json ./
RUN npm ci

COPY frontend ./
RUN npm run build

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:${PATH}"

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY . .
COPY --from=frontend-build /frontend/dist ./frontend/dist
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app/data \
    && chmod +x /app/docker-entrypoint.sh

EXPOSE 4174

USER appuser

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["uvicorn", "opentryrw.api:app", "--host", "0.0.0.0", "--port", "4174"]
