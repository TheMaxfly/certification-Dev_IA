# 10 — mesures de la recherche

Mesure la qualité de la recherche sur le **jeu d'évaluation v2 gelé** (69
questions, onze familles), au **grain entité**, en trois tours : six mesures au
premier (spec E2 jour 2), sept au second (spec E2 jour 3), quatre au troisième
(spec E2 jour 4) — chaque tour choisi après lecture des précédents, déclaré avant
d'être exécuté. Aucun LLM pour la recherche,
aucune analyse de la question, aucun filtre structuré.

| # | Configuration | Réglages (fixés d'avance, `config/mesures.toml`) |
|---|---|---|
| 1 | BGE-M3, par le sens | cosinus, vecteurs `bench.vecteurs_bge_m3`, question encodée par le service (module 09), sans préfixe |
| 2 | EmbeddingGemma, par le sens | idem, `bench.vecteurs_embeddinggemma`, préfixe de requête de sa configuration |
| 3 | Plein texte PostgreSQL | `french`, lexèmes de la question reliés par OU, `ts_rank_cd` |
| 4 | Fusion 3 + 1 | rang réciproque, k = 60, sur les 100 premières entités de chaque liste |
| 5 | Fusion 3 + 2 | idem |
| 6 | TF-IDF (scikit-learn) | minuscules, accents retirés, mots simples, `sublinear_tf`, `min_df = 2`, cosinus |

Second tour (`tour = 2`), deux réglages nouveaux : `perimetre_entites`
(`toutes`, ou `catalogue` : seules les séries du catalogue sont classées) et la
fusion avec le TF-IDF au lieu du plein texte PostgreSQL.

| # | Configuration | Entités | Constante de fusion |
|---|---|---|---:|
| 7 | BGE-M3, par le sens | catalogue | — |
| 8 | EmbeddingGemma, par le sens | catalogue | — |
| 9 | TF-IDF | catalogue | — |
| 10 | Fusion TF-IDF + EmbeddingGemma | toutes | 60 |
| 11 | Fusion TF-IDF + EmbeddingGemma | catalogue | 60 |
| 12 | Fusion TF-IDF + EmbeddingGemma | toutes | 10 |
| 13 | Fusion TF-IDF + EmbeddingGemma | toutes | 100 |

Troisième tour (`tour = 3`), EmbeddingGemma par le sens : représenter l'œuvre
entière (`representation`) plutôt que son meilleur fragment.

