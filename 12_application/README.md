# 12 — Application du libraire

Une application web minimale, rendue par le serveur, qui consomme l'API du modèle
(`11_api_modele/`) par sa clé : le libraire pose une question, l'application appelle
`POST /v1/reponse` et affiche la réponse ou le refus, les séries citées, leurs métadonnées
et les passages cités. Ébauche, complétée à la fin de l'étape.

## Architecture

```text
navigateur → application (127.0.0.1:8020) → API du modèle (X-API-Key de l'application)
                                             → service d'embedding, base, fournisseur
```

L'application ne voit jamais la base, ni les clés des fournisseurs. Elle ne connaît l'API
que par HTTP et par son contrat : profils, bornes et modes sont lus dans `/v1/info`, et la
doublure de ses tests est validée contre `11_api_modele/contrat/openapi_v1.json`.

## Démarrer

```bash
cp env.exemple .env      # APPLICATION_CLE_API : le secret seul, sans son nom
uv sync --locked
uv run --env-file .env python -m application
```

Sans clé, l'application refuse de démarrer. Sans API, elle démarre et le dit à l'écran.

## Configuration

`config/reglages.toml` : chaque réglage et son motif, tous les textes des écrans. Lu et
validé au démarrage : un écart empêche de démarrer.

| Variable | Rôle |
|---|---|
| `APPLICATION_CLE_API` | la clé de l'application, obligatoire ; jamais dans un fichier suivi |
| `API_MODELE_ADRESSE` | l'adresse de l'API ; défaut `http://127.0.0.1:8010` |
| `APPLICATION_ECOUTE` | `hôte:port` ; défaut `127.0.0.1:8020` |
| `APPLICATION_METRIQUES_RESEAUX` | les réseaux admis à lire `/metrics` ; défaut le bouclage |

## Vérifier

```bash
make verify MODULE=12     # depuis la racine du dépôt
```
