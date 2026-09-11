# 08 — Enrichissement personnages

Deux collectes de personnages, une seule ossature : **Wikipédia français** et
**AniList**. Le module produit un *raw* immuable, hors dépôt, et les artefacts
qui permettent de le vérifier sans y accéder.

> **Ce que le dépôt porte est le livrable.** Le raw ne part pas au dépôt — la
> seule source Kitsu pèse 126 Mo. Ce qu'un lecteur voit de cette collecte, c'est
> le code, le manifeste, ce README et les rapports. Le manifeste n'est pas un
> accessoire : c'est la seule preuve de ce qui a été collecté.

## Pourquoi un module et non deux

Quatre composants sont communs aux deux sources et n'existent qu'en un
exemplaire. Si l'un finissait dupliqué dans les collecteurs, le choix du module
unique aurait échoué.

| Composant | Fichier | Ce qu'il mutualise |
|---|---|---|
| Dérivation du périmètre | [commun/perimetre.py](commun/perimetre.py) | même table, même clé catalogue ; seule change la colonne source |
| Fusion du manifeste | [commun/manifeste.py](commun/manifeste.py) | un run partiel complète, n'écrase jamais |
| Limiteur de cadence | [commun/limiteur.py](commun/limiteur.py) | deux cadences, un seul mécanisme |
| Enveloppe de run et reprise | [commun/enveloppe.py](commun/enveloppe.py) + [commun/collecteur.py](commun/collecteur.py) | même sémantique d'interruption |

Chaque collecteur n'apporte que trois choses : son nom, sa cadence, et sa
fonction de récupération.

## Arborescence

```
08_enrichissement_personnages/
├── collecte_wikipedia_fr.py     ← CLI, deux modes
├── collecte_anilist.py          ← CLI, deux modes
├── commun/                      ← les composants mutualisés + rapport.py
├── rapports/                    ← VERSIONNÉS, horodatage UTC complet
├── tests/
└── data/raw/<source>/<AAAA-MM>/
    ├── *.ndjson                 ← hors dépôt
    ├── .manifeste_etat.json     ← hors dépôt : état de fusion
    └── MANIFEST.md              ← VERSIONNÉ : la preuve
```

L'emplacement suit la convention constatée du dépôt, pas une convention neuve :
le module 05 héberge déjà deux sources sous `data/raw/<source>/`, et les modules
01, 04 et 05 versionnent un `MANIFEST.md` à côté d'un raw ignoré. Le `.gitignore`
procède par **dépliage niveau par niveau** — git n'entre jamais dans un
répertoire exclu, une négation à l'intérieur serait sans effet.

## Les deux sources

### Wikipédia français

| | |
|---|---|
| **Point d'accès** | `https://fr.wikipedia.org/w/api.php` — **API MediaWiki**, jamais de collecte HTML ni de rendu de page |
| **Licence constatée** | **CC BY-SA**, attribution obligatoire en cas de réutilisation. **À instruire avant tout usage aval** (pas avant la collecte) : forme de l'attribution retenue — titre, URL, `revid` — et ligne de partage entre reformulation intégrale et reprise de tournures. À porter au registre C4. |
| **Périmètre** | Dérivé des sitelinks `frwiki` déjà stockés : `manga.wd_pivot.wiki_fr`, joint au catalogue par `manga.work_identity.wikidata_qid`. **1 368 séries**, 9,33 % du catalogue (mesuré le 2026-09-11). |
| **Pas de recherche par titre** | Élargir aux séries jamais appariées à un QID réintroduirait le matching flou que le bloc 1 a éliminé. Ici une erreur d'appariement est pire qu'un silence : elle rattacherait les personnages d'une œuvre à une autre, **sans signal**. |
| **Cadence** | 1 s entre requêtes, sérielles, `maxlag=5`. En cas de dépassement : attendre et réessayer, jamais forcer. `User-Agent` descriptif avec contact, exigé par l'étiquette Wikimedia. |
| **Limites connues** | Couverture **plancher** : la mesure ne porte que sur les 3 136 séries appariées à un QID ; pour les 11 534 autres l'existence d'un article français est *inconnue, pas absente*. Forte concentration en tête de catalogue — 87 % du top 100, 14 % du catalogue. **Aucune forme française des noms de personnages n'est garantie.** |
| **Non collecté** | Aucune donnée de contributeur — ni nom d'utilisateur, ni historique d'édition. Les personnages sont fictifs, donc hors RGPD ; les contributeurs, non. Aucune image. |

### AniList

