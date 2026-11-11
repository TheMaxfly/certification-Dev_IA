# Déploiement — cible production, étiquette `e4-livraison-1`

| Champ | Valeur |
|---|---|
| Cible | `production` |
| Geste | déploiement |
| Étiquette | `e4-livraison-1` (commit `933ce0d94394`) |
| Début (UTC) | 2026-11-11 22:27:58 |
| Durée | 37 s |
| Utilisateur du poste | `maxime` |
| Verdict | **santé rouge** |

## Images

| Image | Empreinte | Statut |
|---|---|---|
| `api-modele:1.5.0` | `sha256:ff1e93cf39d2b1d957b1159f238d5b2c86c7d55996a545ac005507c724c98e22` | réécrite (ancienne empreinte sha256:b10ffb4ef08a08b8b8f7e4941d61c39c112159e48556ad43c653b883f5f33ada) |
| `api-catalogue:0.7.0` | `sha256:d05c130833287ffea1fe44677750000a3e731309978c4acfbab967142262dc4e` | réécrite (ancienne empreinte sha256:9b585aee18e21c83c265f24d9a58a54648048d2c75a5ceab4a6cb4ae7a132c1b) |
| `application-conseiller:0.9.1` | `sha256:9d68287f7dbb25c7f46831f534dc1e12349b4c585c0a246b9d255a8da9859e0f` | réécrite (ancienne empreinte sha256:a20cb6c3fb3707c122f38dbff115c1eb3af06072d877774a06b314583f74e92f) |

## Migrations de la base

- avant : 30 appliquées, 0 en attente (apimanga)
- après : 30 appliquées, 0 en attente (apimanga)

## Santé

| Service | Contrôle | Attendu | Constaté | Verdict |
|---|---|---|---|---|
| api | image servie | api-modele:1.5.0 | api-modele:1.5.0 | tenu |
| catalogue | image servie | api-catalogue:0.7.0 | api-catalogue:0.7.0 | tenu |
| application | image servie | application-conseiller:0.9.1 | application-conseiller:0.9.1 | tenu |
| application | /sante | 200 | 200 | tenu |
| application | version | 0.9.1 | 0.9.1 | tenu |
| application | api | ok | ok | tenu |
| application | base | ok | ok | tenu |
| application | cles_api | deux | deux | tenu |
| application | profil_actif | lu | defaut | tenu |
| application | catalogue | ok | ok | tenu |
| catalogue | /health | 200 | 200 | tenu |
| catalogue | db | ok | ok | tenu |
| catalogue | normalisation | fonction | fonction | tenu |
| api | /v1/sante | ok | ok | tenu |
| api | /v1/info | 200 | 200 | tenu |
| api | version_api | 1.5.0 | 1.5.0 | tenu |
| api | encodage servi = encodage en service | 3 | 3 | tenu |
| base | migrations en attente (apimanga) | 0 | 0 | tenu |
| prometheus | cibles up | toutes | 4/5 up (api:down) | **manque** |

## Appels facturés (compteurs de l'API, depuis son démarrage)

- avant : non relevé (pile arrêtée)
- après : 0 appels, 0.000000 $

## Notes

- La pile n'est pas arrêtée : proposer un retour arrière à Max (livrer retour), jamais sans lui.
