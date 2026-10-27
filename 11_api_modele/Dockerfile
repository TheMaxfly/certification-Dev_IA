# L'image de l'API du modèle — construite depuis la RACINE du dépôt :
#
#   docker build -f 11_api_modele/Dockerfile -t api-modele:1.0.0 .
#
# (le Compose du module la construit ainsi). La racine, parce que l'API exécute aussi
# le code de trois autres modules, sans le recopier : le client du service d'embedding
# (09, dépendance locale), le classement du banc (10, chargé par son chemin), le
# rattachement (05, chargé par le 10) et la méthode des pseudonymes (05, chargée par
# son chemin), avec la liste des homonymes admis (`database/donnees/`). Seuls ces
# fichiers entrent dans le contexte (`Dockerfile.dockerignore`, liste blanche).
#
# Dépendances : celles du verrou (`uv sync --locked`, sans les extras de
# développement), par uv à version fixe. Images de base épinglées par empreinte.
# Utilisateur non root ; journal JSON sur la sortie d'erreur.

FROM ghcr.io/astral-sh/uv:0.9.17@sha256:5cb6b54d2bc3fe2eb9a8483db958a0b9eebf9edff68adedb369df8e7b98711a2 AS uv

FROM python:3.12-slim@sha256:c3d81d25b3154142b0b42eb1e61300024426268edeb5b5a26dd7ddf64d9daf28 AS construction
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /app
# Les dépendances d'abord (couche réutilisée tant que les verrous ne changent pas).
COPY 09_service_embedding/pyproject.toml 09_service_embedding/README.md 09_service_embedding/
COPY 09_service_embedding/src 09_service_embedding/src
COPY 11_api_modele/pyproject.toml 11_api_modele/uv.lock 11_api_modele/README.md 11_api_modele/
RUN cd 11_api_modele && uv sync --locked --no-install-project
# Puis le code, installé en mode modifiable : l'API et le 09 trouvent leur
# configuration à côté de leurs sources (`config/`), et le 10 par son chemin.
COPY 09_service_embedding/config 09_service_embedding/config
COPY 10_mesures_recherche/src/mesures_recherche 10_mesures_recherche/src/mesures_recherche
COPY 05_nettoyage_agregation_bdd/src/evaluation/atteignabilite.py 05_nettoyage_agregation_bdd/src/evaluation/
COPY 05_nettoyage_agregation_bdd/src/corpus/pseudonymes.py 05_nettoyage_agregation_bdd/src/corpus/
COPY database/donnees/corpus_homonymes_admis.csv database/donnees/
COPY 11_api_modele/config 11_api_modele/config
COPY 11_api_modele/src 11_api_modele/src
RUN cd 11_api_modele && uv sync --locked

FROM python:3.12-slim@sha256:c3d81d25b3154142b0b42eb1e61300024426268edeb5b5a26dd7ddf64d9daf28
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    API_MODELE_HOTE=0.0.0.0
COPY --from=construction /opt/venv /opt/venv
COPY --from=construction /app /app
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin api
USER 10001
WORKDIR /app/11_api_modele
EXPOSE 8010
# Sain = `/v1/sante` à 200 : base, service et encodage en service cohérents.
HEALTHCHECK --interval=5s --timeout=3s --start-period=120s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8010/v1/sante', timeout=2)"]
CMD ["python", "-m", "api_modele"]
