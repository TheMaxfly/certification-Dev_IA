# Manga Sanctuary — snapshot 2025-12

Fichiers **immuables**. Pour rafraîchir la source, créer un dossier daté voisin ;
ne jamais réécrire celui-ci. Ce manifeste est la mémoire de la collecte : sans
lui, les JSONL ne sont que des fichiers dont personne ne peut prouver le
contenu ni la date.

Ce manifeste est **versionné** (exception ciblée du `.gitignore` du
module) alors que les JSONL qu'il décrit ne le sont pas : c'est le seul
moyen de confronter une copie locale du raw à une référence. Même choix
qu'au module 01.

Les items MS ne portent **aucun horodatage de collecte** (38 champs
pour `VolumeItem`, 12 pour `ReviewItem`, aucun temporel). La datation
d'un enregistrement ne peut donc venir que du dossier daté et de ce manifeste.

## Fichiers

| Fichier | Lignes | Octets | SHA-256 |
|---|---:|---:|---|
| `manga_sanctuary_reviews.jsonl` | 6 749 | 12 660 550 | `270f530e7bfc407592c873de8ce2eaca70f31163c7d4f9b21ef39081815ab997` |
| `manga_sanctuary_volumes.jsonl` | 89 188 | 255 726 571 | `8f33a9a987ae3c5062b35756f0022c37f54659ef59192f243823296a6a6f8344` |

## Collecte

**Aucun `run.json` n'existe pour ce snapshot.** Le dossier `data/runs/` n'en contient pas pour `2025-12` : ce snapshot est antérieur à la mise en place du lanceur `scripts/run_scrape.py`.

La date de collecte n'est donc **pas reconstituée ici** — elle n'est pas mesurable. Le seul repère est le nom du dossier (`2025-12`), qui désigne le mois de collecte.

Ce snapshot sert de **référence de comparaison** : c'est sur lui qu'a porté le canari de juillet 2026 (`canari/rapport.md`), qui l'a mesuré à 89 188 volumes et 13 211 séries — valeurs reproduites par ce manifeste.

## Vérifier l'intégrité

```bash
cd 04_scraping_manga_sanctuary/data/raw/2025-12
sha256sum -c <<'EOF'
270f530e7bfc407592c873de8ce2eaca70f31163c7d4f9b21ef39081815ab997  manga_sanctuary_reviews.jsonl
8f33a9a987ae3c5062b35756f0022c37f54659ef59192f243823296a6a6f8344  manga_sanctuary_volumes.jsonl
EOF
```
