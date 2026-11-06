# L'image de la sonde et de l'exporteur — construite depuis la RACINE du dépôt, à cause du
# paquet du module 09, réutilisé par son chemin, sans copie de son code dans ce module :
#
#   docker build -f 13_monitorage/Dockerfile -t monitorage-sonde:0.1.0 .
#
# Seuls les fichiers nommés entrent dans le contexte (`Dockerfile.dockerignore`, liste
# blanche) : ni fichier d'environnement, ni données. Images de base épinglées par empreinte,
# les mêmes qu'au module 12. Utilisateur non root ; journal JSON sur la sortie d'erreur.

FROM ghcr.io/astral-sh/uv:0.9.17@sha256:5cb6b54d2bc3fe2eb9a8483db958a0b9eebf9edff68adedb369df8e7b98711a2 AS uv

FROM python:3.12-slim@sha256:b18b261e1b4974b4f321451ad5251ded85abb22c2b8b262da527dda1d031ab30 AS construction
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /app/13_monitorage
# Les dépendances d'abord (couche réutilisée tant que le verrou ne change pas).
COPY 13_monitorage/pyproject.toml 13_monitorage/uv.lock 13_monitorage/README.md ./
RUN uv sync --locked --no-install-project
COPY 13_monitorage/src src
RUN uv sync --locked
# Le module 09, à son chemin : son code et sa configuration, côte à côte.
COPY 09_service_embedding/src/service_embedding /app/09_service_embedding/src/service_embedding
COPY 09_service_embedding/config /app/09_service_embedding/config

FROM python:3.12-slim@sha256:b18b261e1b4974b4f321451ad5251ded85abb22c2b8b262da527dda1d031ab30 AS sonde
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SONDE_ECOUTE=0.0.0.0:9108
COPY --from=construction /opt/venv /opt/venv
COPY --from=construction /app /app
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin sonde
USER 10001
WORKDIR /app/13_monitorage
EXPOSE 9108
HEALTHCHECK --interval=5s --timeout=3s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import socket; socket.create_connection(('127.0.0.1', 9108), 2).close()"]
CMD ["python", "-m", "monitorage"]
