# database — migrations PostgreSQL versionnées

Schéma de la base `apimanga` en **fichiers SQL versionnés**, joués par un runner
minimal. Objectif : que le schéma soit reconstructible depuis le dépôt, et que
l'écart entre le dépôt et une base réelle soit **détectable**.

## Usage

```bash
export DATABASE_URL='postgresql://postgres@localhost:5432/apimanga' # mot de passe via ~/.pgpass
uv run python migrate.py status          # appliquées / en attente
uv run python migrate.py up              # applique tout ce qui manque
uv run python migrate.py up --target 001 # s'arrête à 001 incluse
uv run python migrate.py mark-applied 000 # enregistre SANS exécuter (cf. 000)
uv run --extra dev pytest tests/         # suite sur base jetable (Docker)
```

## Ce qui est livré

| Migration | Contenu |
|---|---|
| `000_baseline.sql` | **baseline d'héritage** : le schéma qui préexistait au versionnement — `ms_*`, `kitsu_*`, `rag_*` et les vues RAG (module 05), schéma `bench` (module 06) |
| `001_socle_identite.sql` | schémas `manga` et `staging` ; `work_identity`, `volume_identity`, `match_decision` + vue `v_match_current` |
| `002_staging_referentiels.sql` | staging des référentiels : `wd_*` (pivot Wikidata), `kitsu_formes`, `mi_sorties` / `mi_series` (Manga Insight) |
| `003_evolution_ms.sql` | évolution `ms_*` pour le snapshot 2026-07 : `volume_ean`, `work_uid` (moyeu), `review_grain` ; table `ms_formes` + index trigramme |
| `004_staging_ms.sql` | staging du snapshot Manga Sanctuary : `staging.ms_volumes` (39 clés), `staging.ms_reviews` (12 clés) |
| `005_unicite_review_url.sql` | index UNIQUE partiel sur `ms_reviews_all.review_url` — la clé d'upsert du cycle mensuel |
| `006_referentiels.sql` | référentiels typés de la cascade : `staging.kitsu_mappings` ; `manga.wd_pivot` / `wd_formes` / `wd_auteurs` ; `manga.kitsu_mappings` / `kitsu_formes` |
| `007_referentiel_mi.sql` | Manga Insight typé : `manga.mi_sorties` / `mi_series` + vue `v_mi_ean_multiples` |

## `000` — la frontière héritage / versionné

`apimanga` a été construite **avant** que son schéma ne soit versionné. Les tables
du module 05 et le schéma `bench` du module 06 existaient déjà quand `001` est
arrivée — c'est pourquoi `001` crée ses schémas en `IF NOT EXISTS`. Rien dans le
dépôt ne les décrivait : une base neuve rejouant `001` et `002` **n'obtenait pas
`apimanga`**, et aucun contrôle ne le signalait.

`000` ferme cet écart. C'est un fichier **généré** (`pg_dump --schema-only`), dont
le périmètre est défini **par soustraction** : tout ce que `001`, `002` et le
runner ne créent pas. Il ne porte **aucune** `CREATE EXTENSION` — l'héritage n'en
requiert aucune : les index GIN de `manga` portent sur du `jsonb` ou sur
`to_tsvector(...)`, jamais sur des trigrammes.

Il s'applique **différemment selon la base** :

- **base neuve** (tests, reconstruction) : `up` la joue normalement, puis `001`,
  `002`, … La base obtenue est enfin celle du dépôt.
- **`apimanga`** : ces objets y existent déjà, avec leurs données. L'y rejouer
  échouerait, et c'est voulu — d'où l'absence d'`IF NOT EXISTS`, qui masquerait
  les divergences que `000` doit rendre visibles. On l'y a **enregistrée sans
  l'exécuter**, via `mark-applied`.

`mark-applied` dit au runner qu'un état est atteint sans l'avoir produit. C'est
son seul emploi légitime, et il est réservé à ce cas.

## État de la base réelle

`001`, `002` et `003` ont été **appliquées à `apimanga` le 2026-07-15**, `004`
à `007` le 2026-07-16 ; `000` y a été **marquée appliquée** le 2026-07-15, sans
exécution. Le contrôle final affiche **8 migrations appliquées et 0 en
attente**.

