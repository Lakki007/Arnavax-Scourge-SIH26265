# Scourge demo image: portal + verifier + synthetic banks behind nginx (the app itself binds loopback only).
# Build: docker build -t scourge .   Run: docker run -p 8000:8000 -e SCOURGE_ACCESS_CODE=<judges code> scourge
FROM python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends nginx curl && rm -rf /var/lib/apt/lists/*
WORKDIR /srv/scourge
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN rm -rf var && chmod +x scripts/*.sh bin/* && cp deploy/nginx.container.conf /etc/nginx/conf.d/default.conf && cp deploy/nginx.internal.conf /etc/nginx/conf.d/internal.conf && rm -f /etc/nginx/sites-enabled/default
ENV SCOURGE_COOKIE_SECURE=0 PORT=8000
EXPOSE 8000
HEALTHCHECK CMD curl -sf http://127.0.0.1:8080/api/health || exit 1
CMD ["sh", "-c", "sed -i \"s/listen 8000/listen ${PORT}/\" /etc/nginx/conf.d/default.conf && nginx && exec ./scripts/run.sh"]
