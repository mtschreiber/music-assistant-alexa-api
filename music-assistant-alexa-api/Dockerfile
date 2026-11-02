# https://developers.home-assistant.io/docs/add-ons/configuration#add-on-dockerfile
# The application layer ships prebuilt: the skill image contains the Python
# 3.12 environment (with the verifier/certvalidator patches applied), ASK CLI,
# the skill code and the assets. This layer adds the add-on runtime glue.
# The tag+digest here and in build.yaml must stay identical — see AGENTS.md.
ARG BUILD_FROM=ghcr.io/timlaing/music-assistant-skill:v1.6.10@sha256:39cf27f294051f9369bb920f7f157da679fdd85d02d1bfedd411df8316ab3794
FROM $BUILD_FROM

# Serve with gunicorn instead of the skill image's Flask dev server, using the
# Home Assistant ingress log format from gunicorn.conf.py.
RUN /app/venv/bin/pip install --no-cache-dir gunicorn==26.2.0

# Add-on service definition. The skill image is Ubuntu-based and has no Home
# Assistant base s6-overlay/bashio, so the service is a plain entrypoint
# replacing the image CMD.
COPY gunicorn.conf.py /app/gunicorn.conf.py

# Fork patch: per-Echo Spotify account routing. The skill image carries the
# skill package twice (/app/src is what gunicorn serves, /app/app is a copy),
# so overlay the patched modules onto every copy, and fail the build when the
# package is not where it is expected.
COPY skill_patches/ /tmp/skill_patches/
RUN set -eu; \
    test -f /app/src/skill/voice_music.py; \
    for dir in /app/src/skill /app/app/skill; do \
        if [ -d "$dir" ]; then \
            cp /tmp/skill_patches/*.py "$dir"/; \
            /app/venv/bin/python -m py_compile "$dir"/account_routing.py "$dir"/voice_music.py; \
            echo "skill patches applied to $dir"; \
        fi; \
    done; \
    rm -rf /tmp/skill_patches
COPY rootfs /

ENV LOCALE="en-US" \
    MA_HOSTNAME="" \
    PORT="5000" \
    QUIET_HTTP="1" \
    SKILL_HOSTNAME="" \
    SKIP_URL_VALIDATION="false" \
    ENABLE_APL="false" \
    PYTHONFAULTHANDLER="1"

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl --fail --silent --output /dev/null "http://localhost:5000/health"
