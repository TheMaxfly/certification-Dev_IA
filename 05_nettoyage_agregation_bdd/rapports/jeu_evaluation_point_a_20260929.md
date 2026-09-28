# Jeu d'évaluation — structure, outil de confirmation, atteignabilité

**2026-09-29.** Première passe de l'étape « jeu d'évaluation » du bloc 2 : le
schéma, le modèle vide, l'outil qui confirmera les réponses, et la définition de
l'atteignabilité. **Aucune question n'est écrite** : elles sont à la main de Max,
et l'étape s'arrête ici. Les 24 requêtes de décembre restent fermées jusqu'à ce
que les cinquante soient écrites.

## Environnement

PostgreSQL 16.15 local, base `apimanga`, DSN par `DATABASE_URL`, catalogue lu
dans `pg_catalog`. Toutes les lectures d'`apimanga` en lecture seule
(`default_transaction_read_only=on`). La migration n'a été jouée que sur des
bases jetables.

## Point A — le schéma existant ne suffisait pas

`bench.qrels` est au grain **document** : sa clé est `(query_id, doc_key)`, et
`doc_key` est une FK vers `corpus_docs` en `ON DELETE CASCADE`. Toute
reconstruction du corpus détruit les jugements — 154 des 157 le 2026-09-28.
`bench.queries` ne porte ni mode, ni famille, ni version ; une réponse attendue
vide y est indiscernable d'une question non jugée. `bench.metrics` ne peut pas
citer une version de jeu, et le module 06 y écrivait `recall@K` =
`hits / len(queries)` (un taux de succès) et `mrr` sans K, en `ON CONFLICT … DO
UPDATE` : la dernière exécution écrasait la précédente.

## Décisions (E1–E9, validées le 2026-09-29)

| | Décision |
|---|---|
| E1 | réponse attendue = `series_id` Manga Sanctuary, FK vers `ms_series_enriched` ; une issue hors catalogue ne porte pas de clé |
| E2 | tables neuves `bench.eval_*`, sans aucun lien vers `corpus_docs` ; `queries` / `qrels` restent l'historique de décembre |
| E3 | les CSV versionnés font foi, la table est une projection ; sha256 au gel ; une version gelée est immuable ; toute mesure cite sa version |
| E4 | trois modes : `proposition`, `reconnaissance`, `refus` (F7) — taux de refus correct, et taux de refus à tort sur les deux autres |
| E5 | le mode appartient à la question, pas à la famille |
| E6 | chaque mesure rend deux chiffres : toutes les questions, et celles que le corpus peut atteindre |
| E7 | trois issues : `au_catalogue` · `reconnue_hors_catalogue` (F1 ou F2, sans clé) · `inconnue` (F7). Attendre un refus sur une œuvre que le corpus indexe (37 258 synopsis Kitsu hors catalogue) testerait un filtre aval, pas la récupération |
| E8 | jeu dans `database/donnees/jeu_evaluation/`, outil dans `src/evaluation/` |
| E9 | mesures par famille **et** par mode — le critère C7 demande l'adéquation « pour chaque ensemble fonctionnel » |

**Règle de vérification.** La réponse s'écrit d'abord, telle que Max la connaît ;
l'outil la confirme par titre exact, auteur ou identifiant, jamais par une
recherche sur les mots de la question. Une réponse non retrouvable ainsi met la
question de côté. Motif : un outil de vérification flou serait lui-même un bras
lexical, et biaiserait d'avance F2 et F4 en sa faveur.

## Migration `016_jeu_evaluation.sql`

Quatre tables : `eval_jeux` (version, empreinte, date de gel, déclaration de
non-contamination), `eval_questions`, `eval_attendus`, `eval_mesures`. Les règles
sont tenues **par la base**, pas par la bonne volonté du chargeur :

