# Minimal, distroless, non-root. Base pinned by digest (gcr.io/distroless/python3-debian12:nonroot).
FROM gcr.io/distroless/python3-debian12:nonroot@sha256:7d1042ce588ab97019fe95c24ffca7bc5a82ccdac572511d5e09bda4435c89c5
# --chmod on COPY also applied 0444 to the created /app dir (not traversable for UID 65532 -> EACCES). Copy the dir with 0555.
COPY --chown=0:0 --chmod=0555 app/ /app/
USER 65532:65532
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ENTRYPOINT ["/usr/bin/python3", "/app/pusher.py"]
