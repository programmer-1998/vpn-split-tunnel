# Dockerfile for VPN Split Tunnel
# Multi-stage build for minimal runtime image

# Build stage
FROM python:3.11-slim-bookworm AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    meson \
    ninja-build \
    pkg-config \
    libgtk-4-dev \
    libadwaita-1-dev \
    python3-gi \
    python3-gi-cairo \
    gir1.2-gtk-4.0 \
    gir1.2-adw-1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml meson.build README.md LICENSE ./
COPY src/ ./src/
COPY resources/ ./resources/
COPY systemd/ ./systemd/
COPY nftables/ ./nftables/
COPY scripts/ ./scripts/

RUN pip install --no-cache-dir meson-python build && \
    python -m build --wheel --sdist

# Runtime stage
FROM python:3.11-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-gi \
    python3-gi-cairo \
    gir1.2-gtk-4.0 \
    gir1.2-adw-1 \
    libgtk-4-1 \
    libadwaita-1-0 \
    nftables \
    policykit-1 \
    iproute2 \
    systemd \
    dbus \
    && rm -rf /var/lib/apt/lists/*

# Copy wheel from builder
COPY --from=builder /app/dist/*.whl /tmp/

RUN pip install --no-cache-dir /tmp/*.whl && \
    rm /tmp/*.whl

# Create non-root user
RUN useradd -m -s /bin/bash vpnuser
USER vpnuser

# Set up environment
ENV XDG_RUNTIME_DIR=/run/user/1000
ENV DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus

ENTRYPOINT ["vpn-split-tunnel"]
CMD ["--help"]