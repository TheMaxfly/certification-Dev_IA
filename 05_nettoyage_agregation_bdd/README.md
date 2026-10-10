# preparation_bdd — exploration JSON & CSV

Objectif : préparer un environnement Python simple pour explorer des fichiers `.json`, `.jsonl` et `.csv` (aperçu, schéma/colonnes, statistiques rapides).

## Prérequis

- Python 3.11+

## Installation

```bash
uv sync --all-extras
```

Optionnel : crée un fichier `.env` (voir `.env.example`) pour définir `DATA_DIR`.

## Exemples

```bash
uv run python -m preparation_bdd csv data/sample.csv --head 10
uv run python -m preparation_bdd json data/sample.jsonl --limit 5
```

## Notes Git (données / exports)

Le `.gitignore` ignore par défaut :

- `data/*` (sauf `data/sample.csv` et `data/sample.jsonl`)
- les exports volumineux en `exports/**/*.csv` et `exports/**/*.parquet`
- les sorties `out_ms_*`

Si tu veux versionner un fichier ignoré (ex: un KPI en JSON), utilise `git add -f <fichier>`.

## Notebooks

Notebooks disponibles dans `notebooks/` (exemples) :

- `notebooks/analyse_kitsutoprated_fixed_v2.ipynb`
- `notebooks/analyse_ms_volumes_step1_parquet_csv_jsonb_ready.ipynb`
- `notebooks/analyse_ms_reviews_step2_parquet_csv.ipynb`

Conseils :

- sélectionne le kernel Python de `.venv`
- si tu vois `PermissionError: ... ~/.jupyter` (sandbox/droits), définis `JUPYTER_CONFIG_DIR`, `JUPYTER_DATA_DIR` et `JUPYTER_RUNTIME_DIR` vers des dossiers du projet (voir `.env.example`), puis redémarre VS Code / le notebook
- certains notebooks exportent en CSV + Parquet ; les dépendances correspondantes
  sont installées par `uv sync --all-extras`

## Commandes

```bash
uv run python -m preparation_bdd --help
uv run python -m preparation_bdd csv --help
uv run python -m preparation_bdd json --help
uv run python src/identity/wikidata_dump.py --help
```

### Mesures (lecture seule)

Elles ne décident rien et n'écrivent rien en base : elles instruisent, et
produisent un rapport JSON horodaté dans `data/rapports/`.

```bash
# Par quel SCRIPT passent les concordances d'auteur de l'étage 1 (dette 22.3).
DATABASE_URL='postgresql://manga_api@localhost:5432/apimanga' \
    PYTHONPATH=src uv run python -m identity.mesure_formes_auteur
```

**Ce que ce chiffre veut dire.** Dénominateur : les séries dont la décision
courante est `method='exact_author'`, `status='auto'` — celles dont l'identité
existe parce que le signal auteur a tranché. « Forme latine » : `forme_norm`
sans idéogramme ni kana — c'est la **graphie** qui est mesurée, pas le tag
`langue` de Wikidata, les deux ne coïncidant pas.

Mesure du **2026-08-25** : **100,00 %** (1 058 / 1 058). Sans
`wd_auteurs_formes`, l'étage 1 tomberait à **21** séries. Ces valeurs
remplacent les chiffres indicatifs antérieurs (99,6 % et 4 séries), qui
venaient d'une requête de diagnostic non conservée — cf. ETAT,
section « Dette 22.3 — le 99,6 % certifié : il vaut 100,00 % ».

### Corpus RAG (`bench`)

Reconstruit `bench.corpus_docs` / `bench.corpus_chunks` depuis
`manga.ms_reviews_all` et le snapshot Manga Sanctuary 2026-07, sans donnée
d'auteur. Règle, décisions et résultats : `rapports/corpus_decisions_20260928.md`.

```bash
export DATABASE_URL='postgresql://postgres@localhost:5432/apimanga'
uv run python -m corpus.construire --dry-run   # gardes, diff, contrôles §7, ROLLBACK
uv run python -m corpus.construire             # applique (un rejeu n'écrit rien)
uv run python -m corpus.construire --a-blanc   # vide + reconstruit, compare, ROLLBACK
uv run python -m corpus.mesurer                # mesures, session en lecture seule
```

