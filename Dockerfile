FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 TZ=Asia/Singapore DISABLE_AUTOUPDATER=1 CLAUDE_CONFIG_DIR=/app/data/claude-config
# curl, ca-certificates and git are for the Claude Code installer (reads the Sheng Siong flyer JPG)
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# UID matches the NAS user that owns ./data and the vault, so files stay editable by the owner
ARG UID=1000
RUN useradd -m -u ${UID} app && mkdir -p /app/data && chown -R app /app
USER app
RUN curl -fsSL https://claude.ai/install.sh | bash -s stable
ENV PATH=/home/app/.local/bin:$PATH
RUN claude --version
# code last: a code-only change rebuilds in seconds instead of reinstalling the Claude CLI
COPY smh ./smh
COPY tests ./tests

HEALTHCHECK --interval=2m --timeout=20s --start-period=2m CMD ["python", "-m", "smh", "health"]
CMD ["python", "-m", "smh", "serve"]
