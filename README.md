# Certification Dev IA

Depot principal du projet de certification Dev IA : chaine de collecte, nettoyage,
stockage, exposition API et evaluation RAG pour un assistant de recommandation
manga.

Le workflow de reference est dans `workflow_certification_dev_ia.drawio`.

## Objectif

Construire une chaine Data / IA reproductible autour de donnees manga :

1. collecter des donnees depuis plusieurs sources ;
2. nettoyer, normaliser et valider les jeux de donnees ;
3. charger les donnees dans PostgreSQL ;
4. exposer les metadonnees et le corpus RAG-ready via une API ;
5. evaluer les embeddings, la recherche vectorielle et les reponses LLM.

## Workflow

La chaine est un **ELT** : le raw date est immuable, il entre d'abord en staging
TOUT-TEXT sans typage, puis la transformation typee se fait dans la base. La
consequence pratique est la rejouabilite : **on peut re-transformer sans
recollecter**. C'est ce qui a permis de reparer les alias tronques sans relancer
un crawl, et de trancher les 59 volumes perdus en rejouant le fichier d'origine.

```text
Sources externes
  -> collecte Scrapy / API
  -> nettoyage, enrichissement, validation
  -> PostgreSQL schema manga
  -> FastAPI + recherche plein texte
  -> corpus de recherche versionne (schema bench)
  -> encodage (pgvector) et mesure de la recherche
```

Le depot est organise par etapes numerotees.

## Structure du depot

| Dossier | Role | Etat |
| --- | --- | --- |
| `01_scraping_manganews/` | Scraping Manga-News avec Scrapy, validation Great Expectations et import PostgreSQL. | Collecte 2026-07 terminee : 11 717 series et 50 populaires. |
| `02_api_manga/` | API FastAPI en lecture seule sur PostgreSQL : metadonnees, corpus RAG et recherche plein texte. | MVP teste, avec Compose d'integration. |
| `03_kitsu_api_exports/` | Client Kitsu et collecteur exhaustif reprenable. | Catalogue, mappings et staff termines ; characters hors perimetre actuel. |
| `04_scraping_manga_sanctuary/` | Scraping Manga Sanctuary : series, tomes et critiques staff. | Snapshot 2026-07 promu : 103 811 volumes et 11 052 critiques. |
| `05_nettoyage_agregation_bdd/` | Nettoyage, normalisation, identite multi-source, corpus de recherche et jeu d'evaluation. | Package `identity` : cascade complete (etages 0 a R) et promotion decisionnelle (run 2). Package `corpus` : deux corpus clos dans `bench`, `v1` (48 090 documents, 66 290 fragments) et `v2` (56 775 documents, 77 006 fragments). Package `evaluation` : jeu d'evaluation `v2` gele (69 questions, 11 familles). 542 tests. |
| `06_benchmark_embeddings_llm/` | Premier benchmark : embeddings, FAISS, recall@K, MRR et evaluation LLM. | Scripts experimentaux. Remplace, pour l'encodage et la mesure de la recherche, par les modules 09 et 10 ; ses tables restent dans `bench`, non utilisees par la chaine actuelle. |
| `07_databricks_manga_export/` | Lakehouse Spark + Delta conteneurise : medaillon bronze / silver / gold sur le raw multi-snapshots, controles qualite historises. | v2 livree : 4 sources en bronze, silver Manga Sanctuary, 5 tables gold ; 18 tests, 12 / 12 verifications en conteneur. Couche consultative, hors chemin critique. |
| `08_enrichissement_personnages/` | Personnages : collectes Wikipedia francais et AniList sur une ossature commune, chargeurs Kitsu et Wikipedia vers les tables `manga.character*`. | Charges : 34 293 personnages et 105 048 formes (Kitsu), 13 101 personnages et 21 398 formes (Wikipedia francais) ; 90 tests. |
| `09_service_embedding/` | Service d'embedding Text Embeddings Inference (image officielle, GPU) : deux instances, BGE-M3 et EmbeddingGemma, configuration commentee, client qui applique les prefixes, encodeur du corpus, test d'equivalence du service. | Trois encodages float32 dans `bench` (pgvector) : BGE-M3 (1 024 d) et EmbeddingGemma (768 d) sur le corpus `v1`, 66 290 vecteurs chacun (2026-10-07) ; EmbeddingGemma sur le corpus `v2`, 77 006 vecteurs (2026-10-10). Reprise et rejeu sans ecriture ; 72 tests. |
| `10_mesures_recherche/` | Banc de mesure de la recherche sur le jeu d'evaluation gele : 17 configurations en trois tours (sens BGE-M3 et EmbeddingGemma, plein texte PostgreSQL, TF-IDF, fusions, classement limite au catalogue, representation de l'oeuvre), metriques au grain entite, MLflow ; regle de promotion et journal des promotions. | 18 runs enregistres (MLflow local, `bench.eval_mesures`, 1 872 lignes) : les 17 mesures sur le corpus `v1`, puis la mesure 8 sur le corpus `v2`. Mesure 8 : 31 questions reussies sur 59, sur l'un comme sur l'autre. Encodage du `v2` en service depuis le 2026-10-10 ; 120 tests. |
| `database/` | Migrations PostgreSQL partagees et versionnees. | 22 migrations (`000` a `021`), toutes appliquees a `apimanga`, 0 en attente ; fidelite du schema verifiee ; 213 tests sur base jetable. |
| `demo/` | Console `manga-pipeline` : ecran d'etat et menu d'actions groupe par phase ELT, en orchestrant les CLI existantes. | Mode lecture seule par defaut ; 17 actions ; 78 tests. |