| # | Représentation | Entités |
|---|---|---|
| 14 | vecteur de série : moyenne des vecteurs de ses fragments, ramenée à la longueur 1 | toutes |
| 15 | vecteur de série | catalogue |
| 16 | trois meilleurs fragments : moyenne de leurs scores (moins de trois : ceux qu'elle a) | toutes |
| 17 | trois meilleurs fragments | catalogue |

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

Avec `perimetre_entites = "catalogue"` (second tour), les entités Kitsu sont
retirées du classement et l'ordre des séries conservé : aucun score ne change,
une série ne peut que monter. Pour une fusion, le périmètre vaut pour chaque
liste source (ses 100 premières entités du périmètre) et pour la liste fusionnée.

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

`--enregistrer` crée un run MLflow (expérience `E2-mesures-recherche-jeu-v2`,
étiquettes `mesure`, `tour`, `spec`) et écrit les mêmes valeurs dans
`bench.eval_mesures`, `run_id` = identifiant du run.
Le run porte en paramètres tous les réglages, la version et l'empreinte du jeu,
l'empreinte de la configuration et celle du code, le commit, l'encodage et la
version du service ; en pièces, le tableau par question, la configuration et le
résultat complet. MLflow est stocké sous `mlflow/` (SQLite et pièces), hors dépôt :

```bash
MLFLOW_DISABLE_AGENT_HINT=1 uv run mlflow ui \
  --backend-store-uri "sqlite:///$PWD/mlflow/mlflow.db" --host 127.0.0.1 --port 5000
```

Les comparaisons déclarées d'avance — 1–2, 4–1, 1–3, 6–3 au premier tour, 10–2 et
11–8 au second, 15–8 et 17–8 au troisième : bootstrap apparié sur les questions de
rang, 10 000 tirages, graine fixe, intervalle à 1 − 0,05 / m (m = 8 depuis le
troisième tour). Une entité à un seul fragment a le même score dans les trois
représentations (`diagnostic.ecart_un_fragment`, écart toléré déclaré dans
`[controles]`). Le filtre
sur le catalogue (7–1, 8–2, 9–6) se mesure, ne se teste pas :
`diagnostic.controle_filtre` vérifie seulement qu'aucune série n'a reculé.

## Diagnostiquer un classement

```bash
uv run python -m mesures_recherche.diagnostic 1 3 4 6 --sortie resultats/diagnostic.json
uv run python -m mesures_recherche.diagnostic 2 5 --lecture F3 --sortie resultats/diagnostic_2.json
```

Analyse exploratoire, **sans nouvelle mesure** : rien dans MLflow ni dans
`bench.eval_mesures`. Les classements sont recalculés à blanc par le harnais, puis
confrontés au run MLflow de la mesure (pièce `resultat.json`) : mêmes dix
premières entités et même rang de la première série attendue pour chaque
question, sinon arrêt. Pour chaque question de rang : le rang de la première série
attendue dans le classement complet, son rang parmi les seules séries du
catalogue, le nombre d'entités hors catalogue dans les dix premières, et si son
score est nul (son rang ne tient alors qu'au départage). Par portée : les
médianes, et la part des questions dont la première série attendue est dans les
50, puis les 100 premières. `--lecture F3` liste, pour une famille, les dix
premières entités avec leur titre, leur appartenance au catalogue et leur score.

## Études : quels réglages comptent

Des **études**, pas des mesures : elles montrent quels réglages comptent, sans
désigner de « meilleur réglage » (une grille essayée sur 59 questions trouve
toujours un gagnant, qui doit beaucoup au hasard). Étiquette `etude` dans MLflow,
rien dans `bench.eval_mesures`, aucun test de significativité. Réglages déclarés
dans `config/etudes.toml`.

### Sensibilité du TF-IDF

```bash
uv run python -m mesures_recherche.etudes tfidf --sortie resultats/etudes/tfidf.json --enregistrer
```

36 combinaisons — unité (mots simples ; mots simples et paires ; suites de 3 à 5
caractères dans les mots), `sublinear_tf`, `min_df` (1, 2, 5), `max_df` (1,0 ; 0,5) —,
classement au catalogue. Chaque combinaison est évaluée par le harnais des mesures,
dans un processus à part, **sous garde de mémoire** : sous 5 Go de mémoire vive
disponible, ou avec du swap écrit dix secondes d'affilée, elle est arrêtée, sautée
et nommée, et l'étude continue. La combinaison des mesures 6 et 9 sert de témoin :
elle doit redonner les métriques de la mesure 9. MLflow (expérience
`E2-etudes-recherche`) : un run parent pour la grille, un run enfant par
combinaison. Le résumé donne, par réglage et par valeur, la moyenne de
`hit_rate@10` et de `ndcg@10`, toutes les autres valeurs confondues, avec
l'étendue ; F4 et les coûts (temps, vocabulaire, mémoire) par unité. Aucun
classement des combinaisons.

### Les voisins : profondeur et mesure de distance

```bash
# l'instance EmbeddingGemma du service lancée seule (module 09)
uv run python -m mesures_recherche.etudes voisins --sortie resultats/etudes/voisins.json --enregistrer
```

Sur le classement de la mesure 8 (EmbeddingGemma, meilleur fragment, catalogue) :
`hit_rate` à 1, 3, 5, 10, 20 et 50 résultats, global et par mode ; puis les dix
premières entités de chaque question selon le produit scalaire (ce que calcule la
mesure), le cosinus et la distance euclidienne, comparées. Un seul run MLflow,
étiquette `etude`.

### Classification d'intention

```bash
# méthode 1 : l'instance EmbeddingGemma du service lancée seule (module 09)
uv run python -m mesures_recherche.etudes intention-voisins --sortie resultats/etudes/intention_voisins.json --enregistrer
# méthode 2 : l'instance arrêtée ; Ollama, le modèle sur la carte graphique
uv run python -m mesures_recherche.etudes intention-llm --sortie resultats/etudes/intention_llm.json --enregistrer
```

Le mode de chacune des 69 questions (proposition, reconnaissance, refus) prédit de
deux façons : par le vote de ses k plus proches voisines parmi les 68 autres
(vecteurs EmbeddingGemma, k = 1, 3, 5, 7), et par le LLM local
(`ministral-3:3b`, température 0, réponse contrainte aux trois valeurs par un
schéma JSON, invite `config/invite_intention.toml` qui définit les modes sans
aucun exemple tiré du jeu — un test le vérifie). Expérience MLflow
`E2-etude-intention`, un run par méthode et par k : exactitude, rappel par
classe, matrice de confusion en pièce, latence pour le LLM, appels tracés.
69 exemples dont 5 refus ne donnent qu'un ordre de grandeur.

## Réglages de la génération (démonstration)

```bash
# 1. l'instance EmbeddingGemma du service lancée seule (module 09) :
uv run python -m mesures_recherche.generation passages --sortie resultats/generation/passages.json
# 2. l'instance arrêtée ; Ollama, le modèle sur la carte graphique :
uv run python -m mesures_recherche.generation generer \
    --passages resultats/generation/passages.json \
    --sortie resultats/generation/generations.json --enregistrer
```

Effet de quelques réglages de la génération — **pas une mesure de qualité** : aucun
juge, aucune note, rien dans `bench.eval_mesures`. Onze questions (la première, par
identifiant, de chaque famille) ; leurs passages sont ceux de la mesure 2 (les
entités de tête et le meilleur fragment de chacune), confrontés au run MLflow de la
mesure, puis enregistrés. Quatre configurations (`config/generation.toml`) :
température 0 ou 0,7, 3, 5 ou 10 passages, 1 ou 3 répétitions ; 400 jetons au plus ;
graine = 20261007 + (répétition − 1). L'invite est versionnée à part
(`config/invite_generation.toml`).

Le modèle (`ministral-3:3b-instruct-2512-q4_K_M`, par Ollama) doit tenir **entier sur
la carte graphique** — sinon arrêt avant la série ; il demande Flash Attention dans
le service (sans elle, son encodeur d'images réserve 9 Gio de graphe). Chaque
requête garde le modèle chargé (`keep_alive`), une dernière le décharge ; une entrée
qui ne laisserait pas la place des 400 jetons de réponse arrête la série.

Avec `--enregistrer` : expérience MLflow `E2-reglages-generation`, un run par
configuration — paramètres, métriques (latence médiane et p95, jetons en entrée et
en sortie, part des répétitions identiques, part des séquences de 5 mots de la
réponse présentes dans les passages, réponses tronquées, mémoire vidéo), pièces
(réponses, passages, invite, réglages) ; chaque appel est une trace MLflow.

## Corpus, encodage en service, promotion (E3)

Depuis la migration 021, plusieurs corpus et plusieurs encodages coexistent. Une
mesure par le sens lit **un** encodage : celui que `--encodage N` désigne, sinon
celui en service (`bench.v_encodage_en_service`) s'il est du modèle de la mesure,
sinon le seul encodage terminé de ce modèle. Le corpus suit l'encodage. Chaque run
enregistré a sa ligne `bench.eval_runs` (corpus, encodage). Les 17 runs d'E2, mesurés
avant `021`, y ont été inscrits une fois, depuis leurs paramètres MLflow :
`uv run python -m mesures_recherche.historique --corpus v1` (un rejeu n'écrit rien ;
un run sans trace MLflow arrête tout, avant d'écrire).

```bash
# mesurer un encodage qui n'est pas en service, dans l'expérience de l'étape
uv run python -m mesures_recherche.executer 8 --encodage 3 --enregistrer \
    --experience E3-corpus-v2 --spec "E3 étape 1 — corpus v2" --sortie resultats/m8_v2.json
# le comparer à la mesure 8 : verdict de la règle commitée avant la mesure
uv run python -m mesures_recherche.promotion --run <run_id> --sortie resultats/comparaison.json
# inscrire la décision de Max (une ligne au journal) — ou `refuser` ;
# `--derogation` si la décision est contraire au verdict de la règle
uv run python -m mesures_recherche.journal_promotions promouvoir --encodage 3 \
    --run <run_id> --motif "…" --par Max
uv run python -m mesures_recherche.journal_promotions etat
```

- **La règle** (`config/promotion_corpus_v2.toml`) est écrite et commitée avant la
  mesure : global ≥ 31 **et** reconnaissance ≥ 20, comptés en questions.
- **La comparaison** ne rend aucun verdict si le run n'a pas sa ligne `eval_runs` sur
  le corpus de la règle, si l'encodage mesuré est **celui en service**, ou si la
  référence ne rend plus 31 / 21 / 10.
- **La décision** reste celle de Max. `promouvoir` et `refuser` refont la comparaison :
  une décision contraire au verdict exige `--derogation`, une décision conforme la
  refuse. Le run doit être celui de l'encodage décidé.
- **Le journal** `bench.promotions` est en ajout seul : la base contrôle chaque ligne
  (encodage terminé, précédent = encodage en service, pas déjà en service).

### Retour arrière — une commande, une ligne au journal

```bash
DATABASE_URL=… uv run python -m mesures_recherche.journal_promotions revenir \
    --motif "…" --par Max
```

- **Effet** : une ligne `retour_arriere` qui remet en service l'encodage que la
  décision en service a remplacé (son `encodage_precedent_id`), ou celui que
  `--vers N` désigne. La base refuse un encodage qui n'a jamais été servi.
- **Ce qui ne bouge pas** : aucun vecteur, aucun fragment, aucun encodage n'est
  effacé ; les deux encodages restent en base, et revenir encore est une ligne de
  plus.
- **Contrôle, aussitôt après** : `journal_promotions etat` (la vue désigne l'encodage
  remis), puis la mesure de contrôle sans `--encodage`, qui suit la vue :
  `executer 8 --rejeu-de <resultat.json du run de cet encodage> --sortie …` rend
  `identiques: true`.
- **Essayé sur base jetable** (copie d'`apimanga`, E3 étape 1, bloc H) : promouvoir,
  revenir, la vue et la mesure de contrôle suivent. Sur la base réelle, on ne joue
  pas d'aller-retour pour la démonstration.

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
question encodée par une doublure), lecture seule, rejeu identique. Second tour :
réglages déclarés (`tests/test_configuration.py`), périmètre « catalogue » (ordre
conservé, aucun recul, chaque liste source d'une fusion au périmètre), étiquettes
`tour` et `spec` du run ; diagnostic sur cas écrits à la main. Études : grille
déclarée, résumé sans classement, garde de mémoire (lecteurs simulés), témoin égal à
la mesure 9, MLflow parent et enfants ; profondeurs sur cas écrits à la main,
distances (même ordre pour des vecteurs de longueur 1, pas au-delà), voisins de
bout en bout ; intention : vote, rappel et matrice, sorties hors valeurs, invite
sans extrait du jeu, LLM contre une doublure. Troisième tour :
vecteur moyen ramené à la longueur 1, moyenne des trois meilleurs (ou de ceux qu'on
a), même score pour une entité à un seul fragment, de bout en bout sur la base
jetable. Génération :
questions retenues, meilleur fragment, part copiée, répétitions, invite, plan de 88
appels, client Ollama contre une doublure HTTP (`keep_alive`, options,
déchargement), runs et traces MLflow, passages sur la base jetable. E3 : lecture
d'un corpus et d'un encodage désignés, résolution sans deviner entre plusieurs
candidats ; inscription des runs d'E2 dans `eval_runs` ; règle de promotion, verdict
aux bornes, comparaison et ses garde-fous (dont l'encodage en service) ; journal
des promotions : promouvoir, refuser, dérogation exigée ou refusée, retour arrière
(la vue et la résolution de l'encodage suivent), retour vers un encodage jamais
servi refusé par la base.
