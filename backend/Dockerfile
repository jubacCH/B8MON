# ── Build stage: install dependencies ────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Runtime stage ────────────────────────────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

# iputils-ping for ICMP checks, snmp for SNMP monitoring, gosu for privilege drop
RUN apt-get update && apt-get install -y --no-install-recommends \
        iputils-ping snmp gosu libcap2-bin ca-certificates curl \
    && setcap cap_net_raw+ep /usr/bin/ping \
    && rm -rf /var/lib/apt/lists/*

# Ookla speedtest CLI. The pip package speedtest-cli is unmaintained since 2021
# and reports a fixed ping of 1800000 ms against some connections; it stays
# installed as a fallback for images built without network access to Ookla.
ARG TARGETARCH=amd64
ARG OOKLA_VERSION=1.2.0
RUN set -eux; \
    case "$TARGETARCH" in \
        amd64) ookla_arch=x86_64 ;; \
        arm64) ookla_arch=aarch64 ;; \
        arm) ookla_arch=armhf ;; \
        *) ookla_arch="" ;; \
    esac; \
    if [ -n "$ookla_arch" ]; then \
        curl -fsSL "https://install.speedtest.net/app/cli/ookla-speedtest-${OOKLA_VERSION}-linux-${ookla_arch}.tgz" \
            | tar -xz -C /usr/local/bin speedtest \
        && chmod +x /usr/local/bin/speedtest \
        && /usr/local/bin/speedtest --version >/dev/null; \
    else \
        echo "No Ookla build for $TARGETARCH — falling back to speedtest-cli"; \
    fi

COPY --from=builder /install /usr/local

# Non-root user
RUN groupadd -r nodeglow && useradd -r -g nodeglow -d /app -s /sbin/nologin nodeglow

COPY . .
RUN chown -R nodeglow:nodeglow /app

# Embed build version (set via --build-arg or defaults to "dev")
ARG GIT_COMMIT=dev
ARG BUILD_DATE=unknown
RUN echo "{\"commit\":\"${GIT_COMMIT}\",\"date\":\"${BUILD_DATE}\"}" > /app/.build-version

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
