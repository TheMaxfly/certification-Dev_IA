# Jeu d'évaluation — comment l'écrire

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
| `famille` | `F1` à `F10` |
| `issue_attendue` | `au_catalogue` · `reconnue_hors_catalogue` · `inconnue` |
| `origine` | `nouvelle` · `decembre` |
| `origine_query_id` | l'identifiant de décembre si `origine = decembre`, vide sinon |
| `note` | ce que la question teste, en une ligne |

Règles tenues par la base, et vérifiées par l'outil avant elle :

- `F7` ⇔ `refus` ⇔ `inconnue` ;
- `reconnue_hors_catalogue` ⇒ `F1` ou `F2`, en `reconnaissance` ;
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
| `reponse_ecrite` | toi | `titre: …`, `auteur: …` ou `id: …` — la réponse **telle que tu la connais** |
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
