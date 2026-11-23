# Déploiement — cible production, étiquette `e5-incident`

| Champ | Valeur |
|---|---|
| Cible | `production` |
| Geste | déploiement |
| Étiquette | `e5-incident` (commit `611043e2facc`) |
| Début (UTC) | 2026-11-17 02:50:03 |
| Pile prête | 36 s |
| Durée | 47 s |
| Utilisateur du poste | `maxime` |
| Verdict | **santé verte** |

## Images

| Image | Empreinte | Statut |
|---|---|---|
| `api-modele:1.5.0` | `sha256:a18ee79f7d7cfc9a51e1020ef3c9905b968d24147a3c0c612edca308e2b9ce4b` | réécrite (ancienne empreinte sha256:ff1e93cf39d2b1d957b1159f238d5b2c86c7d55996a545ac005507c724c98e22) |
| `api-catalogue:0.7.2` | `sha256:d86a12bc1714f2ad4bb1d439b5c5c6298eaab437a9efec4fd2c3cb12fd4c2c4e` | construite |
| `application-conseiller:0.9.2` | `sha256:fe2433cb584c707a5aae4c3f7838bbb19dab5b460c73d84233279a162b2d2e9e` | construite |

## Migrations de la base

- avant : 30 appliquées, 0 en attente (apimanga)
- après : 30 appliquées, 0 en attente (apimanga)

## Santé

| Service | Contrôle | Attendu | Constaté | Verdict |
|---|---|---|---|---|
| api | image servie | api-modele:1.5.0 | api-modele:1.5.0 | tenu |
| catalogue | image servie | api-catalogue:0.7.2 | api-catalogue:0.7.2 | tenu |
| application | image servie | application-conseiller:0.9.2 | application-conseiller:0.9.2 | tenu |
| application | /sante | 200 | 200 | tenu |
| application | version | 0.9.2 | 0.9.2 | tenu |
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

- Prometheus : 10 s d'attente d'un relevé postérieur au lever (borne 30 s, cibles de Prometheus)
