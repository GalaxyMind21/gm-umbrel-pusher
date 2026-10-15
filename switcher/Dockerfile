# Separate, minimal switcher image (the pusher stays isolated). Distroless, non-root, pinned base.
FROM gcr.io/distroless/python3-debian12:nonroot@sha256:7d1042ce588ab97019fe95c24ffca7bc5a82ccdac572511d5e09bda4435c89c5
COPY --chown=0:0 --chmod=0555 switcher.py ed25519.py /app/
USER 65532:65532
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ENTRYPOINT ["/usr/bin/python3", "/app/switcher.py"]
