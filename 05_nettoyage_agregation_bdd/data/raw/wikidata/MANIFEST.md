# Wikidata — raw du pivot d'identité

Fichiers **immuables**. Pour rafraîchir la source, créer un dossier daté voisin ;
ne jamais réécrire celui-ci.

> **Un raw Wikidata n'est pas reproductible à l'identique.** Le résultat d'une
> requête SPARQL dépend de l'état de la base au moment de l'appel : relancer
> l'extraction aujourd'hui ne rendrait pas ces fichiers. Ce manifeste porte donc
> **ce qui a été demandé** autant que ce qui a été reçu — sans la requête, les
> octets ne sont pas interprétables.

Un seul manifeste couvre les deux dossiers datés : ce ne sont pas deux
instantanés concurrents mais **deux étapes d'une même acquisition** — le pivot
des œuvres le 2026-07-14, l'hydratation des auteurs le 2026-07-18, la seconde
n'étant possible qu'une fois la première chargée en base.

## Inventaire

| Répertoire | Contenu | Fichiers | Octets | Unités | Empreinte d'ensemble (SHA-256) |
|---|---|---:|---:|---:|---|
| `2026-07-14/pages/` | Pages SPARQL | 4 | 3 199 400 | 15 955 bindings | `1e3267f26c1710f9a5d79a67287ea742b06da4ecabb34fbdde73c1bb9e931b98` |
| `2026-07-14/entities/` | Entités d'œuvres | 165 | 107 844 402 | 8 214 entités | `683e9f52274592b52949650ab7f92910b1bd31abc757a0e7f8e27848578b29ff` |
| `2026-07-18/auteurs/` | Entités d'auteurs | 56 | 793 665 | 2 780 entités | `5cfaabc5969890ce4556d1ad4e14ec145101a02423df6e840a047493d6ba253c` |

**Total : 225 fichiers JSON, 111 837 467 octets.**

L'**empreinte d'ensemble** est le SHA-256 de la liste triée des empreintes
individuelles : un seul fichier modifié, ajouté ou retiré la change. Le détail
fichier par fichier est dans [`MANIFEST_FICHIERS.txt`](MANIFEST_FICHIERS.txt),
au format `sha256sum` — donc directement rejouable.

## Provenance — ce qui a été demandé

### Étape 1 — pivot SPARQL (`2026-07-14/pages/`)

