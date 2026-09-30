# Jeu d'évaluation — comment l'écrire

> **La version qui fait foi est `v2/`**, gelée le 2026-09-30 — empreinte
> `31712b0d0b0cbd318ef228da105673af0cba2db431a72462f7635b2f6ec78b30`.
> **v2 = v1 au titre près** : v1, gelée le même jour, a été retirée pour un mot
> dans un titre de déclaration, aucune question modifiée ; elle reste en base
> locale et sa version complète est archivée hors dépôt. Le dossier `v1/` ne
> garde que les gabarits vides de `questions.csv` et `attendus.csv`, décrits
> ci-dessous.

Le jeu de référence du bloc 2, au **grain entité** : une réponse attendue est
une série du catalogue, désignée par son `series_id`, jamais par un titre
recopié. Les CSV de `v1/` **font foi** ; les tables `bench.eval_*` (migration
`016`) n'en sont que la projection, posée au gel.

## La discipline

1. Les questions s'écrivent **sans ouvrir le système** : aucune recherche,
   aucune récupération pour « voir ce que ça donne ». Une question écrite après
   avoir vu un résultat est contaminée et sort de la version.
2. **La réponse s'écrit d'abord, telle que tu la connais**, dans
   `attendus.csv` (`reponse_ecrite`). L'outil la **confirme** ensuite contre le
   catalogue par titre exact, auteur exact ou identifiant, **jamais par une
   recherche sur les mots de la question**. Une réponse non retrouvable ainsi
   met la question de côté : on ne cherche pas autrement.
3. Une fois le jeu écrit, confirmé et relu, il est **gelé** : sha256, version,
   déclaration datée de non-contamination. Une version gelée ne change plus. Toute
   correction ouvre une `v2`.

## `v1/questions.csv` — une ligne par question

| Colonne | Valeurs |
|---|---|
| `question_id` | `Q001`, `Q002`… — indépendant de la famille, pour survivre à un reclassement |
| `texte` | tel qu'un utilisateur l'écrirait, fautes comprises pour F4 |
| `mode` | `proposition` · `reconnaissance` · `refus` |
| `famille` | `F1` à `F11` — F11 « par référence » depuis `019` (2026-09-30) |
| `issue_attendue` | `au_catalogue` · `reconnue_hors_catalogue` · `inconnue` |
| `origine` | `nouvelle` · `decembre` |
| `origine_query_id` | l'identifiant de décembre si `origine = decembre`, vide sinon |
| `note` | ce que la question teste, en une ligne |

Règles tenues par la base, et vérifiées par l'outil avant elle :

- `F7` ⇔ `refus` ⇔ `inconnue` ;
- `reconnue_hors_catalogue` ⇒ `reconnaissance`, toute famille (`017`) ;
- `au_catalogue` ⇒ au moins une série attendue ; en `reconnaissance`, **exactement
  une** ;
- `reconnue_hors_catalogue` et `inconnue` ⇒ **aucune** série attendue.

Le mode de la famille (§5 de la spec) est un mode **par défaut** : c'est la
question qui porte le sien.

## `v1/attendus.csv` — une ligne par réponse écrite

Tu remplis trois colonnes ; l'outil remplit les trois autres.

| Colonne | Qui | Contenu |
|---|---|---|
| `question_id` | toi | la question |
| `reponse_ecrite` | toi | `titre: …`, `auteur: …`, `id: …` ou `regle: Qnnn` — la réponse **telle que tu la connais** |
| `grade` | toi | `2` très pertinent, `1` pertinent — vide pour les issues sans clé |
| `series_id` | l'outil | la clé du catalogue |
| `titre_catalogue` | l'outil | le titre **tel que le catalogue l'écrit** — une vérification visuelle, jamais une clé |
| `confirmation` | l'outil | par où la réponse a été confirmée, ou pourquoi elle ne l'a pas été |

- **`titre:`** — comparé aux titres et alias du catalogue après la normalisation
  de la cascade d'identité (casse, accents, ponctuation, article initial). Ce
  n'est pas une recherche : égalité stricte des formes normalisées.
- **`auteur:`** — comparé au dessinateur et au scénariste, tels que le catalogue
  les écrit (prénom puis NOM). Une ligne `auteur:` se déplie en **une ligne par
  série** de l'auteur ; en `reconnaissance`, un auteur à plusieurs séries est
  ambigu.
- **`id:`** — le `series_id`, quand un titre est ambigu ou pour lever un doute.