| | |
|---|---|
| **Point d'accès** | `https://graphql.anilist.co` — API GraphQL |
| **Licence constatée** | **À instruire.** Le régime de réutilisation des contenus AniList n'a pas été établi à ce jour. Aucune exploitation aval avant cette instruction et son inscription au registre C4. |
| **Périmètre** | `manga.work_identity.anilist_id`, alimenté par le pont `mappings` de Kitsu — **ne transite pas par Wikidata**. **8 051 séries**, 54,9 % du catalogue (mesuré le 2026-09-11), soit un facteur 6 sur le périmètre Wikipédia. |
| **Cadence** | 2 s entre requêtes (30/minute), valeur **volontairement conservatrice**. La limite réelle est à confirmer contre la documentation AniList au moment de la collecte. |
| **Limites connues** | Biais de popularité hérité du pont Kitsu. Langue des descriptions non mesurée — à titre de repère, le raw Kitsu porte 84,9 % de descriptions, à 94 % en anglais. |
| **Non collecté** | Aucune image, aucune donnée d'utilisateur du site. |

> **Sur la ligne « licence ».** La spec demandait d'y documenter pourquoi
> Nautiljon a été écarté. **Cette décision n'est écrite nulle part dans le
> dépôt** — aucune occurrence dans le journal, la feuille de route ou `docs/`.
> Elle reste donc à motiver et à dater ; une exclusion motivée vaut mieux qu'une
> source absente sans explication, mais je n'invente pas le motif.

## Commandes

```bash
export APIMANGA_DSN='dbname=apimanga user=... host=localhost port=5432'
export CONTACT_COLLECTE='projet-manga (contact@exemple.org)'   # obligatoire

uv run python collecte_wikipedia_fr.py reconnaissance
uv run python collecte_wikipedia_fr.py collecte --partition 2026-09
```

`--limite-series N` borne le parcours. `--dsn` remplace la variable
d'environnement. Les deux collecteurs ont la même interface.

**L'arrêt entre les deux phases est structurel, pas conventionnel.** Le mode
`reconnaissance` écrit son rapport avec `Statut : EN ATTENTE DE VALIDATION`.
Le mode `collecte` refuse de démarrer — avant même de toucher à la base — tant
qu'aucun rapport ne porte `Statut : VALIDE`. **L'outil ne se valide jamais
lui-même** : passer le statut est un acte humain.

**Reprise.** Chaque série traitée est inscrite dans l'enveloppe de run
immédiatement. Une interruption se reprend par la même commande : le périmètre
déjà couvert est sauté, sans retéléchargement et **sans dégrader le manifeste**.
Un échec n'est jamais avalé — il est documenté avec son motif, et l'égalité
`périmètre = collectées ∪ échecs` est vérifiée en fin de run.

**Tout échec sort en code non nul, avec message sur `stderr`.**

## Nommage des compteurs

Tout compteur porte dans son nom **ce qu'il compte** : `articles_traites`,
`liens_collectes`, `personnages_distincts`, `series_du_perimetre`. Les noms
génériques — `items`, `count`, `total` — sont **refusés à l'écriture comme à la
relecture**, par exception.

> *Motif.* L'état de la collecte Kitsu nomme `items` un compteur de **liens**
> œuvre × personnage. Repris comme un nombre de personnages, il a produit une
> surestimation de 14 % — 39 161 au lieu de 34 293 — propagée dans plusieurs
> documents de pilotage avant d'être corrigée. La définition était dans le nom
> qu'on n'a pas donné.

## Tests

```bash
uv run pytest tests -q
```

**42 tests.** Les cinq contrôles imposés y figurent nommément : le `.gitignore`
ne masque jamais un `MANIFEST.md` (vérifié par `git check-ignore`), la fusion
d'un run partiel rend l'union et non le second seul, la cadence se vérifie sans
réseau ni attente réelle, aucun compteur générique ne passe, et un échec sort en
code non nul. S'y ajoutent l'idempotence du manifeste, l'égalité d'ensembles, le
rattachement catalogue de chaque enregistrement, et — si `APIMANGA_DSN` est
défini — la dérivation contre la base réelle, en lecture seule.

## État

L'ossature est complète et éprouvée. **L'extraction ne l'est pas**, et c'est
voulu : les deux specs de collecte décrivent *quoi* extraire, et celle de
Wikipédia impose un arrêt tant que la définition de « section personnages
exploitable » n'est pas validée. La fonction `recuperer` de chaque collecteur
est une jointure explicite qui lève, en renvoyant à sa spec — écrire
l'extraction avant cette validation reviendrait à décider à sa place.