Point d'accès `https://query.wikidata.org/sparql`
([`wikidata_dump.py:37`](../../../src/identity/wikidata_dump.py#L37)).
Requête à [`wikidata_dump.py:49-57`](../../../src/identity/wikidata_dump.py#L49-L57) :

```sparql
SELECT ?item ?mal ?anilist WHERE {
  { ?item wdt:P4087 ?mal }
  UNION
  { ?item wdt:P8731 ?anilist }
}
ORDER BY ?item
LIMIT {limit} OFFSET {offset}
```

| Propriété visée | Sens |
|---|---|
| `P4087` | identifiant MyAnimeList (manga) |
| `P8731` | identifiant AniList (manga) |

C'est une **union, pas une intersection** : une œuvre entre dans le pivot si
elle porte l'un *ou* l'autre identifiant. D'où 15 955 bindings pour
8 214 QID distincts après consolidation — une même œuvre apparaît deux fois
lorsqu'elle porte les deux.

Pagination `PAGE_SIZE = 5000`
([`:40`](../../../src/identity/wikidata_dump.py#L40)), arrêt quand une page rend
moins que la taille demandée ([`:97`](../../../src/identity/wikidata_dump.py#L97))
— d'où 5 000 + 5 000 + 5 000 + 955 sur 4 pages.

### Étape 2 — hydratation des œuvres (`2026-07-14/entities/`)

`wbgetentities` sur `https://www.wikidata.org/w/api.php`
([`:38`](../../../src/identity/wikidata_dump.py#L38)), par lots de
`BATCH_SIZE = 50` — le maximum autorisé sans authentification
([`:41`](../../../src/identity/wikidata_dump.py#L41)).

| Paramètre | Valeur | Ligne |
|---|---|---|
| `props` | `labels\|aliases\|claims\|sitelinks` | [`:153`](../../../src/identity/wikidata_dump.py#L153) |
| `languages` | `fr\|en\|ja` | [`:154`](../../../src/identity/wikidata_dump.py#L154) |

### Étape 3 — hydratation des auteurs (`2026-07-18/auteurs/`)

Mêmes API et lots, mais **demande volontairement plus étroite** :

| Paramètre | Valeur | Ligne |
|---|---|---|
| `props` | `labels\|aliases` — ni `claims` ni `sitelinks` | [`hydrater_auteurs.py:124`](../../../src/identity/hydrater_auteurs.py#L124) |
| `languages` | `ja\|en\|fr` | [`:125`](../../../src/identity/hydrater_auteurs.py#L125) |

**C'est ici que porte la minimisation, et c'est ici qu'elle compte** : les
entités de cette étape sont des **personnes physiques**, là où celles de l'étape
2 sont des œuvres. Le projet ne demande que des noms et des alias — pas de date
de naissance, pas de nationalité, pas de liens de projet, alors que `claims` les
aurait rendus. La restriction à trois langues borne d'autant la collecte. Le
commentaire du code l'énonce comme un choix, pas comme un effet de bord.

### Politesse réseau (commune)

| | Valeur | Ligne |
|---|---|---|
| User-Agent | nominatif, avec adresse de contact (politique Wikimedia) | [`:34-35`](../../../src/identity/wikidata_dump.py#L34-L35) |
| Pause entre appels | `PAUSE_S = 1.5` s | [`:42`](../../../src/identity/wikidata_dump.py#L42) |
| Tentatives | `MAX_RETRIES = 5`, backoff honorant `Retry-After` | [`:43`](../../../src/identity/wikidata_dump.py#L43), [`:69-74`](../../../src/identity/wikidata_dump.py#L69-L74) |

## Date de collecte

Portée par le **nom des dossiers** — `2026-07-14` pour le pivot et les œuvres,
`2026-07-18` pour les auteurs. Aucune autre datation n'existe : les fichiers ne
contiennent pas d'horodatage de réponse et aucun `run.json` n'accompagne cette
source. Les dates ne sont donc **pas reconstituées ici**.

## Ce que ce manifeste ne sait pas

- **Aucune durée enregistrée.** `hydrater_auteurs` chronomètre son exécution et
  l'affiche ([`hydrater_auteurs.py:139`](../../../src/identity/hydrater_auteurs.py#L139)),
  mais rien ne la persiste ; `wikidata_dump` ne mesure rien du tout.
- **Aucun point de reprise intra-étape pour SPARQL.** `extract()` repart
  toujours de l'offset 0 ([`wikidata_dump.py:84`](../../../src/identity/wikidata_dump.py#L84)) :
  une interruption en cours d'extraction fait tout recommencer, même si les
  pages déjà écrites sont sur le disque. Les deux étapes d'hydratation, elles,
  reprennent par fichier existant ([`:146`](../../../src/identity/wikidata_dump.py#L146),
  [`hydrater_auteurs.py:115`](../../../src/identity/hydrater_auteurs.py#L115)).
- **Aucune trace de l'état de Wikidata au moment de l'appel** — ni horodatage de
  requête, ni numéro de révision. C'est la limite structurelle rappelée en tête :
  ces fichiers ne sont pas reproductibles, seulement vérifiables.

## Vérifier l'intégrité

Fichier par fichier, depuis ce répertoire :

```bash
cd 05_nettoyage_agregation_bdd/data/raw/wikidata
sha256sum -c MANIFEST_FICHIERS.txt
```

Empreinte d'ensemble d'un répertoire — doit rendre la valeur du tableau :

```bash
cd 2026-07-14/entities
LC_ALL=C ls *.json | LC_ALL=C sort | tr '\n' '\0' | xargs -0 sha256sum | sha256sum
```
