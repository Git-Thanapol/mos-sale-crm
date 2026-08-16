FROM python:3.12-slim AS builder
WORKDIR /wheels
RUN apt-get update && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt requirements-dev.txt ./
RUN pip wheel --no-cache-dir -r requirements.txt -w /wheels/base \
    && pip wheel --no-cache-dir -r requirements-dev.txt -w /wheels/dev

FROM python:3.12-slim AS runtime
ARG INSTALL_DEV=false
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 1000 crm

WORKDIR /app
COPY --from=builder /wheels /wheels
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir --no-index --find-links=/wheels/base -r requirements.txt \
    && if [ "$INSTALL_DEV" = "true" ]; then pip install --no-cache-dir --no-index --find-links=/wheels/dev -r requirements-dev.txt; fi \
    && rm -rf /wheels

COPY . .
RUN chown -R crm:crm /app
USER crm

ENTRYPOINT ["docker/entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "-w", "4", "-k", "gthread", "--threads", "4", "-t", "60"]