`applied_at` de `000` est plus **récent** que celui de `001`/`002` alors que sa
version est plus ancienne : la baseline date le constat, pas la construction.

Aucune de ces migrations ne charge de données — elles ne créent que des
structures. Le chargement du snapshot est le travail du chargeur (cf. plus bas).
Les fichiers `000` à `007` sont désormais immuables.

### Preuve de fidélité

La baseline ne vaut que si elle ne ment pas. Contrôle rejoué le 2026-07-16 :
`pg_dump --schema-only` (schémas `manga`, `bench`, `staging`) d'une base jetable
reconstruite par `up` **000→007**, comparé au même dump d'`apimanga`. Diff
normalisé (hors commentaires, préambule `SET`, OWNER/GRANT) : **vide**, 1 237
lignes de part et d'autre, y compris après tri. La base reconstruite depuis le
dépôt est identique à la base réelle.

À rejouer après toute migration touchant le schéma :

```bash
bash outils/fidelite.sh   # rejeu 000→NNN sur base jetable, diff contre apimanga
```

## Règles

**Une migration appliquée est immuable.** Le runner enregistre un checksum
SHA-256 ; si un fichier déjà joué change, il refuse d'avancer plutôt que de
rejouer silencieusement. Pour corriger, on ajoute `NNN+1`.

**Pas de `down`.** Un rollback automatique donne une fausse sécurité : il ne sait
pas restaurer les données détruites par le `up`, et l'écrire coûte à chaque
migration pour un cas qui ne se présente presque jamais. On avance par
**migrations correctives**, testées comme les autres. En cas de vrai besoin de
retour arrière : restauration depuis sauvegarde, décision humaine.

**Une transaction par fichier.** Un échec annule le fichier entier et arrête le
runner ; les migrations déjà appliquées restent en place. C'est ce que garantit
la connexion en autocommit — sans elle, tout le run tiendrait dans une seule
transaction et un échec tardif annulerait aussi les migrations précédentes.

**Le staging est en TEXT partout**, sans contraintes ni FK : un fichier source
ne doit jamais faire échouer un chargement sur une question de type. Le typage
et les filtres (dont le `subtype` Kitsu) se font à la **promotion**.

## Application réelle sur `apimanga` — 2026-07-15

```console
$ DATABASE_URL='postgresql://postgres@localhost:5432/apimanga' uv run python migrate.py status
 version  état         fichier
------------------------------------------------------------
     001  en attente   001_socle_identite.sql
     002  en attente   002_staging_referentiels.sql
------------------------------------------------------------
0 appliquée(s), 2 en attente

$ DATABASE_URL='postgresql://postgres@localhost:5432/apimanga' uv run python migrate.py up
→ application de 001_socle_identite.sql ...
  ✓ 001 appliquée
→ application de 002_staging_referentiels.sql ...
  ✓ 002 appliquée
2 migration(s) appliquée(s).

$ DATABASE_URL='postgresql://postgres@localhost:5432/apimanga' uv run python migrate.py status
 version  état         fichier
------------------------------------------------------------
     001  appliquée    001_socle_identite.sql
     002  appliquée    002_staging_referentiels.sql
------------------------------------------------------------
2 appliquée(s), 0 en attente
```

Objets créés : `manga.match_decision`, `manga.volume_identity`,
`manga.work_identity` (+ `manga.v_match_current`) ; `staging.kitsu_formes`,
`staging.mi_series` (37 col.), `staging.mi_sorties` (34 col.),
`staging.wd_auteurs`, `staging.wd_entities`, `staging.wd_formes`,
`staging.wd_pivot`.

### Marquage de `000` — 2026-07-15

`000` a été ajoutée après coup, et **enregistrée sans être exécutée** : ses objets
étaient déjà là depuis le module 05. Aucun DDL n'a tourné, aucune donnée n'a
bougé (l'état d'alors : 13 208 séries / 89 129 volumes / 6 749 critiques).

