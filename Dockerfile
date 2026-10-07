# ── Stage 1: build the Angular front ─────────────────────────────────────────
FROM node:22-alpine AS front-build

WORKDIR /app
COPY front/package.json front/package-lock.json ./
RUN npm ci
COPY front/ ./
RUN npm run build

# ── Target "front": static Angular app + nginx reverse proxy ─────────────────
FROM nginx:1.27-alpine AS front

# Template gets envsubst'd by the stock nginx entrypoint: set API_UPSTREAM /
# WEBHOOK_UPSTREAM (compose: api:8000 / k8s: myreviewer-api:8000).
COPY nginx.conf /etc/nginx/templates/default.conf.template
COPY --from=front-build /app/dist/myreviewer/browser /usr/share/nginx/html

EXPOSE 80

# ── Target "api": FastAPI REST API (:8000) ───────────────────────────────────
FROM python:3.12-slim AS api

WORKDIR /srv/app/back

COPY back/requirements.txt /srv/app/requirements.txt
RUN pip install --no-cache-dir -r /srv/app/requirements.txt

COPY back/ /srv/app/back/

ENV DATA_DIR=/data
RUN mkdir -p /data && chmod 1777 /data
VOLUME /data

EXPOSE 8000
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]

# ── Target "webhook": GitHub webhook receiver (:8001) ────────────────────────
FROM api AS webhook

EXPOSE 8001
CMD ["uvicorn", "github_app.app:app", "--host", "0.0.0.0", "--port", "8001"]