## Sources de donnees

### Manga-News

Source scrapee avec Scrapy dans `01_scraping_manganews/`.

Sorties principales :

- `data/enriched/manganews_series.jsonl`
- `data/enriched/populaires.jsonl`
- versions backfilled utilisees pour validation et import PostgreSQL

La chaine Manga-News contient :

- nettoyage et enrichissement des champs ;
- construction de `rag_text` ;
- flags de qualite pour Great Expectations ;
- validation critique / warning ;
- staging PostgreSQL ;
- upsert final ;
- audit des imports.

Le snapshot courant contient **11 717 series** et **50 entrees populaires**. Le
snapshot du 31 decembre 2025 reste archive localement avec son manifeste SHA-256.

### Kitsu

Source API publique dans `03_kitsu_api_exports/`.

Le run exhaustif `exports/full_catalog/20260714T152202Z/` est termine sur le
perimetre utile :

- catalogue : **62 768 mangas** ;
- mappings externes : **104 726 ressources** ;
- staff : **53 183 ressources** ;
- `top_rated.json` derive du catalogue complet.

La relation `characters` est conservee partiellement mais n'est ni reprise ni
utilisee dans le pipeline actuel. Le collecteur stocke les JSONL bruts, un
`state.json` et un manifeste afin de permettre une reprise fiable.

### Manga Sanctuary

Source scrapee avec Scrapy dans `04_scraping_manga_sanctuary/`.

Donnees ciblees :

- metadonnees series ;
- tomes / editions ;
- notes membres et experts ;
- critiques staff ;
- synopsis series et tomes.

Le snapshot `data/raw/2026-07/` a ete promu apres validation : **103 811 volumes**,
**14 652 series distinctes** et **11 052 critiques**. Les donnees restent locales
et ignorees par Git.

### Lakehouse qualite (module 07)

Couche transverse dans `07_databricks_manga_export/lakehouse/`, **complementaire
et consultative** : elle historise le raw multi-snapshots et publie des metriques
de qualite comparees d'un snapshot a l'autre. Elle **n'ecrit jamais dans
PostgreSQL et ne bloque aucune promotion** — les planchers bloquants restent au
module 04.

