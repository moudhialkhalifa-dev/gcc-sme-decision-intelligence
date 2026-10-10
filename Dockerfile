# ---- build the dashboard ----
FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- API + static files ----
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv/backend
COPY backend/requirements.txt ./
RUN pip install -r requirements.txt
COPY backend/app ./app
COPY configs /srv/configs
COPY data/raw/online_retail_II.csv.gz /srv/data/raw/
RUN gunzip -k /srv/data/raw/online_retail_II.csv.gz \
 && useradd --create-home app && mkdir -p /srv/var && chown -R app /srv/var
COPY --from=web /web/dist /srv/static
ENV STATIC_DIR=/srv/static DATA_DIR=/srv/var DEMO_CSV=/srv/data/raw/online_retail_II.csv DEMO_CONFIG=/srv/configs/online_retail_ii.json
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
