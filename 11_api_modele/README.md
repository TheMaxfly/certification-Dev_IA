# 11 — API du modèle

> Ébauche (E3, étape 2, blocs 02 et 03). Le README complet vient avec la
> documentation de l'étape.

Une API REST qui expose le modèle d'embedding en service : une question entre, une
liste de séries classées sort, avec les passages qui les justifient. Elle reprend
exactement le classement mesuré par le banc (module 10, mesure 8 sur le corpus en
service) : ce qui est servi est ce qui a été mesuré.

Le module 02 (API de données) ne change pas : deux API distinctes, données et
inférence.

## Ce que l'API réutilise

| Module | Quoi | Comment |
|---|---|---|
| 09 | le client du service d'embedding (préfixes, refus de troncature, confrontation `/info` ↔ registre) | dépendance locale `uv`, modifiable |
| 10 | le classement : rattachement des fragments, score d'une série = son meilleur fragment, départage, limitation au catalogue | paquet chargé par son chemin (`api_modele.banc`) |
| 05 | le rattachement (`atteignabilite.py`) | chargé par le module 10, par son chemin |

Les versions dont le calcul dépend (numpy, pgvector, psycopg, typer) sont celles du
verrou du module 10 ; `tests/test_verrous.py` compare les deux verrous.

## Lancer

```bash
cp .env.example .env    # puis y poser les clés et le DSN (sans mot de passe)
uv run --env-file .env python -m api_modele
```

Au démarrage, l'API vérifie, et refuse de démarrer au moindre écart :

- la base répond sous `modele_api` ;
- un encodage est en service, terminé, sur un corpus clos, du modèle et de la
  dimension que sert l'instance ;
- `/info` du service est conforme à cet encodage (`confronter` du module 09) ;
- la borne de la question tient sous la longueur maximale du service ;
- le classement du banc se charge : rattachement des fragments et vecteurs de
  l'encodage en service seulement (77 006 en 17 s, pic de 1,5 à 1,8 Go, puis 0,5 Go
  résidents, mesurés le 2026-10-20).

Ce qui est servi est ce qui a été mesuré : `tests/test_egalite_banc.py` rejoue les
69 questions du jeu gelé et compare les dix premières séries au run de référence du
banc (étape d'équivalence de `make verify-poste`, sous `modele_api`).

## Routes

| Route | Clé | Rôle |
|---|---|---|
| `POST /v1/recherche` | oui | une question → les séries classées (`nombre_series` 10, de 1 à 50 ; `passages_par_serie` 1, de 1 à 5 ; `catalogue_seul` vrai), avec leurs passages et le contexte (modèle, révision, corpus, encodage, réglages, identifiant, durée) |
| `GET /v1/sante` | non | base, service, encodage en service : `ok` ou `degrade` (503) |
| `GET /v1/info` | oui | modèle, révision, encodage et corpus servis ; réglages et bornes |
| `GET /metrics` | non | Prometheus, aux seules adresses de `API_MODELE_METRIQUES_RESEAUX` (404 ailleurs) |
| `GET /v1/openapi.json` | non | le contrat, égal à `contrat/openapi_v1.json` (versionné) |

Erreurs : `{"code", "message", "details", "identifiant_requete"}`, un code stable par
cas ; l'identifiant aussi en en-tête `X-Identifiant-Requete`.

## Réglages

Un seul lecteur, `api_modele.configuration` : défauts, bornes et motif dans
`config/reglages.toml` (versionné) ; secrets et adresses dans `.env` (hors dépôt).

**Limite** : le débit (60 requêtes par minute et par clé, fenêtre glissante) est
compté en mémoire du processus. Il vaut pour **une** instance de l'API ; plusieurs
instances derrière un répartiteur auraient chacune leur compte.

## Vérifier

```bash
make verify MODULE=11   # depuis la racine du dépôt
```

Exigences du module neuf, bloquantes dans `make verify` : Pyright strict sur `src/`,
Bandit sur `src/`, pip-audit sur le verrou, couverture mesurée.
