# One image, two targets:
#   agent    — what you deploy behind your gateway
#   harness  — the agent plus the stand-in gateway, for harness/docker-compose.yml
FROM python:3.14-slim AS agent
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY labagent ./labagent
RUN useradd --create-home --uid 10001 agent && mkdir -p /app/data && chown agent /app/data
USER agent
ENV LABAGENT_DB=/app/data/labagent.sqlite3 LABAGENT_PEERS=/app/peers.toml
# Only the public A2A port. The owner API stays on the container's loopback:
# reach it with `docker compose exec <service> python -m labagent …`.
EXPOSE 8080
HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz')"
CMD ["python", "-m", "labagent", "serve"]

FROM agent AS harness
COPY --chown=agent harness ./harness
