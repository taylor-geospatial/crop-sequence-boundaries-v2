# syntax=docker/dockerfile:1.9

# Build tippecanoe from source against a pinned debian base, then copy the
# binary into a slim python runtime. Keeps the runtime image free of compilers.

ARG TIPPECANOE_VERSION=2.79.0

# ---- builder: compile tippecanoe ----------------------------------------
FROM debian:bookworm-slim@sha256:5a2a80d11944804c01b8619bc967e31801ec39bf3257ab80b91070eb23625644 AS tippecanoe-builder

ARG TIPPECANOE_VERSION

# Build deps only; nothing here ships in the final image.
RUN apt-get update \
  && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    git \
    libsqlite3-dev \
    zlib1g-dev \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /src

# Pin to felt/tippecanoe v2.79.0 release tag.
RUN git clone --depth 1 --branch "v${TIPPECANOE_VERSION}" \
    https://github.com/felt/tippecanoe.git . \
  && make -j"$(nproc)" \
  && make install PREFIX=/opt/tippecanoe

# ---- runtime: python + csb ----------------------------------------------
FROM python:3.14-slim-bookworm@sha256:cba2eed20b946f0fcf51f2e736f00b71921884b0704b4301febf8d01032b1792 AS runtime

# Minimal GDAL/PROJ runtime libs for rasterio/pyogrio/pyproj wheels. The
# wheels bundle their own GDAL, but libsqlite3 + libexpat are needed by the
# copied tippecanoe binary, and ca-certificates is needed for HTTPS downloads.
RUN apt-get update \
  && apt-get install -y --no-install-recommends \
    ca-certificates \
    libsqlite3-0 \
    libexpat1 \
  && rm -rf /var/lib/apt/lists/* \
  && apt-get clean

# Copy tippecanoe binaries from the builder stage.
COPY --from=tippecanoe-builder /opt/tippecanoe/bin/ /usr/local/bin/

# Install uv from its official distroless image (pinned by digest upstream
# via the tag; uv self-verifies).
COPY --from=ghcr.io/astral-sh/uv:0.9.3 /uv /uvx /usr/local/bin/

ENV UV_LINK_MODE=copy \
  UV_COMPILE_BYTECODE=1 \
  UV_PYTHON_DOWNLOADS=never \
  PYTHONDONTWRITEBYTECODE=1 \
  PYTHONUNBUFFERED=1

WORKDIR /app

# Copy only what's needed to build/install the wheel.
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src/ ./src/

# Install the project as a wheel into a system-level venv. --no-dev keeps
# pre-commit/pytest out of the runtime image.
RUN uv sync --frozen --no-dev \
  && uv cache clean

ENV PATH="/app/.venv/bin:${PATH}"

# Drop privileges.
RUN useradd --system --create-home --uid 1001 csb
USER csb

# Smoke-test the CLI on container start; healthcheck reuses it.
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD csb --help >/dev/null 2>&1 || exit 1

ENTRYPOINT ["csb"]
CMD ["--help"]
