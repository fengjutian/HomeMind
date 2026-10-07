# HomeMind device runtime — container image.
#
# Runs the filesystem agent on a NAS or a home server. The runtime holds
# one device token and nothing else: it cannot read family data, and it
# can only touch paths inside the mounted root.
#
#   docker build -f docker/device-runtime.Dockerfile -t homemind-runtime .
#   docker run -d --name homemind-runtime \
#     -e HOMEMIND_SERVER=https://homemind.example \
#     -v /srv/family:/data/family:ro \
#     -v homemind-runtime:/home/runtime/.homemind-runtime \
#     homemind-runtime run --root /data/family
#
# Pair once (the code comes from the Devices page in the dashboard):
#
#   docker exec -it homemind-runtime \
#     homemind-device-runtime --data-dir /home/runtime/.homemind-runtime \
#       pair --server "$HOMEMIND_SERVER" --code CODE --root /data/family
#
# The root is mounted read-only by default. A runtime that can only read
# is still useful (scan, list, hash) and cannot damage a photo library
# through a bug or a mis-queued command. Remount read-write only once you
# have a concrete reason to.

FROM python:3.12-slim AS base

# The runtime is a long-lived client; it needs no build toolchain.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir . && \
    useradd --create-home --uid 10001 runtime

# The token lives here; a named volume keeps it out of the image and out
# of the container's writable layer.
ENV HOMEMIND_RUNTIME_DATA=/home/runtime/.homemind-runtime
RUN mkdir -p "$HOMEMIND_RUNTIME_DATA" && chown -R runtime:runtime /home/runtime

USER runtime
VOLUME ["/home/runtime/.homemind-runtime"]

HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import pathlib,sys; sys.exit(0 if pathlib.Path('/home/runtime/.homemind-runtime/device.json').is_file() else 1)"

ENTRYPOINT ["homemind-device-runtime"]
CMD ["--help"]