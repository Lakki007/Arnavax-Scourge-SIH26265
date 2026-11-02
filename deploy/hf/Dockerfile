# Hosted demo (Hugging Face Spaces, Docker SDK). Builds the PUBLIC GitHub repo at a pinned ref, so the hosted
# demo is exactly the shipped code. Spaces run containers as uid 1000 and route to port 7860.
FROM python:3.13-slim
ARG REPO=https://github.com/Lakki007/Arnavax-Scourge-SIH26265
ARG REF=main
RUN apt-get update && apt-get install -y --no-install-recommends nginx curl git ca-certificates && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 --branch "$REF" "$REPO" /srv/scourge && rm -rf /srv/scourge/.git
WORKDIR /srv/scourge
RUN pip install --no-cache-dir -r requirements.txt \
 && rm -rf var && sed -i 's/\r$//' scripts/*.sh && chmod +x scripts/*.sh bin/* \
 && cp deploy/hf/nginx.conf /etc/nginx/nginx.conf \
 && useradd -m -u 1000 user 2>/dev/null || true \
 && mkdir -p /tmp/nginx && chown -R 1000:1000 /srv/scourge /tmp/nginx /var/lib/nginx /var/log/nginx
USER 1000
ENV SCOURGE_COOKIE_SECURE=1 HOME=/home/user
EXPOSE 7860
HEALTHCHECK CMD curl -sf http://127.0.0.1:8080/api/health || exit 1
CMD ["sh", "-c", "nginx && exec ./scripts/run.sh"]
