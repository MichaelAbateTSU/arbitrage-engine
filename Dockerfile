FROM node:24-alpine AS frontend
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci --no-fund --no-audit
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 FRONTEND_DIST=/srv/frontend/dist PYTHONPATH=/srv/backend
WORKDIR /srv/backend
ARG PIP_INDEX_URL=https://pypi.org/simple
COPY backend/requirements.lock ./
RUN pip install --no-cache-dir --require-hashes --index-url "$PIP_INDEX_URL" -r requirements.lock && useradd --uid 10001 --create-home arb
COPY backend/ ./
COPY scripts/ /srv/scripts/
COPY --from=frontend /build/dist /srv/frontend/dist
RUN chown -R arb:arb /srv
USER arb
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