Medaillon Delta execute en conteneur, versions figees sur la matrice officielle
(Spark 3.5.9 avec delta-spark 3.3.2) :

- **bronze** — ingestion generique parametree par source : Manga Sanctuary
  (volumes et critiques, deux snapshots), Kitsu (catalogue et mappings), Manga
  Insight, Wikidata. Reingestion idempotente (`replaceWhere`) et evolution de
  schema geree (`mergeSchema`) ;
- **silver** — Manga Sanctuary type : EAN valide par le meme algorithme que le
  pipeline, dates francaises normalisees, grain serie par snapshot ;
- **gold** — cinq tables comparables entre snapshots : volumetrie, completude par
  prefixe, remplissage des champs, recouvrement, qualite des EAN.

Un **lecteur externe DuckDB** lit ces memes tables **sans Spark ni JVM** (environ
0,1 seconde) : c'est lui que la revue mensuelle consulte avant promotion.

```bash
cd 07_databricks_manga_export/lakehouse
docker compose run --rm lakehouse pipeline   # bronze -> silver -> gold -> rapport
uv run lakehouse synthese                    # lecteur DuckDB, hors Spark
```

#### Demonstration retrospective

Le rapport genere rejoue trois incidents reels du projet, detectes a l'epoque par
accident et desormais **par systeme** :

| Incident | Metrique gold | Signal mesure |
| --- | --- | --- |
| Reference 2025-12 tronquee | volumetrie et remplissage | critiques +63,76 % ; `review_body` de 47,22 % a 99,99 % |
| Trou de crawl `Di` | completude par prefixe | 9 series disparues, la ou l'ecart net (-1) masquait le trou |
| Volumes perdus par l'ELT | recouvrement | 302 volumes et 22 critiques non revus |

La lecon de methode est inscrite dans la metrique : **l'ecart net ment**, c'est
le compte des disparues qui revele un deficit localise.

**Databricks** n'est plus qu'une cible de **portabilite bonus** : le job bronze
Manga Sanctuary est porte sur Unity Catalog dans `lakehouse/bonus_databricks/`,
versionne mais non execute. Format ouvert, plateforme optionnelle.

## Nettoyage et validation

Le nettoyage est reparti entre plusieurs niveaux.

### `01_scraping_manganews`

Pipeline le plus complet :

- normalisation des textes, slugs et listes ;
- parsing origine / annee ;
- version de schema et version d'enrichissement ;
- timestamp `scraped_at` ;
- construction et controle du texte RAG ;
- validation Great Expectations ;
- import bloque si la validation critique echoue.

Commandes utiles :

```bash
cd 01_scraping_manganews
uv run python scripts/run_all_validations_gx110.py --do-backfill
uv run python scripts/run_prod_import.py --dataset series
uv run python scripts/run_prod_import.py --dataset populaires
```

### `05_nettoyage_agregation_bdd`

Espace de preparation pour explorer, nettoyer et agreger JSON, JSONL et CSV.

Commandes utiles :

```bash
cd 05_nettoyage_agregation_bdd
uv run python -m preparation_bdd json data/sample.jsonl --limit 5
uv run python -m preparation_bdd csv data/sample.csv --head 10
uv run python src/identity/wikidata_dump.py --help
```

Exports Kitsu prepares dans `Preparation_weekly/export/` :

- `kitsu_series_core.csv`
- `kitsu_weekly_snapshot.csv`
- `kitsu_series_authors.csv`
- `kitsu_rag_documents.csv`

## Stockage PostgreSQL

La source de verite du DDL partage est `database/migrations/`, joue par un runner
a checksums. **Toutes les migrations versionnees sont appliquees a la base
`apimanga` ; 0 en attente.**

Deux garanties tenues par le depot :

- **une migration appliquee est immuable** — le runner enregistre un SHA-256 et
  refuse d'avancer si un fichier deja joue change ; toute evolution passe par un
  nouveau fichier ;
