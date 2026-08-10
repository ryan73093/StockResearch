FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN groupadd --system quant && useradd --system --gid quant --create-home quant

COPY pyproject.toml README.md ./
COPY src ./src

ARG QUANT_EXTRAS=data,ml,infra
RUN python -m pip install --upgrade pip && \
    python -m pip install ".[${QUANT_EXTRAS}]"

RUN mkdir -p /app/artifacts /app/logs && chown -R quant:quant /app
USER quant

EXPOSE 5000 8000
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--threads", "4", "quant_platform.dashboard.wsgi:app"]
