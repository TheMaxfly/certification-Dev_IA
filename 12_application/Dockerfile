# L'image de l'application — construite depuis la RACINE du dépôt :
#
#   docker build -f 12_application/Dockerfile --target application -t application-conseiller:0.2.0 .
#
# La racine, à cause de la seule cible `doublure`, qui lit le contrat du module 11. Seuls
# les fichiers nommés entrent dans le contexte (`Dockerfile.dockerignore`, liste blanche) :
# ni `.env`, ni données. Dépendances : celles du verrou, par uv à version fixe. Images de
# base épinglées par empreinte ; python:3.12-slim relevée le 2026-11-03. Utilisateur non
# root ; journal JSON sur la sortie d'erreur.

FROM ghcr.io/astral-sh/uv:0.9.17@sha256:5cb6b54d2bc3fe2eb9a8483db958a0b9eebf9edff68adedb369df8e7b98711a2 AS uv

FROM python:3.12-slim@sha256:b18b261e1b4974b4f321451ad5251ded85abb22c2b8b262da527dda1d031ab30 AS construction
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /app/12_application
# Les dépendances d'abord (couche réutilisée tant que le verrou ne change pas).
COPY 12_application/pyproject.toml 12_application/uv.lock 12_application/README.md ./
RUN uv sync --locked --no-install-project
COPY 12_application/config config
COPY 12_application/src src
RUN uv sync --locked

# La doublure de l'API, pour l'essai d'intégration : jamais publiée, jamais en production.
FROM construction AS construction-doublure
RUN uv sync --locked --extra dev
COPY 11_api_modele/contrat/openapi_v1.json /app/11_api_modele/contrat/openapi_v1.json
COPY 12_application/tests/doublure_api.py tests/doublure_api.py

FROM python:3.12-slim@sha256:b18b261e1b4974b4f321451ad5251ded85abb22c2b8b262da527dda1d031ab30 AS doublure
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
COPY --from=construction-doublure /opt/venv /opt/venv
COPY --from=construction-doublure /app /app
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin doublure
USER 10001
WORKDIR /app/12_application/tests
EXPOSE 8010
CMD ["python", "-m", "uvicorn", "doublure_api:app", "--host", "0.0.0.0", "--port", "8010"]

FROM python:3.12-slim@sha256:b18b261e1b4974b4f321451ad5251ded85abb22c2b8b262da527dda1d031ab30 AS application
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APPLICATION_ECOUTE=0.0.0.0:8020
COPY --from=construction /opt/venv /opt/venv
COPY --from=construction /app /app
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin application
USER 10001
WORKDIR /app/12_application
EXPOSE 8020
# Sain = l'application écoute. Pas `/sante`, qui attend l'API : elle sert sans l'API, et le
# dit à l'écran.
HEALTHCHECK --interval=5s --timeout=3s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import socket; socket.create_connection(('127.0.0.1', 8020), 2).close()"]
CMD ["python", "-m", "application"]