- **la reconstruction est verifiee** — `database/outils/fidelite.sh` rejoue
  toutes les migrations sur une base jetable et compare son `pg_dump` a celui
  d'`apimanga`.
  Dernier controle : **diff vide**, le depot reconstruit la base de reference a
  l'identique.

Les donnees collectees **sont chargees** : Manga Sanctuary (14 652 series,
103 811 volumes, 11 052 critiques), le pivot Wikidata (8 214 QID), le catalogue
Kitsu filtre par sous-type (155 003 formes, 74 866 mappings) et Manga Insight
(48 900 sorties, 10 162 series). Les chargeurs sont rejouables : les rejouer ne
change aucun compte.

Les donnees nettoyees alimentent principalement le schema `manga`.

Tables et vues utilisees par l'API :

- `manga.kitsu_series_core`
- `manga.kitsu_weekly_snapshot`
- `manga.kitsu_series_authors`
- `manga.rag_docs_scored`
- `manga.rag_export_docs`

Le schema `bench` porte le corpus de recherche, ses vecteurs, les mesures et la
promotion (etat lu dans la base le 2026-10-12) :

| Objet | Role | Contenu |
| --- | --- | --- |
| `bench.corpus` | Une ligne par version du corpus : regle, decoupage, cloture. | `v1` et `v2`, clos tous les deux. |
| `bench.corpus_docs`, `bench.corpus_chunks` | Documents et fragments, chacun avec son `corpus_id`. | `v1` : 48 090 documents, 66 290 fragments ; `v2` : 56 775 et 77 006. |
| `bench.chunking_strategies` | Strategies de decoupage. | `char_1200_overlap_200` (`v1`), `phrases_1200_recouvrement_200` (`v2`). |
| `bench.encodages` | Registre des encodages : corpus, modele, revision, precision, prefixes, image. | 3 encodages. |
| `bench.vecteurs_bge_m3`, `bench.vecteurs_embeddinggemma` | Vecteurs pgvector, une table par modele, cle `(encodage_id, chunk_id)`. | 66 290 ; 66 290 (`v1`) et 77 006 (`v2`). |
| `bench.eval_jeux`, `bench.eval_questions`, `bench.eval_attendus` | Jeu d'evaluation gele. | Jeu `v2` : 69 questions, 412 attendus sur 328 series. |
| `bench.eval_mesures`, `bench.eval_runs` | Metriques de chaque run ; corpus et encodage de chaque run. Ajout seul. | 1 872 lignes, 18 runs. |
| `bench.promotions`, `bench.v_encodage_en_service` | Journal des promotions, en ajout seul ; vue de l'encodage en service. | 2 decisions ; encodage 3, corpus `v2`. |

Un corpus clos refuse toute ecriture, sauf extension explicite. Les tables du
premier benchmark (module 06) restent dans le schema, non utilisees par la chaine
actuelle : `embedding_models`, `embedding_runs`, `faiss_indexes`, `queries`,
`qrels`, `retrieval_results`, `metrics`.

## Referentiel d'identite multi-source

Le verrou central du projet : **aucune plateforme ne partage d'identifiant avec
une autre**. Manga Sanctuary connait ses `series_id`, Kitsu ses `kitsu_id`,
Wikidata ses QID — aucune jointure directe n'est possible.

La reponse n'est pas du rapprochement flou generalise, mais un **referentiel
pivot d'identifiants (Wikidata)** et une **cascade a etages, du plus sur au plus
faible**, dont chaque decision est journalisee et auditable.

Trois tables portent ce referentiel (migration `001`) :

- `manga.work_identity` — une ligne par oeuvre, ses identifiants externes
  (`wikidata_qid`, `kitsu_id`, `mal_id`, `anilist_id`), unicite garantie par des
  index UNIQUE partiels ;
