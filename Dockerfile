# syntax=docker/dockerfile:1
#
# Grimoire distributable — a self-contained image that runs arbitrary LLM
# prompts through the grim adapter (the six-verb sandbox over
# mini-swe-agent). Model id + provider API key come from the environment at
# `docker run` time; nothing is baked in. See README "Run in a container".
#
# Base: Astral's uv image ships `uv` plus a managed CPython 3.12 on Debian
# bookworm-slim. uv is needed at *runtime*, not just build time: dispatched
# python scripts execute via `uv run --no-project` (exec/dispatch.py), so uv
# must remain on PATH inside the running container.
# Ref: https://docs.astral.sh/uv/guides/integration/docker/
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# Seed-library toolchain. Every seed is a python script (seeds/bodies.py),
# but several shell out to host tools: apply_patch -> `git apply` / `patch`,
# grep_tree -> `rg` (ripgrep), shell -> /bin/sh. Install them so the starter
# library works on first run instead of failing at dispatch time.
# --no-install-recommends keeps the layer lean; apt lists are deleted in the
# same layer so they never ship in the image.
# Ref: https://docs.docker.com/build/building/best-practices/#apt-get
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ripgrep patch ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy dependency manifests first so the slow dependency layer is cached and
# only rebuilds when the lockfile changes, not on every source edit.
# --frozen: install exactly what uv.lock pins, never re-lock inside the
# image. --no-dev: drop ruff/mypy/pytest (dev-only). --extra agent: pull in
# mini-swe-agent (the harness). --no-install-project: install third-party
# deps only for now; the project itself lands after the source copy below, so
# a code-only change doesn't invalidate this (expensive) layer.
# Ref: https://docs.astral.sh/uv/concepts/projects/sync/
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra agent --no-install-project

# Source, then install the project itself into the same venv.
COPY . .
RUN uv sync --frozen --no-dev --extra agent

# The script library lives at $GRIM_DB. /data is the documented volume mount
# point so a host can persist the accumulated library across runs; the
# default keeps a single-run, mount-free container working out of the box.
ENV GRIM_DB=/data/grimoire.db
RUN mkdir -p /data

# Put the venv bin on PATH so `grim` and `mini` resolve directly (the
# entrypoint calls them without a `uv run` prefix).
ENV PATH="/app/.venv/bin:${PATH}"

RUN chmod +x /app/docker-entrypoint.sh
ENTRYPOINT ["/app/docker-entrypoint.sh"]