```console
$ DATABASE_URL='postgresql://postgres@localhost:5432/apimanga' uv run python migrate.py mark-applied 000
→ 000 (000_baseline.sql) marquée appliquée.
  Le SQL n'a PAS été exécuté : ses objets sont supposés déjà en base.

$ DATABASE_URL='postgresql://postgres@localhost:5432/apimanga' uv run python migrate.py status
 version  état         fichier
------------------------------------------------------------
     000  appliquée    000_baseline.sql
     001  appliquée    001_socle_identite.sql
     002  appliquée    002_staging_referentiels.sql
------------------------------------------------------------
3 appliquée(s), 0 en attente
```

## Tests

`uv run --extra dev pytest tests/` — 91 tests. Le harnais lance un PostgreSQL
**jetable** en conteneur et crée une base neuve par test. Si Docker est absent,
les tests **skippent avec un message** : ils ne se rabattent jamais sur une base
réelle, et `apimanga` n'est jamais atteignable depuis la suite.

Couverture : application sur base vide (le rejeu part de `000`), idempotence,
`--target`, ordre lexicographique, dérive de checksum, rollback d'un fichier en
échec, survie des migrations précédentes ; `mark-applied` (enregistre sans
exécuter, refus si version inconnue ou déjà enregistrée, checksum du fichier,
`up` qui ne rejoue pas une migration marquée) ; la reconstruction de l'héritage
par `000` et sa frontière avec `001` ; les CHECK/index livrés par `001` et la
forme des tables de `002` ; les colonnes, contraintes et index de `003`, dont
l'usabilité réelle de l'index trigramme (l'opérateur `%` est exercé).

Les garde-fous ont été vérifiés **par mutation** : la garde retirée, le test
correspondant doit virer au rouge — 4/4 pour `mark-applied`, 5/5 pour les
contraintes de `003`. Un test qui reste vert sur du code cassé ne prouve rien.

## Notes sur `002`

Les colonnes sont calquées sur les **fichiers réels**, pas sur leur
documentation :

- `wd_*` : en-têtes exacts des CSV de `wikidata_dump.py` (module 05).
- **Manga Insight** : un seul parquet (59 062 × 43) contenant **deux
  populations**, séparées sur `Original Url` — vide ⇒ **A**, grain
  sortie/volume (48 900 lignes) ; rempli ⇒ **B**, grain série (10 162 lignes).
  Chaque table ne porte que les colonnes alimentées pour sa population. `Ean`
  est à 96,5 % en A et 0 % en B : l'EAN appartient au grain sortie. A utilise
  `Titre`, B utilise `Title`. `Unnamed: 19`, vide à 100 %, est la seule des 43
  colonnes écartée. La correspondance nom parquet → nom SQL est documentée en
  commentaire dans la migration.

## Notes sur `003`

Le DDL est calqué sur le schéma **réel**, inspecté avant écriture. Quatre
colonnes de la spécification initiale existaient déjà et n'ont donc **pas** été
recréées : `ms_reviews_all.volume_number` (`integer`), `ms_reviews_all.review_type`
(`text`), `ms_series_enriched.series_genres` et `series_tags` (déjà en `jsonb`).

Deux points structurants, décidés à partir du profilage du snapshot :

- **`volume_ean` est TEXT et brut.** 61,90 % des 103 811 volumes en portent un,
  dont 99,02 % sont des EAN-13 valides — le ~1 % restant doit pouvoir entrer pour
  être vu. L'ISBN-13 typé et son contrôle de clé vivent dans `volume_identity`
  (001) : cette colonne est la matière première, pas sa lecture.
- **`ms_formes.langue` reste NULL côté Manga Sanctuary.** La source
  (`series_other_titles`) est une liste plate de chaînes **sans langue**. On
  aurait pu inférer la langue de l'écriture (53,92 % des alias en CJK, 45,26 % en
  latin), mais une inférence rangée dans une colonne « langue » devient une donnée
  source aux yeux du lecteur suivant. La colonne existe pour les sources qui la
  déclarent (Wikidata, Kitsu).

`review_grain` est aujourd'hui constante (`'volume'` : les 11 052 critiques du
snapshot sont toutes ancrées sur un tome). Elle est créée quand même, avec
`'serie'` autorisé par le CHECK : le jour où un avis au grain série arrivera, la
structure l'accueillera sans migration.