- `manga.volume_identity` — le volume et son ISBN-13 controle (104 107 lignes,
  dont 63 627 ISBN-13 valides) ;
- `manga.match_decision` — le **journal append-only** des decisions : on n'y fait
  ni `UPDATE` ni `DELETE`, se raviser consiste a inserer une nouvelle decision.
  La vue `manga.v_match_current` expose la decision courante par serie.

### Etages livres

| Etage | Methode | Principe | Standing courant |
| --- | --- | --- | --- |
| 0 | `kitsu_bridge` | Pures jointures d'identifiants : `ms_kitsu_map` x `kitsu_mappings` x `wd_pivot`. Aucune lecture de titre. | 1 688 |
| 1 | `exact` / `exact_author` | Jointure exacte sur forme normalisee, desambiguisee par auteur puis par annee. | 1 627 |
| 2 | `exact_kitsu` / `exact_kitsu_author` | Titre exact MS x Kitsu, confirme/desambigue par le staff et l'annee Kitsu. | 4 980 |
| 3 | `trgm` | Similarite trigramme `pg_trgm` : aucun auto, candidats laisses en revue. | 90 en revue |
| R | `llm_review` / `human_review` | Juge LLM (OpenAI `gpt-5.6-luna`, Batch) en avis-seulement, puis promotion humaine bornee par les mesures (run 2) et correction des faux positifs du socle. | 980 promus, 1 correction |

**Etat : 8 413 series en auto (57,3 % des 14 670), 952 en revue, 1 rejetee ;
10 347 decisions journalisees append-only, 0 doublon. QID sur 3 136 oeuvres,
kitsu_id sur 7 028.** Le regime avis-seulement de l'etage R a ete leve au run 2,
de facon bornee : seuls les verdicts `same_work` de haute confiance, a candidat
unique et sans collision, sont devenus des decisions.

L'univers compte 14 670 series la ou le snapshot `2026-07` en apporte 14 652 :
les 18 fiches restantes viennent du snapshot precedent et sont **volontairement
conservees**. Le chargement est un upsert sans suppression — disparaitre d'une
source n'est pas une preuve d'inexistence.

### Regles de conception

- **Une seule normalisation** — `identity.normaliser()` (Python) des deux cotes
  de tout rapprochement ; jamais de normalisation SQL ad hoc, qui ferait diverger
  les deux cotes en silence.
- **L'annee confirme, elle ne decide jamais seule** ; une annee contradictoire
  interdit la decision automatique.
- **Les collisions d'unicite sont detectees avant insertion** et envoient tout le
  groupe en revue : jamais de resolution par ordre d'arrivee.
- **Idempotence** — une serie deja decidee n'est jamais rejouee ; rejouer un
  etage n'ecrit rien.
- **Le doute ne devient jamais une decision automatique** : il devient un
  `needs_review` trace, avec ses candidats.

Chaque etage produit un entonnoir chiffre case par case, un echantillon de
controle et la liste des cas en revue.

```bash
cd 05_nettoyage_agregation_bdd
uv run python -m identity.pont_kitsu   --dry-run   # etage 0
uv run python -m identity.etage1_exact --dry-run   # etage 1
```

## API

L'API est dans `02_api_manga/`.

Demarrage local :

```bash
cd 02_api_manga
uv sync --all-groups
uv run uvicorn app.main:app --reload --env-file .env
```

Demarrage Docker :

```bash
cd 02_api_manga
docker compose up --build
```

Endpoints principaux :

- `GET /health`
- `GET /kitsu/{kitsu_id}`
- `GET /rag/export`
- `GET /rag/doc/{doc_key}`
- `GET /search?q=...`

## Benchmarks RAG / LLM

Le premier benchmark, `06_benchmark_embeddings_llm/` (FAISS, recall@K, MRR), est
**remplace** depuis le 2026-10-07 par deux modules :

