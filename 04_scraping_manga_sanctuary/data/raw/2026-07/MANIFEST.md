# Manga Sanctuary — snapshot 2026-07

Fichiers **immuables**. Pour rafraîchir la source, créer un dossier daté voisin ;
ne jamais réécrire celui-ci. Ce manifeste est la mémoire de la collecte : sans
lui, les JSONL ne sont que des fichiers dont personne ne peut prouver le
contenu ni la date.

Ce manifeste est **versionné** (exception ciblée du `.gitignore` du
module) alors que les JSONL qu'il décrit ne le sont pas : c'est le seul
moyen de confronter une copie locale du raw à une référence. Même choix
qu'au module 01.

Les items MS ne portent **aucun horodatage de collecte** (39 champs
pour `VolumeItem`, 12 pour `ReviewItem`, aucun temporel). La datation
d'un enregistrement ne peut donc venir que du dossier daté et de ce manifeste.

## Fichiers

| Fichier | Lignes | Octets | SHA-256 |
|---|---:|---:|---|
| `manga_sanctuary_reviews.jsonl` | 11 052 | 31 143 173 | `f8cd721cf7b89be7570a75f32f22b61e0027e0029194d7fdb74d10062ea49ec1` |
| `manga_sanctuary_volumes.jsonl` | 103 811 | 315 442 817 | `6bab5c94cc1374ee659b2336d95c72061bf1ed0d48086ca5d8223ab306f2b04d` |

## Collecte

| | |
|---|---|
| Démarrée le | 2026-07-14T21:52:47.743653+00:00 |
| Terminée le | 2026-07-15T10:19:40.629351+00:00 |
| État | `promoted` |
| Items promus — volumes | 103 811 |
| Items promus — reviews | 11 052 |
| Doublons retirés — volumes | 0 |
| Doublons retirés — reviews | 0 |
| Durée du crawl | 44 804 s (~12 h 26) |
| Requêtes émises | 135 745 |
| Réponses HTTP 200 | 130 814 |
| Retries | 382 |
| Motif de fin | `finished` |

Trace complète dans `data/runs/2026-07-full/` : `run.json` (promotion) et
`status.json` (statistiques Scrapy intégrales — codes HTTP, retries, débit).
Ces deux fichiers restent **hors dépôt**, comme tout `data/` hormis ce
manifeste : les chiffres ci-dessus en sont le report.

## Vérifier l'intégrité

```bash
cd 04_scraping_manga_sanctuary/data/raw/2026-07
sha256sum -c <<'EOF'
f8cd721cf7b89be7570a75f32f22b61e0027e0029194d7fdb74d10062ea49ec1  manga_sanctuary_reviews.jsonl
6bab5c94cc1374ee659b2336d95c72061bf1ed0d48086ca5d8223ab306f2b04d  manga_sanctuary_volumes.jsonl
EOF
```