## Notes sur `004` / `005`

`004` calque `staging.ms_*` sur les clés **réelles** des fichiers. Deux points :

- **Une colonne dérivée par table** (`volume_publication_date_iso`,
  `review_date_iso`), calculée par le chargeur et non lue du fichier. Le staging
  reste tout-TEXT — une date ISO est du texte — mais la promotion peut la caster
  par un simple `::date`. Le parsing des mois français appartient à Python
  (`identity.dates`), pour la même raison que la normalisation des titres :
  `to_date('27 nov. 2012', 'DD mon YYYY')` dépend du `lc_time` du serveur et
  rendrait le résultat dépendant de la machine.
- **`volume_members_rating` est chargée mais non promue** : `ms_volumes_enriched`
  n'a pas de colonne pour elle, alors que `volume_experts_rating` existe. Écart du
  schéma historique, pas du snapshot. La valeur reste dans le staging et dans le
  raw ; lui ouvrir une colonne est une décision d'évolution à part.

`005` ajoute l'unicité de `review_url`. Sans elle, la promotion des critiques ne
peut pas être un upsert : `ms_reviews_all` a pour PK `review_id`, une séquence —
un identifiant technique qui ne dit rien de l'identité d'une critique.

## Chargement du snapshot Manga Sanctuary

Le chargeur vit dans le module 05, auprès de `normaliser()` :

```bash
cd 05_nettoyage_agregation_bdd
export DATABASE_URL='postgresql://postgres@localhost:5432/apimanga'
uv run python -m identity.charger_ms                       # snapshot 2026-07
uv run python -m identity.charger_ms --snapshot chemin/    # un autre
```

**Upsert, jamais de DELETE.** Une fiche absente du snapshot du mois reste en base :
disparaître de Manga Sanctuary n'est pas une preuve d'inexistence. Le chargement
2026-07 a laissé 296 volumes, 22 critiques et 18 séries de 2025-12 intacts.

Chargement réel du 2026-07 sur `apimanga` (2026-07-16, 21 s) : 14 652 séries /
103 811 volumes / 11 052 critiques promues ; 14 652 `title` + 17 252 alias dans
`ms_formes` ; 104 107 lignes dans `volume_identity`, dont 63 627 `isbn13` valides.
Rejouer le chargement ne change aucun compte.

## Notes sur `006` — les référentiels de la cascade

002 a posé un staging tout-TEXT pour Wikidata et Kitsu, et c'était le bon geste.
Mais le staging est **TRONQUÉ à chaque chargement** : la cascade, qui joint ces
référentiels à chaque décision, ne peut pas s'appuyer dessus. D'où le miroir
typé et durable dans `manga` — staging = ce que le fichier dit, `manga` = ce sur
quoi on décide.

Il manquait par ailleurs **toute table pour les mappings Kitsu**. Vérifié avant
d'écrire : ni le dépôt ni l'héritage n'en avaient. C'est l'étage `kitsu_bridge`
de la cascade (cf. le CHECK de `match_decision` en 001) qui n'existait pas — sans
lui, le pivot Wikidata, qui ne connaît que `mal_id` et `anilist_id`, ne peut pas
rejoindre Kitsu.

**Une seule normalisation, des deux côtés.** `forme_norm` vient de
`identity.normaliser()` pour `ms_formes` (B2) comme pour `wd_formes` et
`kitsu_formes`. Une jointure d'égalité entre deux colonnes normalisées par deux
implémentations différentes renverrait « pas de match » là où les titres sont les
mêmes — en silence. Les trois tables portent le même outillage : index btree +
GIN trigramme sur `forme_norm`.

`006` ne double pas `manga.kitsu_series_core` (héritage) : celle-ci reste la
fiche Kitsu, `kitsu_formes` ne porte que des cibles de matching. Attention, ses
`title_norm_*` viennent de l'ancien code du module 05 et ne sont pas garantis
identiques à `forme_norm` — la cascade doit lire `kitsu_formes`.

## Chargement des référentiels