- **`09_service_embedding/`** sert les modeles d'embedding par Text Embeddings
  Inference 1.9.4 : deux instances float32, BGE-M3 (1 024 dimensions) et
  EmbeddingGemma (768 dimensions). Son client applique les prefixes ; son encodeur
  ecrit les vecteurs dans PostgreSQL (pgvector) et inscrit chaque encodage au
  registre `bench.encodages`. Un test d'equivalence verifie que le service rend
  toujours les vecteurs de la base : 200 fragments en 10 tranches de longueur,
  cosinus minimal d'au moins 0,999 pour "equivalent", echec sous 0,99.
- **`10_mesures_recherche/`** mesure la recherche sur le jeu d'evaluation `v2`
  gele (69 questions, 11 familles ; 59 questions portent une metrique de rang),
  au grain de l'oeuvre et non du fragment. Metriques : `hit_rate@5`,
  `hit_rate@10`, `mrr@10`, `ndcg@10`. 17 configurations en trois tours, chaque
  tour declare avant d'etre execute ; chaque run est enregistre dans MLflow
  (local) et dans `bench.eval_mesures`.

Aucun reglage n'est change apres lecture d'un resultat : un changement est une
nouvelle mesure.

### Corpus v2, promotion et retour arriere

Deux versions du corpus coexistent dans `bench` (migration `021`), closes toutes
les deux :

| | Corpus `v1` | Corpus `v2` |
| --- | --- | --- |
| Documents | 48 090 : 11 041 critiques, 37 049 synopsis Kitsu | 56 775 : les memes, plus 8 685 resumes de serie |
| Fragments | 66 290, decoupes par caracteres | 77 006, decoupes par phrases |
| Series du catalogue atteignables, sur 14 670 | 8 718 | 11 155 |
| Encodage EmbeddingGemma | encodage 2 | encodage 3, **en service** |
| Mesure 8 (EmbeddingGemma, classement limite au catalogue) : questions reussies a dix resultats, sur 59 | 31 | 31 |
| dont reconnaissance, sur 23 | 21 | 21 |
| dont proposition, sur 36 | 10 | 10 |

- **Ce que le `v2` change** : les resumes de serie de Manga Sanctuary s'ajoutent,
  le decoupage suit les phrases, et le texte des critiques n'est plus masque —
  seul le nom de leur auteur reste anonymise.
- **La regle de promotion est ecrite et commitee avant la mesure**
  (`10_mesures_recherche/config/promotion_corpus_v2.toml`) : au moins 31
  questions sur 59 au global, et au moins 20 sur 23 en reconnaissance. Elle
  compte en questions, pas en pourcentages. Une seule mesure est faite, comparee
  a la mesure 8 du `v1`.
- **Le resultat** : 31 et 21, donc "promotion proposee". L'hypothese d'un gain
  en proposition (au moins 12 sur 36) est dementie : 10, comme sur le `v1`. Le
  `v2` ne gagne aucune question nette ; il est promu pour sa portee et son
  decoupage.
- **La decision est humaine** : elle est inscrite au journal `bench.promotions`
  (2026-10-10), en ajout seul. La vue `bench.v_encodage_en_service` designe
  l'encodage que lisent, par defaut, les mesures et les controles.
- **Le retour arriere est une commande, et une ligne au journal.** Aucun vecteur
  n'est efface : les deux encodages restent en base. La procedure a ete essayee
  sur une copie jetable de la base.

```bash
cd 10_mesures_recherche
uv run python -m mesures_recherche.journal_promotions etat
uv run python -m mesures_recherche.journal_promotions revenir --motif "..." --par <decideur>
```

## Reproductibilite

Chaque module contient son propre `README.md`, ses dependances et ses commandes
d'execution. Les artefacts lourds et secrets locaux restent ignores par Git :

- fichiers `.env` ;
- exports volumineux ;
- checkpoints de crawl ;
- sorties FAISS ;
- tables Delta du lakehouse, reconstructibles depuis le raw.

