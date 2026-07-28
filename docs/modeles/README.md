# Modeles de donnees — MCD / MPD du referentiel manga

Livrable documentaire du bloc C4. Les planches sont **derivees du schema reel**
de la base `apimanga` : aucune table, aucune colonne, aucune cle etrangere n'y
est saisie a la main.

## Fichiers

| Fichier | Role |
| --- | --- |
| `modele_donnees.drawio` | **La reference.** 3 onglets, editable dans draw.io / diagrams.net / l'extension VS Code. |
| `mpd_coeur.png` · `mpd_staging.png` · `mcd.png` | Exports pour insertion directe au rapport (2800 x 1980, A3 paysage). |
| `mpd_coeur.svg` · `mpd_staging.svg` · `mcd.svg` | Sources vectorielles des PNG. |
| `schema_reel.md` | **La preuve de fidelite** : inventaire complet extrait de la base (41 tables, 647 colonnes, 18 FK), plus la confrontation avec les migrations. |
| `modele_donnees.mmd` | Version Mermaid du MPD coeur (secondaire, rendu automatique sur GitHub). |
| `schema.json` | Extraction brute, source des generateurs. |
| `extraire_schema.py` · `generer_schema_reel.py` · `generer_diagrammes.py` · `layout.py` | La chaine de production, rejouable. |

## Regenerer

```bash
export DATABASE_URL='postgresql://postgres@localhost:5432/apimanga'
cd docs/modeles
uv run --with 'psycopg[binary]' python extraire_schema.py > schema.json   # lecture seule
uv run python generer_schema_reel.py                                      # schema_reel.md
uv run python generer_diagrammes.py                                       # .drawio + .svg + .mmd
uv run --with cairosvg python -c "
import cairosvg
for b in ['mpd_coeur','mpd_staging','mcd']:
    cairosvg.svg2png(url=f'{b}.svg', write_to=f'{b}.png')"
```

`generer_diagrammes.py` sort un rapport JSON de controle : positions hors
grille, chevauchements, gouttieres trop courtes, debordements, **croisements de
liens** et **traversees de boite**. L'etat vise et atteint est **0 partout**.

## Ce que montre chaque planche

### Planche 1 — MPD coeur

`manga.work_identity` au centre ; les quatre familles de sources en couronnes
(Manga Sanctuary a gauche, Wikidata en haut a droite, Kitsu en bas a droite,
Manga Insight en bas a gauche) ; le bloc journal — `match_decision`,
`v_match_current`, `llm_avis` — sous le centre, avec `volume_identity`.

Ce qu'il faut y lire :

- **six index UNIQUE partiels** sur `work_identity` (`series_id`,
  `wikidata_qid`, `kitsu_id`, `mal_id`, `anilist_id`, `madb_id`, chacun
  `WHERE … IS NOT NULL`). C'est la mecanique qui empeche deux oeuvres de
  revendiquer le meme identifiant externe, tout en tolerant les inconnus ;
- **le journal est append-only** : `match_decision` accumule, `v_match_current`
  n'est qu'une lecture de la decision courante — d'ou le trait pointille ;
- **`match_decision` n'a aucune contrainte FK.** Ses rattachements a
  `ms_series_enriched` et `wd_pivot` sont applicatifs, donc en pointille. Les
  dessiner en trait plein aurait ete un mensonge sur le schema.

### Planche 2 — MPD staging (annexe)

Les 11 tables `staging.*`, en representation **simplifiee** : nom, mention
« toutes colonnes en text », colonnes techniques de tracabilite et nombre total
de colonnes. Le detail n'apporte rien parce que ces tables sont **jetables** :
elles recoivent le fichier brut sans typage ni rejet, et le typage a lieu
ensuite, en SQL, vers `manga.*`. C'est le L et le T de l'ELT.

### Planche 3 — MCD (Merise)

Huit entites metier — OEUVRE, VOLUME, SERIE-SOURCE, FORME, AUTEUR, CRITIQUE,
DECISION-DE-RAPPROCHEMENT, AVIS-LLM — et leurs associations avec cardinalites
(min,max). **Ni type, ni cle technique** : un MCD dit ce que le domaine
contient, pas comment PostgreSQL le stocke.

L'association centrale se lit : *SERIE-SOURCE (0,n) --designe--> (0,1) OEUVRE*,
c'est-a-dire « une serie de source designe au plus une oeuvre ; une oeuvre est
designee par zero a n series venues de plateformes differentes ». C'est le
verrou du projet exprime en conceptuel : **aucune plateforme ne partage
d'identifiant**, donc l'oeuvre est une construction, pas une donnee recue.

## Conventions de lecture (rappelees en legende sur chaque planche)

| Notation | Sens |
| --- | --- |
| **`PK nom : type`** (gras souligne) | cle primaire |
| *`FK nom : type`* (italique) | cle etrangere |
| `U nom : type` | index UNIQUE (partiel s'il est conditionnel) |
| `nom : type *` | colonne NOT NULL |
| trait plein | contrainte FK reelle du catalogue |
| trait pointille | lien applicatif, **sans** contrainte FK |
| couleur | famille : coeur / journal / Manga Sanctuary / Wikidata / Kitsu / Manga Insight |

Les tables de plus de 12 colonnes sont **tronquees** a l'affichage, avec la
mention `… (n autres colonnes)`. La lisibilite prime ; le detail complet est
dans `schema_reel.md`.

## Perimetre exclu, et pourquoi

Sept tables `manga` ne figurent pas sur la planche 1 :

- `rag_kitsu_docs`, `rag_reviews_docs` et **`ms_reviews`** — heritage du corpus
  RAG. Attention au piege : `ms_reviews` contient **3 187 documents RAG**, ce
  n'est pas le referentiel des critiques ; le referentiel est
  **`ms_reviews_all`** (11 074 lignes), qui, lui, est sur la planche ;
- `kitsu_series_core_stg`, `kitsu_series_core_stage`,
  `kitsu_series_authors_stg`, `kitsu_weekly_snapshot_stg` — du staging heritage
  reste dans le schema `manga`, sans role applicatif.

Le schema `bench.*` (module 06, experimental) est hors sujet de ce modele : il
sert aux benchmarks d'embeddings, pas au referentiel.

## Note sur la generation des PNG

Les PNG sont produits par le **meme generateur** et la **meme geometrie** que le
`.drawio` (memes objets `Boite` et `Lien`), via un rendu SVG puis `cairosvg` —
et non par le moteur de rendu de draw.io, dont l'outil en ligne de commande
n'est pas disponible dans cet environnement. Les deux sorties ne peuvent pas
diverger, puisqu'elles lisent la meme mise en page ; mais un export refait
depuis draw.io peut differer a la marge sur le rendu typographique.