| Règle | Mécanisme |
|---|---|
| F7 ⇔ refus ⇔ inconnue ; hors catalogue ⇒ F1/F2 en reconnaissance ; origine décembre ⇔ identifiant | `CHECK` |
| série attendue existante | FK vers `ms_series_enriched` |
| au_catalogue ⇒ ≥ 1 série ; reconnaissance au catalogue ⇒ exactement 1 ; autres issues ⇒ 0 | déclencheur **différé** à la validation |
| une version gelée ne change plus, une mesure ne s'écrase pas | déclencheurs `BEFORE UPDATE OR DELETE` ; `UNIQUE NULLS NOT DISTINCT` sur les mesures |
| K dans une colonne, jamais dans un nom ; K exigé pour hit_rate / recall / mrr / ndcg | `CHECK` |

`eval_mesures.run_id` n'a pas de FK : un bras lexical n'a pas de modèle
d'embedding, et `embedding_runs` en exige un. `bench` reste hors du périmètre de
`manga_ro`.

**Tests** : `database/tests/test_jeu_evaluation.py`, 33 cas sur base jetable, et
le rejeu complet 000→016 de `test_migrate.py` — 144 verts.

## Le modèle et l'outil

`database/donnees/jeu_evaluation/v1/` : `questions.csv` (colonnes du §6 +
`issue_attendue`) et `attendus.csv` (`reponse_ecrite` et `grade` écrits à la
main ; `series_id`, `titre_catalogue`, `confirmation` remplis par l'outil). Mode
d'emploi : `database/donnees/jeu_evaluation/README.md`.

`python -m evaluation.confirmer [--ecrire]` confirme chaque réponse écrite :

- `titre:` — égalité stricte avec un titre ou un alias du catalogue, après
  `identity.wikidata_dump.normaliser`, la normalisation unique de la cascade ;
- `auteur:` — égalité avec le dessinateur ou le scénariste ; se déplie en une
  ligne par série ;
- `id:` — le `series_id`.

Une réponse est confirmée, ambiguë (un titre, plusieurs séries), introuvable (la
question passe **de côté**) ou à revoir (issue contredite par le catalogue). Pour
`reconnue_hors_catalogue`, l'outil vérifie l'absence du catalogue, la présence
chez Kitsu et l'absence de rattachement par la cascade. Pour `inconnue`, il
vérifie l'absence du catalogue, de Kitsu et de Wikidata (titres et personnes).
Il signale les questions F3, F6, F8, F9, F10 qui portent la moitié ou plus des
mots du titre attendu, sans jamais les corriger. Il ne lit **que** `manga`,
jamais le corpus ni un index — vérifié par test. Il est idempotent.

**Tests** : `tests/test_evaluation_*.py`, 50 verts, dont « Bersek ne retrouve pas
Berserk » : la règle, pas seulement la fonction.

## Atteignabilité

**Définition.** Une série du catalogue est atteignable si au moins un **fragment** du corpus appartient à un document rattaché à elle : une critique par son `series_id`, ou un synopsis Kitsu dont le `kitsu_id` est rattaché à la série par `manga.work_identity`. La requête exacte :

```sql
WITH par_critique AS (
  SELECT DISTINCT d.series_id
  FROM bench.corpus_docs d
  WHERE d.source = 'ms_review'
    AND EXISTS (SELECT 1 FROM bench.corpus_chunks k WHERE k.doc_key = d.doc_key)
),
par_kitsu AS (
  SELECT DISTINCT w.series_id::bigint AS series_id
  FROM bench.corpus_docs d
  JOIN manga.work_identity w ON w.kitsu_id = d.kitsu_id::text
  WHERE d.source = 'kitsu_synopsis' AND w.series_id IS NOT NULL
    AND EXISTS (SELECT 1 FROM bench.corpus_chunks k WHERE k.doc_key = d.doc_key)
)
SELECT series_id FROM par_critique
UNION
SELECT series_id FROM par_kitsu
```

| | Séries |
|---|---:|
| Catalogue (`ms_series_enriched`) | 14 670 |
| atteintes par une critique | 3 456 |
| atteintes par un synopsis Kitsu, via la cascade | 5 819 |
| dont par les deux | 1 760 |
| **atteignables (union)** | **7 515 — 51,2 %** |
| au niveau document (sans exiger de fragment) | 7 520 |

**Défaut de construction.** Séries que la cascade rattache à une œuvre Kitsu, mais dont le synopsis n'est pas atteignable au corpus :

| | Séries |
|---|---:|
| rattachées à Kitsu par la cascade | 7 028 |
| **sans synopsis atteignable** | **1 209** |
| — œuvre absente de `kitsu_series_core` (source du corpus) | 1 201 |
| — dans la source, sans document au corpus | 0 |
| — document au corpus, sans fragment | 8 |

**Correction.** Le « 51,3 % » avancé au point A comptait les **documents** :
7 520 séries. Au grain du **fragment** — celui sur lequel opère la récupération
—, cinq séries ne sont reliées au corpus que par des synopsis Kitsu de moins de
50 caractères, restés sans fragment. La valeur exacte est **7 515 séries, 51,2
%**.

**Le défaut se corrige.** Les 1 201 séries dont l'œuvre Kitsu manque à
`kitsu_series_core` sont rattachées par la cascade à des œuvres que le
référentiel Kitsu de juillet connaît (`kitsu_formes`, `kitsu_meta`). Le corpus
tire ses synopsis d'une source plus ancienne que celle de l'identité. Dans le
brut Kitsu de juillet (`03_kitsu_api_exports/exports/full_catalog/20260714T152202Z/manga.ndjson`,
sha256 `9cfc3952…3056` conforme à son manifeste), les 1 201 œuvres sont
présentes, et **1 070 ont un synopsis non vide** ; 131 n'en ont aucun. Sur ces
1 070 séries, 211 sont déjà atteintes par une critique : le gain net serait
**859 séries au plus**, soit 7 515 → 8 374 (57,1 %). Le « au plus » tient au
plancher de 50 caractères, non appliqué dans ce décompte. La correction
reconstruit la part Kitsu du corpus : elle sort du périmètre de cette étape, qui
ne modifie pas le corpus.

## Un défaut trouvé en écrivant les tests, et corrigé

`corpus/mesurer.py` (étape précédente) ouvrait sa connexion puis exécutait `SET
SESSION CHARACTERISTICS AS TRANSACTION READ ONLY`. Cette instruction ne fixe que
les transactions **suivantes** ; celle qu'elle ouvre restait en écriture, et
toutes les mesures s'y déroulaient. Aucune écriture n'a eu lieu — le module ne
contient que des `SELECT` —, mais le rapport de mesures du 2026-09-28 affirme
une « session en lecture seule » que le code ne garantissait pas. Les trois
outils de lecture passent désormais `default_transaction_read_only=on` à la
connexion ; un test vérifie, pour chacun, que la première requête voit déjà
`transaction_read_only = on`, et échoue avec l'ancien code.

## Points ouverts, à trancher avant le gel

1. **F9 et la « demande de précision ».** La spec admet pour F9 une réponse
   attendue « ensemble large, **ou** demande de précision ». Les trois issues ne
   représentent pas le second cas. Soit F9 attend toujours un ensemble
   (proposition), soit il faut une quatrième issue.
2. **§8.3 et le mode refus.** « Chaque mode au moins vingt » ne peut tenir pour
   `refus` qu'avec vingt questions F7. À adapter (seuil propre au mode refus) ou
   à assumer.
3. **Le chargeur de gel** (projection des CSV vers `eval_*`, empreinte,
   déclaration) et le contrôle §8.2 de non-recouvrement des F3 avec les
   critiques restent à écrire, pour la passe du gel.
4. **La migration n'est appliquée à aucune base réelle.** L'appliquer à
   `apimanga` la rend immuable (checksum du runner). Elle n'est nécessaire qu'au
   gel.

## Ce que je n'ai pas pu établir

- la représentativité du futur jeu face à l'usage réel — elle ne se mesure qu'en
  production ;
- la stabilité de `work_uid` si `work_identity` était reconstruite (identité
  générée) ; E1 ne s'y fie pas ;
- le statut des 18 séries du catalogue absentes du snapshot 2026-07 ;
- la couverture du bras personnages (F6), qui n'est pas dans `bench` ;
- le nombre exact de synopsis Kitsu de juillet qui franchiraient le plancher de
  50 caractères.
