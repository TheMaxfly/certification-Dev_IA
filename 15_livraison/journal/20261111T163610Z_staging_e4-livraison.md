# Déploiement — cible staging, étiquette `e4-livraison`

| Champ | Valeur |
|---|---|
| Cible | `staging` |
| Geste | déploiement |
| Étiquette | `e4-livraison` (commit `884517232871`) |
| Début (UTC) | 2026-11-11 16:36:10 |
| Durée | 0 s |
| Utilisateur du poste | `maxime` |
| Verdict | **échec : compose.staging.up : la pile ne se lève pas** |

## Images

| Image | Empreinte | Statut |
|---|---|---|
| `api-modele:1.5.0` | `sha256:b10ffb4ef08a08b8b8f7e4941d61c39c112159e48556ad43c653b883f5f33ada` | réécrite (ancienne empreinte sha256:f253119ef394f3b12415c3ba3b20a4610ca841821dc2820f79b3e14ebb89bbf5) |
| `api-catalogue:0.7.0` | `sha256:9b585aee18e21c83c265f24d9a58a54648048d2c75a5ceab4a6cb4ae7a132c1b` | réécrite (ancienne empreinte sha256:21c40a852ddcbb25f93cd5746e156eac74ea1ae09b101adca040b16dded95c4b) |
| `application-conseiller:0.9.1` | `sha256:a20cb6c3fb3707c122f38dbff115c1eb3af06072d877774a06b314583f74e92f` | construite |

## Migrations de la base

- avant : 30 appliquées, 0 en attente (apimanga_preprod)
- après : 30 appliquées, 0 en attente (apimanga_preprod)

## Santé

| Service | Contrôle | Attendu | Constaté | Verdict |
|---|---|---|---|---|

## Appels facturés (compteurs de l'API, depuis son démarrage)

- avant : non relevé (pile arrêtée)
- après : non relevé (pile arrêtée)

## Notes

- La pile est laissée telle quelle : proposer un retour arrière à Max (livrer retour), jamais sans lui.