Chaque commande écrit un rapport horodaté dans `rapports/`. Les exemples
d'auto-référence que produit `mesurer` contiennent du texte à juger : ils vont
dans `data/corpus_exemples/`, hors dépôt. L'état du banc de décembre, avant
reconstruction, est archivé dans `data/archives/bench_2025-12/` (voir son
`MANIFEST.md`).

**Plusieurs corpus coexistent** dans `bench` depuis la migration 021 (E3, étape 1) :
`v1` (48 090 documents, 66 290 fragments, découpage `char_1200_overlap_200`) et `v2`
(56 775 / 77 006, découpage par phrases). Chacun est **clos** : la base refuse toute
écriture dans un corpus clos, sauf extension explicite. `corpus.construire` nomme
`v1` et lui seul.

```bash
uv run python -m corpus.phrases --enregistrer   # inscrit la stratégie de découpage (une fois)
uv run python -m corpus.construire_v2 --dry-run # tout, contrôles compris, ROLLBACK
uv run python -m corpus.construire_v2           # construit, contrôle, clôt le v2
```

- **Le v2** : la règle du v1, avec trois différences et seulement trois
  (`config/corpus_v2.toml`) :
  - les résumés de série du raw de juillet, sous la forme « Résumé Manga <titre> », un
    saut de ligne, puis le résumé ;
  - le découpage par phrases (`corpus.phrases`, réglages et motifs dans
    `config/decoupage_phrases.toml` : 1 200 caractères, recouvrement d'une phrase de
    200 au plus) ;
  - le texte des critiques n'est plus masqué : seul le nom de l'auteur reste
    anonymisé.
- **Résumés exclus**, dans cet ordre : moins de 50 caractères ; faux résumé (le texte
  contient l'amorce d'un lien vers une chronique, ou une critique entière) ;
  pseudonyme dans le texte. Les pseudonymes sont contrôlés avant d'écrire, et le
  contrôle est bloquant.
- **Une seule porte d'écriture** : `corpus.ecriture.ecrire_corpus(curseur,
  corpus_id=…)`, corpus en argument nommé, sans défaut (`CONTRIBUTING.md`). Un test
  vérifie qu'aucune autre écriture des documents ou des fragments n'existe dans le
  module.
- **Lire un corpus** : les mesures et contrôles (`corpus.mesurer`,
  `evaluation.atteignabilite`, `evaluation.controles`, `evaluation.hors_catalogue`,
  `identity.propagation_kitsu`) lisent le corpus nommé (`--corpus`, là où
  l'option existe). Sinon, celui de l'encodage **en service**
  (`bench.v_encodage_en_service`), sinon le seul corpus. Avec plusieurs candidats,
  ils refusent. Depuis la promotion du 2026-10-10, le corpus en service est le
  **v2** : un chiffre d'atteignabilité doit dire son corpus (v1 : 8 718 séries sur
  14 670 ; v2 : 11 155).

### Jeu d'évaluation (bloc 2)

Le jeu s'écrit à la main dans `database/donnees/jeu_evaluation/v1/` (mode
d'emploi : `database/donnees/jeu_evaluation/README.md`). Ces commandes ne
rédigent rien et n'exécutent aucune récupération : elles **confirment** les
réponses écrites contre le catalogue, par titre exact, auteur ou identifiant, en
session lecture seule. Décisions et définitions :
`rapports/jeu_evaluation_point_a_20260929.md`.

```bash
export DATABASE_URL='postgresql://postgres@localhost:5432/apimanga'
uv run python -m evaluation.confirmer            # vérifie, rapporte, n'écrit rien
uv run python -m evaluation.confirmer --ecrire   # remplit series_id / titre_catalogue / confirmation
uv run python -m evaluation.atteignabilite       # part du catalogue que le corpus peut atteindre
uv run python -m evaluation.atteignabilite --corpus v1   # d'un corpus nommé
```

## Makefile

```bash
make setup
make lint
make test
```
