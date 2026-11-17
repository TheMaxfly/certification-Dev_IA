# Déploiement — cible production, étiquette `e5-monitorage`

| Champ | Valeur |
|---|---|
| Cible | `production` |
| Geste | déploiement |
| Étiquette | `e5-monitorage` (commit `9f538dfcb528`) |
| Début (UTC) | 2026-11-13 16:44:54 |
| Pile prête | 47 s |
| Durée | 60 s |
| Utilisateur du poste | `maxime` |
| Verdict | **santé verte** |

## Images

| Image | Empreinte | Statut |
|---|---|---|
| `api-modele:1.5.0` | `sha256:ff1e93cf39d2b1d957b1159f238d5b2c86c7d55996a545ac005507c724c98e22` | présente |
| `api-catalogue:0.7.0` | `sha256:d05c130833287ffea1fe44677750000a3e731309978c4acfbab967142262dc4e` | présente |
| `application-conseiller:0.9.1` | `sha256:9d68287f7dbb25c7f46831f534dc1e12349b4c585c0a246b9d255a8da9859e0f` | présente |

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
| prometheus | cibles up | toutes | 5/5 up | tenu |

## Appels facturés (compteurs de l'API, depuis son démarrage)

- avant : non relevé (pile arrêtée)
- après : 0 appels, 0.000000 $

## Notes

- Prometheus : 12 s d'attente d'un relevé postérieur au lever (borne 30 s, cibles de Prometheus)
