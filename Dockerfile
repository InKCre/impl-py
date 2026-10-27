# syntax=docker/dockerfile:1

ARG PYTHON_VERSION=3.12
ARG SOURCE_REVISION=unknown

FROM python:${PYTHON_VERSION}-slim AS builder

ARG PDM_VERSION=2.28.0
ENV PDM_CHECK_UPDATE=false \
    PDM_IGNORE_SAVED_PYTHON=1 \
    PDM_VENV_IN_PROJECT=1
WORKDIR /app

# PDM 2.28 requires the httpx client replaced in unearth 0.19.
RUN pip install --no-cache-dir "pdm==${PDM_VERSION}" "unearth==0.18.3"

COPY pyproject.toml pdm.lock README.md ./
RUN pdm install --prod --no-editable --frozen-lockfile


FROM python:${PYTHON_VERSION}-slim AS runtime

ARG SOURCE_REVISION
LABEL org.opencontainers.image.revision="${SOURCE_REVISION}"
ENV INKCRE_ENV_FILE="" \
    INKCRE_SOURCE_REVISION="${SOURCE_REVISION}" \
    PATH="/app/.venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app

RUN apt-get update \
    && apt-get install --yes --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 inkcre \
    && useradd --uid 10001 --gid inkcre --create-home inkcre

COPY --from=builder --chown=inkcre:inkcre /app/.venv /app/.venv
COPY --chown=inkcre:inkcre app/ app/
COPY --chown=inkcre:inkcre libs/ libs/
COPY --chown=inkcre:inkcre migrations/ migrations/
COPY --chown=inkcre:inkcre scripts/ scripts/
COPY --chown=inkcre:inkcre utils/ utils/
COPY --chown=inkcre:inkcre \
    alembic.ini \
    pdm.lock \
    pyproject.toml \
    run.py \
    ./

USER inkcre

EXPOSE 8000
ENTRYPOINT []
CMD ["python", "scripts/container.py", "web"]


FROM runtime AS service

LABEL io.inkcre.database-schema.manifest="/app/database-contract/manifest.json" \
    io.inkcre.database-schema.path="/app/database-contract/database-schema.sql"
COPY --chown=inkcre:inkcre release/database-contract/ database-contract/


FROM service AS heroku-release

CMD ["python", "-c", "print('database lifecycle completed before Heroku release')"]


FROM service AS heroku-web


FROM service AS artifact
