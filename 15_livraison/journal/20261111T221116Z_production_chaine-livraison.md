# Retour arrière — cible production, étiquette `chaine-livraison`

| Champ | Valeur |
|---|---|
| Cible | `production` |
| Geste | retour arrière |
| Étiquette | `chaine-livraison` (commit `12cc935c8b54`) |
| Début (UTC) | 2026-11-11 22:11:16 |
| Durée | 42 s |
| Utilisateur du poste | `maxime` |
| Verdict | **santé rouge** |
| Motif | répétition du retour arrière, E4 lot 8 |
| Quitté | arbre à `e4-livraison-1` ; servies : api api-modele:1.5.0, application application-conseiller:0.9.1, catalogue api-catalogue:0.7.0, embedding ghcr.io/huggingface/text-embeddings-inference:86-1.9.4, grafana grafana/grafana:13.3.0, node-exporter prom/node-exporter:v1.12.1, prometheus prom/prometheus:v3.15.1, sonde monitorage-sonde:0.1.0 |

## Images

| Image | Empreinte | Statut |
|---|---|---|
| `api-modele:1.4.0` | `sha256:92cd19daabdd16ba34fc76a0e3f51c4e771252fb15ec824d6b4703499f586f0d` | présente |
| `application-conseiller:0.1.0` | `sha256:1bc10d4653866e70795dc49e00e9a5e25c1557e72b9c92218e988931197777bd` | présente |

## Migrations de la base

- avant : 30 appliquées, 0 en attente (apimanga)
- après : 30 appliquées, 0 en attente (apimanga)

## Santé

| Service | Contrôle | Attendu | Constaté | Verdict |
|---|---|---|---|---|
| api | image servie | api-modele:1.4.0 | api-modele:1.4.0 | tenu |
| application | image servie | application-conseiller:0.1.0 | application-conseiller:0.1.0 | tenu |
| application | /sante | 200 | 200 | tenu |
| application | version | 0.1.0 | 0.1.0 | tenu |
| application | api | ok | ok | tenu |
| api | /v1/sante | ok | ok | tenu |
| api | /v1/info | 200 | 200 | tenu |
| api | version_api | 1.4.0 | 1.4.0 | tenu |
| api | encodage servi = encodage en service | 3 | 3 | tenu |
| base | migrations en attente (apimanga) | 0 | 0 | tenu |
| prometheus | cibles up | toutes | 3/5 up (api:down, application:down) | **manque** |

## Appels facturés (compteurs de l'API, depuis son démarrage)

- avant : 2 appels, 0.003769 $
- après : 2 appels, 0.003769 $

## Notes

- Les migrations de la base ne sont pas revenues (30 appliquées, 0 en attente (apimanga)) ; correction en avant, ou restauration d'une sauvegarde sur décision humaine.
- La pile n'est pas arrêtée : proposer un retour arrière à Max (livrer retour), jamais sans lui.