La procedure executable par un tiers est dans **[`INSTALLATION.md`](INSTALLATION.md)**.
Elle distingue la verification C4, autonome sur une fixture jetable, de
l'installation de production, qui suppose les snapshots et les autorisations de
collecte.

La methode de travail est dans **[`CONTRIBUTING.md`](CONTRIBUTING.md)** : branches, commits, fusions, etiquettes.

### Verification : `make verify`

Une seule commande verifie le depot, sur le poste comme sur GitHub :

```bash
make verify              # chaque module : Ruff, format, tests ; puis les workflows
make verify MODULE=10    # un seul module : 01 ... 10, database, demo, workflows
make verify-indicatif    # Bandit et pip-audit, jamais exiges
make verify-poste        # make verify, puis les harnais du poste seul
```

- **"Tests verts" veut dire `make verify` sans echec.** Un test saute n'est pas
  un test passe : il est compte, et plafonne par module dans
  `verification/sauts_attendus.tsv`. Au-dela du plafond, la verification echoue.
- Aucune suite ne recoit l'adresse de la base reelle : les tests qui ont besoin
  de PostgreSQL lancent une base jetable par Docker.
- Sur GitHub, la chaine `verify` joue une tache par module : 13 controles,
  obligatoires avant toute fusion vers `develop` ou `main`.
- `make verify-poste` ajoute les hooks, la fidelite du schema, l'equivalence du
  service d'embedding et l'integration Compose de l'API.

## Demonstration

Le deroule complet est dans **`GUIDE_PIPELINE.md`** : le pipeline ELT commande
par commande — avec, pour chaque etape, ce qu'elle affiche, comment verifier
qu'elle a reussi et la phrase d'explication — puis un **scenario de demonstration
de 10 minutes** et une **FAQ jury** (ELT ou ETL, Spark sans compte, pourquoi pas
Databricks, idempotence, fidelite du schema).

La console `manga-pipeline` (`demo/`) en est la telecommande : un **ecran d'etat**
reel (migrations, volumetrie, couverture d'identite, snapshots du raw, lakehouse,
dernier commit) puis un menu de **17 actions groupees par phase ELT**. Elle
**orchestre les CLI existantes** et n'implemente aucune logique metier : la
commande exacte est affichee avant chaque execution, donc le jury voit la vraie
commande. Mode **lecture seule par defaut** — les actions qui ecrivent en base ne
sont pas proposees.

```bash
cd demo && uv sync
DATABASE_URL='postgresql://postgres@localhost:5432/apimanga' uv run manga-pipeline
```

## Points de vigilance

- Les notebooks de nettoyage Kitsu et Manga Sanctuary contiennent encore une
  partie importante de la logique metier ; leur extraction vers des modules Python
  et une CLI reste en cours.
- La centralisation PostgreSQL est **etablie** : toutes les migrations appliquees, schema
  reconstructible depuis le depot et fidelite verifiee. La migration `000` est une
  baseline d'heritage, enregistree sans etre executee sur `apimanga` : elle date
  le constat, elle ne reconstruit pas l'historique.
- La cascade d'identite couvre **57,3 %** des series en auto (etages 0 a R livres,
  jusqu'a la promotion decisionnelle du run 2). Restent a la main humaine :
  l'arbitrage des cas residuels (undecidable, conflits multi-candidats, collisions
  d'identite), la decision sur les `same_work` de confiance moyenne, une eventuelle
  v2. Le doute n'est jamais une decision automatique.
- L'API ne sert pas encore le corpus `v2` : `/rag/export` lit la vue
  `manga.rag_export_docs` (51 880 documents), qui ne lit pas `bench`.
- Le renommage des dossiers et du package `api_manga` est gele jusqu'a une fenetre
  atomique dediee, avant l'orchestrateur.
- Les rapports de validation doivent etre regeneres apres chaque collecte
  importante pour garder des preuves a jour.
