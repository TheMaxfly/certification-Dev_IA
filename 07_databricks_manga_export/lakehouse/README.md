# Module 07 v2 — lakehouse Spark + Delta (contrôles qualité historisés)

Couche **complémentaire consultative** de la chaîne : elle historise le **raw
multi-snapshots** en médaillon **bronze → silver → gold** et produit des
**métriques qualité comparées entre snapshots**. Elle détecte TÔT et PAR SYSTÈME
les défauts de collecte que le projet a jadis détectés TARD et PAR ACCIDENT.

> **Hors chemin critique.** Le 07 historise et alerte ; il ne **bloque jamais**.
> Les planchers bloquants de la promotion restent au module 04. Aucune écriture
> PostgreSQL. Le raw est en **lecture seule** (archive) ; les tables Delta en
> sont une projection reconstructible.

## Les trois incidents rejoués (la pièce jury)

Détectés autrefois par accident, désormais par métrique de routine :

| Incident | Métrique gold | Signal |
|---|---|---|
| Référence 2025-12 tronquée | `volumetrie` / `remplissage_champs` | critiques **+63,8 %** ; `review_body` **47,2 % → 99,99 %** |
| Trou de crawl « Di » | `completude_par_prefixe` | `di` **disparues = 9** (Δ net −1, masqué par 9 nouvelles) |
| 59 volumes perdus par l'ELT | `recouvrement_snapshots` | **302 volumes / 22 critiques** non revus, volumétrie de référence par snapshot |

La **leçon de méthode** est dans `completude_par_prefixe` : le Δ **net** ment
(un préfixe recule pendant que le total monte) ; le vrai détecteur est
`disparues`.

## Versions figées (vérifiées, Étape 0)

Apache **Spark 3.5.9** ↔ **delta-spark 3.3.2** (matrice officielle Delta :
3.3.x ↔ Spark 3.5.x). Image `apache/spark:3.5.9`. Couple **testé en local mode
sur Java 8** (écriture/lecture Delta, `replaceWhere`, `mergeSchema`, time
travel) avant toute ligne du module. Spark 4.x écarté : hors matrice documentée
et exige Java 17 (absent du chemin de test local).

## Double chemin d'exécution (même code, deux lanceurs)

- **Tests / dev — Spark local mode** (pas de Docker) :
  ```bash
  uv sync --extra dev
  JAVA_HOME=/usr/lib/jvm/java-8-openjdk-amd64 uv run pytest -q
  # jobs à la main :
  uv run lakehouse ingest --source all --snapshot 2026-07
  uv run lakehouse silver && uv run lakehouse gold && uv run lakehouse rapport
  uv run lakehouse synthese     # lecteur hors Spark (DuckDB)
  uv run lakehouse verifier     # vérifications imposées (PASS/FAIL)
  ```
- **Exécution de référence — conteneur** (jars Delta bundlés, aucun réseau au
  runtime ; raw monté **en lecture seule**, non-root via `nss_wrapper`) :
  ```bash
  HOST_UID=$(id -u) HOST_GID=$(id -g) docker compose run --rm lakehouse pipeline
  HOST_UID=$(id -u) HOST_GID=$(id -g) docker compose run --rm lakehouse synthese
  ```

## Médaillon

- **bronze** — ingestion générique PARAMÉTRÉE par source (un seul job), table
  Delta partitionnée par `snapshot_date`, colonnes techniques. Idempotence par
  `replaceWhere` ; `mergeSchema` pour la colonne `volume_ean` apparue en 2026-07.
  Sources : `ms_volumes`, `ms_reviews` (2 snapshots), `kitsu_manga`,
  `kitsu_mappings` (enveloppes JSON:API déballées), `mi_sorties`, `wd_entities`.
- **silver** — MS d'abord (décision figée) : typage, EAN nettoyé + flag de
  validité (**même algorithme que B2**), dates FR → ISO, grain série par
  snapshot. Les autres sources restent bronze-only ce cycle (les métriques
  inter-snapshots naîtront à leur 2ᵉ snapshot).
- **gold** — 5 tables comparables entre snapshots, estampillées
  `computed_at` + `job_version` : `volumetrie`, `completude_par_prefixe`,
  `remplissage_champs`, `recouvrement_snapshots`, `qualite_ean`.

## La boucle C1 (fermée dans les deux sens)

Le module **écrit** le raw daté en Delta et **est lu** par le reste du projet :
le lecteur externe `synthese` (DuckDB `delta_scan`, **hors JVM, ~0,1 s**) sort le
tableau que la checklist du **cycle mensuel** consulte avant promotion — une ⚠️
peut motiver un **NO-GO humain**. C'est l'extraction depuis le système big data
du critère C1, fermée dans les deux sens.

## Emplacements

| Quoi | Où | Suivi git |
|---|---|---|
| Tables Delta | `07_.../data/lakehouse/` | non (reconstructible) |
| Rapports générés | `07_.../rapports/` | oui |
| Code, tests, Dockerfile, compose | `07_.../lakehouse/` | oui |

## Bonus (optionnel, non bloquant)

`bonus_databricks/` : le **même job bronze MS** porté sur Databricks
Free/Community avec Unity Catalog — « même format, même paradigme, plateforme
optionnelle ». Aucune dépendance du reste du module à ce livrable.
