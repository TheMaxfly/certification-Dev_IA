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
uv run --extra dev pytest tests/         # suite sur base jetable (Docker)
```

## Ce qui est livré

| Migration | Contenu |
|---|---|
| `001_socle_identite.sql` | schémas `manga` et `staging` ; `work_identity`, `volume_identity`, `match_decision` + vue `v_match_current` |
| `002_staging_referentiels.sql` | staging des référentiels : `wd_*` (pivot Wikidata), `kitsu_formes`, `mi_sorties` / `mi_series` (Manga Insight) |

## État de la base réelle

Les migrations `001` et `002` ont été **appliquées avec succès à `apimanga` le
2026-07-15**. Le contrôle final affiche **2 migrations appliquées et 0 en attente**.

Seules les structures SQL ont été créées : aucune donnée issue de Manga-News,
Kitsu, Manga Sanctuary, Wikidata ou Manga Insight n'a encore été chargée par ces
migrations. Les fichiers `001/002` sont désormais immuables ; toute évolution doit
être ajoutée dans une migration `003` ou suivante.

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

## Tests

`uv run --extra dev pytest tests/` — 29 tests. Le harnais lance un PostgreSQL
**jetable** en conteneur et crée une base neuve par test. Si Docker est absent,
les tests **skippent avec un message** : ils ne se rabattent jamais sur une base
réelle, et `apimanga` n'est jamais atteignable depuis la suite.

Couverture : application sur base vide, idempotence, `--target`, ordre
lexicographique, dérive de checksum, rollback d'un fichier en échec, survie des
migrations précédentes, puis les CHECK/index livrés par `001` et la forme des
tables de `002`.

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

## À suivre

Le snapshot Manga Sanctuary `2026-07` est maintenant promu. La prochaine migration
`003` portera les évolutions des tables `ms_*` nécessaires à son chargement. Elle
devra être ajoutée comme nouveau fichier : **ne jamais modifier `001` ou `002`**.
