# Bonus (optionnel, non bloquant) — bronze MS sur Databricks + Unity Catalog

« Même format, même paradigme, plateforme optionnelle. » `bronze_ms_databricks.py`
est un notebook Databricks qui porte le job d'ingestion **bronze MS** (cf.
`../src/lakehouse/bronze.py`) sur Databricks Free/Community avec **Unity
Catalog** : Delta, partition par `snapshot_date`, idempotence `replaceWhere`,
`mergeSchema`, assainissement des noms de colonnes — la même logique.

**Indépendance totale.** Le module 07 est complet sans ce livrable : le chemin
**local mode + conteneur** est la référence, entièrement vérifié. Si ce bonus
échoue (quotas, compte expiré), rien n'est perdu.

## Exécuter (si un espace Databricks est disponible)

1. Déposer le raw MS dans un **Volume** Unity Catalog, p. ex.
   `/Volumes/<catalog>/<schema>/raw/2026-07/manga_sanctuary_volumes.jsonl`.
2. Importer `bronze_ms_databricks.py` comme notebook, l'attacher à un cluster
   (runtime Delta standard).
3. Renseigner les widgets `catalog`, `schema`, `snapshot`, `volume_raw` et
   lancer. Le notebook confronte le compte au chiffre de contrôle (103 811 pour
   2026-07) et signale tout écart sans le corriger.

## Statut

Notebook prêt et versionné. **Non exécuté dans l'environnement de build**
(pas d'espace Databricks branché ici) — c'est le sens même d'un livrable bonus
séparé. Le récit certification : format et paradigme ouverts (Delta/médaillon),
la plateforme cloud est un détail d'exécution.
