# 10 — mesures de la recherche

Mesure la qualité de la recherche sur le **jeu d'évaluation v2 gelé** (69
questions, onze familles), pour six configurations, au **grain entité**. Aucun
LLM, aucune analyse de la question, aucun filtre structuré.

| # | Configuration | Réglages (fixés d'avance, `config/mesures.toml`) |
|---|---|---|
| 1 | BGE-M3, par le sens | cosinus, vecteurs `bench.vecteurs_bge_m3`, question encodée par le service (module 09), sans préfixe |
| 2 | EmbeddingGemma, par le sens | idem, `bench.vecteurs_embeddinggemma`, préfixe de requête de sa configuration |
| 3 | Plein texte PostgreSQL | `french`, lexèmes de la question reliés par OU, `ts_rank_cd` |
| 4 | Fusion 3 + 1 | rang réciproque, k = 60, sur les 100 premières entités de chaque liste |
| 5 | Fusion 3 + 2 | idem |
| 6 | TF-IDF (scikit-learn) | minuscules, accents retirés, mots simples, `sublinear_tf`, `min_df = 2`, cosinus |

Tout réglage est lu dans `config/mesures.toml`. Aucun n'est changé après avoir vu
un résultat : un changement serait une nouvelle mesure. L'empreinte du fichier
accompagne chaque résultat.

## Le grain : l'entité

Une entité est la **série du catalogue** quand le fragment y est rattaché, sinon
l'**identifiant Kitsu** de son document. Le rattachement n'est pas écrit ici : il
est `SQL_RATTACHEMENT` de `05_.../src/evaluation/atteignabilite.py`, chargé tel
quel depuis son fichier. Score d'une entité = le meilleur de ses fragments ;
toutes les entités occupent un rang (38 028 au 2026-10-07), sans filtre sur le
catalogue ; classement exact sur les 66 290 fragments ; égalités départagées par
l'identifiant croissant (la série d'abord à nombre égal).

## Exécuter

```bash
export DATABASE_URL='postgresql://postgres@localhost:5432/apimanga'
export MLFLOW_DISABLE_AGENT_HINT=1
uv run python -m mesures_recherche.executer 3 --sortie resultats/mesure_3.json
```

Première action de chaque exécution : l'**empreinte du jeu** (`31712b0d…`),
recalculée sur les fichiers versionnés et lue dans `bench.eval_jeux` ; sinon,
arrêt. Tout est lu sur une session **en lecture seule** côté serveur. Les mesures
par le sens (1, 2, et les fusions 4, 5) demandent l'instance du service
correspondante (module 09), lancée seule.

Le plein texte est calculé **à la volée** : le `tsvector` de chaque fragment est
recalculé à chaque exécution, dans une seule requête pour toutes les questions
(84 s le 2026-10-07), sans table ni index.

## Enregistrer, rejouer, comparer

```bash
uv run python -m mesures_recherche.executer 1 --enregistrer --sortie resultats/mesure_1.json
uv run python -m mesures_recherche.executer 1 --rejeu-de resultats/mesure_1.json \
    --sortie resultats/rejeu_1.json            # à blanc ; code 1 si les métriques diffèrent
uv run python -m mesures_recherche.comparaisons resultats/mesure_{1,2,3,4,5,6}.json \
    --sortie resultats/comparaisons.json
```

`--enregistrer` crée un run MLflow (expérience `E2-mesures-recherche-jeu-v2`) et
écrit les mêmes valeurs dans `bench.eval_mesures`, `run_id` = identifiant du run.
Le run porte en paramètres tous les réglages, la version et l'empreinte du jeu,
l'empreinte de la configuration et celle du code, le commit, l'encodage et la
version du service ; en pièces, le tableau par question, la configuration et le
résultat complet. MLflow est stocké sous `mlflow/` (SQLite et pièces), hors dépôt :

```bash
MLFLOW_DISABLE_AGENT_HINT=1 uv run mlflow ui \
  --backend-store-uri "sqlite:///$PWD/mlflow/mlflow.db" --host 127.0.0.1 --port 5000
```

Les comparaisons déclarées d'avance (1–2, 4–1, 1–3, 6–3) : bootstrap apparié sur
les questions de rang, 10 000 tirages, graine fixe, intervalle à 1 − 0,05 / 4.

## Métriques

`hit_rate@5`, `hit_rate@10`, `mrr@10`, `ndcg@10` (gain = grade du jeu, 2 ou 1),
par portée (global, mode, famille) et par périmètre (toutes les questions de rang,
questions atteignables). Une question sans série attendue — refus (F7), reconnue
hors catalogue — n'a pas de métrique de rang : elle est comptée et nommée à part.

## Tests

```bash
uv run --extra dev pytest
```

Métriques sur des cas écrits à la main (rang 1, rang 11, aucune série trouvée,
deux grades, question sans attendu), grain entité et départage, fusion ; puis le
harnais de bout en bout sur une base PostgreSQL + pgvector **jetable** : arrêt sur
une empreinte fausse (fichiers ou base), plein texte, TF-IDF, sens et fusion (la
question encodée par une doublure), lecture seule, rejeu identique.