```bash
cd 05_nettoyage_agregation_bdd
export DATABASE_URL='postgresql://postgres@localhost:5432/apimanga'
uv run python -m identity.charger_wikidata   # CSV -> manga.wd_*
uv run python -m identity.charger_kitsu      # ndjson -> manga.kitsu_*
```

Chargement réel du 2026-07-16 : **8 214** `wd_pivot`, **26 103** `wd_formes`
(28 668 lues, 2 565 collisions normalisées), **5 453** `wd_auteurs` en 2,1 s ;
**155 003** `kitsu_formes` couvrant les **41 249** entrées de la cible et
**74 866** `kitsu_mappings` retenus sur 104 726 bruts en 15,1 s. Rejouer ne
change aucun compte.

Résultat qui compte : **5 963 qid Wikidata rejoignent désormais 5 973 kitsu_id**
par les mappings. C'est le pont que la cascade attendait.

## Notes sur `007` — Manga Insight, et une décision révisée

`007` complète `006` : il restait Manga Insight, dont le parquet était
introuvable au moment de l'écriture de `006`.

**« Upsert clé EAN » a été abandonné, mesures à l'appui.** La décision reposait
sur l'idée que l'EAN identifie une sortie. Le fichier réel dit le contraire :
3,52 % des lignes n'ont aucun EAN, et 534 EAN portent plusieurs sorties. Un
upsert de clé EAN aurait gardé 46 492 des 48 900 lignes — **2 408 perdues
(4,92 %), en silence**. Certains doublons sont de vraies rééditions (trois
éditions de « NonNonBa » partagent un EAN, comme les coffrets dans
`volume_identity`), d'autres sont des **erreurs de la source** : l'EAN
`9782368773512` porte « Comment lui avouer !? » et « Egregor - Collector Vol.1 ».
Aucune clé naturelle n'existe : seule la ligne entière est unique.

D'où **PK technique + rechargement complet**. La table EST le snapshot du mois ;
l'historique vit dans le raw daté et immuable. Ce n'est pas le `DELETE` interdit
par le cycle mensuel : `mi_*` n'a aucune FK entrante, n'est pas un référentiel,
et rien n'y est irrécupérable. Le rechargement est protégé par un **plancher de
volumétrie** : un snapshot sous 90 % du compte en base annule tout.

La vue `v_mi_ean_multiples` rend visibles les 534 EAN partagés, dont 47 portent
des titres divergents. Ce drapeau dit « probable », et pas plus : parmi ces 47,
il y a de vraies erreurs (deux œuvres distinctes) et de simples variantes de
libellé (« Coffret 4 - Water Seven » / « Coffret vide Water Seven Vol.4 »).

## Acquisition et chargement de Manga Insight

```bash
cd 05_nettoyage_agregation_bdd
uv run python -m identity.acquerir_mi            # HF -> data/raw/mi/<mois>/ + MANIFEST
export DATABASE_URL='postgresql://postgres@localhost:5432/apimanga'
uv run python -m identity.charger_mi             # raw daté -> manga.mi_*
```

L'acquisition **refuse d'écraser** un raw daté existant : pour rafraîchir, on
change de date. Le chargeur **refuse un raw sans MANIFEST** — des données sans
provenance n'entrent pas en base.

Chargement réel du 2026-07-16 : **48 900** sorties + **10 162** séries en 2,5 s ;
47 028 EAN valides, 151 faux, 1 721 absents. Rejouer ne change aucun compte.

**Recouvrement EAN Manga Insight ↔ `volume_identity.isbn13`** (métrique
récurrente, première mesure) : **38 561 EAN communs**, soit 83,20 % des 46 348
EAN valides de MI et 60,72 % des 63 511 ISBN-13 de Manga Sanctuary.

## À suivre

**Étape C — la cascade** : remplir `work_uid` et journaliser les décisions dans
`match_decision`, en s'appuyant sur les trois tables de formes (`ms_formes`,
`wd_formes`, `kitsu_formes` — même normalisation, même outillage d'index) et le
pont Wikidata → Kitsu.

Toute évolution doit être ajoutée comme nouveau fichier : **ne jamais modifier une
migration déjà appliquée**.