- **`regle:`** — F9 et F10 seulement, en `proposition` : l'ensemble attendu est
  **dérivé** par une règle SQL versionnée, `v1/regles/Qnnn.sql` (une par
  question, du nom de la question), exécutée en lecture seule. Son texte entre
  dans l'empreinte du jeu au gel. Deux contraintes, vérifiées par l'outil :
  - **des colonnes, jamais les mots de la question** — toute recherche
    textuelle est refusée (`LIKE`, `ILIKE`, `~`, `SIMILAR TO`, plein texte,
    similarité). Filtrer `series_category_clean = 'Shonen'` est une colonne ;
    la liste des colonnes admises (genre, année, note, tomes, statut) relève de
    la relecture ;
  - **un plafond** — la règle finit par `LIMIT n`, sous un `ORDER BY` qui
    départage par `series_id`, pour que le nDCG garde un sens et que la même
    règle rende toujours le même ensemble.

  Exemple réel : `v1/regles/Q045.sql` (« un bon shonen »). Les définitions
  communes — public, note des membres, critiques comptées dans les tables du
  snapshot (jamais `series_review_count`, figée à décembre 2025), genres par
  codes du référentiel, année FR, tomes — sont écrites en tête de chaque règle.

  La règle s'écrit aussi en clair dans la `note` de la question.

**Issues sans clé.** Pour une question `reconnue_hors_catalogue`, écris l'œuvre
(`titre: …`) : l'outil vérifie qu'elle est **absente du catalogue** et **connue
de Kitsu**. Pour une question `inconnue` (F7), tu peux écrire le titre ou
l'auteur inventé : l'outil vérifie qu'il n'existe **nulle part** (catalogue,
Kitsu, Wikidata). Ces lignes gardent `series_id` vide et ne sont pas projetées
dans `bench.eval_attendus`.

## Confirmer

```bash
cd 05_nettoyage_agregation_bdd
export DATABASE_URL='postgresql://postgres@localhost:5432/apimanga'
uv run python -m evaluation.confirmer            # vérifie et rapporte, n'écrit rien
uv run python -m evaluation.confirmer --ecrire   # remplit series_id / titre_catalogue / confirmation
```

L'outil lit le catalogue en session **lecture seule** et n'interroge jamais le
corpus ni aucun index. Relancé sur un fichier déjà confirmé, il le rend
identique.

## Le format source v1 — un seul CSV écrit à la main

Depuis le 2026-09-29, le jeu s'écrit dans `jeu_evaluation_recherche.csv` (v2 : `v2/`)
(une ligne par question : séries attendues séparées par « | », grades par
titre, `règle : …` / `par règle` pour F9 et F10, `cle_confirmation` pour une
clé combinée `Titre => auteur: Nom`). `questions.csv` et `attendus.csv` n'en
sont que la **projection**, écrite au gel.

- **Reprise de décembre** : `origine` = `decembre:7` — l'identifiant de la
  requête de décembre, rangé dans `origine_query_id` (2026-09-30).
- **F11 « par référence »** : la question cite une œuvre connue ; la référence
  n'est **jamais** une réponse attendue.

## Contrôler, puis geler

```bash
cd 05_nettoyage_agregation_bdd
export DATABASE_URL='postgresql://postgres@localhost:5432/apimanga'
uv run python -m evaluation.controles                       # §8 : existence, recouvrement F3, effectifs
uv run python -m evaluation.geler --version v2              # gel à blanc : dossier et base intacts
uv run python -m evaluation.geler --version v3 --executer   # gel réel d'une version nouvelle — IRRÉVERSIBLE
```

- **`controles_v1.json`** (dans le dossier de la version) porte la définition du §8.2 — un mot est non
  significatif s'il est porté par plus de **5 %** des critiques du corpus, ou
  s'il figure dans `mots_non_significatifs_v1.txt` — avec la **dérivation** du
  seuil, et les effectifs minimaux. Il entre dans l'empreinte.
- **Le gel** refuse une question non confirmée, un contrôle en échec, une
  version déjà gelée ou l'absence de `DECLARATIONS.md`. Il écrit la
  projection, calcule l'empreinte (sha256 d'un **manifeste** de tous les
  fichiers de la version) et charge `bench.eval_*` en une transaction ; à blanc,
  tout est joué dans un dossier temporaire et une transaction annulée, les
  contraintes différées de `016` forcées pour que l'essai prouve ce que le gel
  prouvera.
- **Le marqueur « atteignable par synopsis anglais seulement »** n'est **jamais
  écrit** dans le jeu : il se calcule à chaque mesure depuis le corpus
  (`evaluation.atteignabilite.synopsis_seul`), comme l'atteignabilité.
