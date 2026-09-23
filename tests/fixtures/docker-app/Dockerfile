# syntax=docker/dockerfile:1
ARG PYTHON_VERSION=3.12
ARG VARIANT=slim

FROM python:${PYTHON_VERSION}-${VARIANT} AS base
WORKDIR /app

FROM base AS build
COPY --from=ghcr.io/astral-sh/uv:0.4.0 /uv /bin/uv
COPY requirements.txt .
RUN uv pip install --system -r requirements.txt

FROM --platform=linux/amd64 \
     nginx:1.27-alpine AS web
COPY --from=build /app /usr/share/nginx/html

FROM build
CMD ["python", "-m", "http.server"]
