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
| `schema_reel.md` | **La preuve de fidelite** : inventaire complet extrait de la base (43 tables, 659 colonnes, 20 FK), plus la confrontation avec les migrations. |
| `modele_donnees.mmd` | Version Mermaid du MPD coeur (secondaire, rendu automatique sur GitHub). |
| `schema.json` | Extraction brute, source des generateurs. |
| `extraire_schema.py` · `generer_schema_reel.py` · `generer_diagrammes.py` · `layout.py` | La chaine de production, rejouable. |

## Regenerer

L'extraction est jouee sous le **role de consultation** `manga_api`, en session
`default_transaction_read_only = on` : le livrable se produit avec les droits
les plus faibles, pas avec ceux du proprietaire.

```bash
export DATABASE_URL='postgresql://manga_api@localhost:5432/apimanga?options=-c%20default_transaction_read_only%3Don'
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
`v_match_current`, `llm_avis` — sous le centre, avec `volume_identity` ; et,
en haut a gauche, l'ilot du **referentiel de genres**, qui n'est pas une source
mais le vocabulaire cible (cf. plus bas).

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

### Le referentiel de genres, et pourquoi il est a part

`genre_ref` / `genre_mapping` forment un **ilot** : aucune FK ne les relie au
reste du schema. Ce n'est pas un oubli, c'est leur nature. Les autres familles
decrivent des **sources** ; celle-ci decrit le **vocabulaire cible** vers lequel
les libelles bruts des sources sont ramenes. 73 codes, dont 14 ont un parent.

Deux choses doivent se lire sur la planche.

**1. `genre_ref.parent` est la seule FK du schema qui pointe sur sa propre
table** — 20 FK au catalogue, une seule reflexive. Elle est dessinee comme
telle : une **boucle** qui sort du bord droit de la boite et rentre par son bord
superieur.

Ce qu'elle sert : une serie cataloguee `yaoi` est *mieux* documentee qu'une
serie cataloguee `lgbt`, parce que la source a ete plus precise. Sans
hierarchie, un filtre sur le code generique ne la trouve pas — le filtre
grossier rate les series les mieux decrites, soit l'inverse de ce qu'on attend.
`parent` repare cela : au recalcul, une serie portant un code fin recoit aussi
ses ancetres. Dix codes portent des enfants (`lgbt` -> `gender_bender`, `yaoi`,
`yuri` ; `adulte` -> `ecchi`, `erotique`, `hentai` ; `drame` -> `tragedie` …).

La contrainte est **DEFERRABLE**, et c'est la boucle qui l'explique : le
chargeur ecrit les 73 codes en **un seul** `INSERT ... SELECT unnest(...)`, et
rien n'ordonne les lignes d'un `unnest`. Si `yaoi` est insere avant `lgbt`, une
FK immediate refuse la ligne. Differer la verification a la fin de la
transaction est la seule des trois issues possibles qui decrive le vrai
invariant : ce n'est pas l'**ordre d'ecriture** qui doit etre correct, c'est
l'**etat a la validation**.

**2. La colonne `type` (`genre` | `format`)** distingue le vocabulaire cible du
reflet des sources. La migration `013` marquait les formats par un prefixe
`format_` : lisible, et vrai meme dans un export ou la table n'est pas jointe —
mais **un prefixe ne se controle pas**. Rien n'empeche un code `format_x` d'etre
cree sans intention, ni un format d'arriver sans prefixe. `type` rend la nature
**declarative et verifiable par un CHECK** ; le prefixe est conserve, et les deux
se validant mutuellement, un test le verifie.

Repartition reelle : **65 codes `genre`** (51 racines, 14 enfants) et **8 codes
`format`**, tous racines — un format ne se specialise pas.

`genre_mapping` est le journal de la reduction : `libelle_brut` tel qu'il arrive
de la source, `code` vers lequel il est ramene, `statut` de la decision. Les 8
codes `format_*` ne sont pas hors des sources : chacun est atteint depuis un
libelle brut reel (`Histoires courtes` -> `format_histoires_courtes`). Le seul
code du referentiel sans aucune correspondance est **`adulte`**, introduit par
`014` comme parent commun d'`ecchi` / `erotique` / `hentai` : lui seul appartient
au vocabulaire cible sans etre le reflet d'un libelle observe. D'ou la regle —
tenue par un test, non par une contrainte, puisqu'elle porte sur deux tables
chargees l'une apres l'autre : **un code sans correspondance doit etre le parent
d'au moins un code mappe**, sinon c'est du vocabulaire mort.

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
| boucle sur la boite | FK d'une table **vers elle-meme** (`genre_ref.parent`) |
| couleur | famille : coeur / journal / Manga Sanctuary / Wikidata / Kitsu / Manga Insight / Referentiel de genres |

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
