# Chargement des personnages — décisions et mesures préalables

Statut : VALIDE

> Décisions prises avant écriture du chargeur, et mesures qui les fondent.
> La migration `015_personnages_multisources.sql` est appliquée ; **aucune
> donnée n'est encore chargée**.

## Points d'arrêt du §8 — levée tracée

| Point | Objet | Résolution |
|---|---|---|
| **A** | `mal_id` | **Conservé**, et le point 16 de la feuille corrigé pour dire « staff uniquement ». Validé le 2026-09-12. |
| **B** | langue des descriptions | **Trois classes `en` / `ja` / NULL**, sans tenter `fr`. Validé le 2026-09-12. |
| **C** | licence et provenance | **Règle proposée par l'exécutant, validée le 2026-09-12.** Verbatim ci-dessous. |
| **D** | normalisation des rôles | **Quatre valeurs françaises**, granularité préservée. Validé le 2026-09-12. |
| **E** | seuil de longueur | Non bloquant. Aucun filtrage ; distribution rapportée ci-dessous. |

### Point C — la règle, verbatim

```
1. EXTRACTION   regex, sur le texte de la description :
                \(?\s*Source\s*:\s*([^)\n]{1,80})
                puis trim et suppression de la ponctuation finale (. , ;)

2. LICENCE      si la provenance extraite correspond, insensible à la casse, à
                   wikipedia | wikia | fandom
                alors  licence = 'CC BY-SA'
                sinon si une provenance est présente
                       licence = 'à instruire'
                sinon  licence = NULL

3. PROVENANCE   la chaîne extraite, stockée telle quelle, jamais normalisée —
                c'est elle qui rend l'attribution vérifiable.
```

**Ce que la règle couvre, mesuré** : 7 434 descriptions citent une source
(25,5 % des 29 108 non vides). La règle en classe **6 092 en `CC BY-SA`
(81,9 %)** et **1 342 en `à instruire` (18,1 %)**.

> **Correction d'un chiffre que j'avais donné.** J'avais annoncé « 3 415 pointant
> Wikipédia », ce qui ne comptait que la chaîne littérale *Wikipedia* et laissait
> croire à ~4 019 citations à instruire. La règle capte aussi les variantes
> *Wikia* et *Fandom* : le reste à instruire est **1 342**, pas 4 019.

## §10 — répartition des provenances non couvertes par la règle

**La question posée : « à instruire » désigne-t-il une tâche ou une impasse ?**

| # | Provenance | Citations |
|---:|---|---:|
| 1 | Narutopedia | 121 |
| 2 | Medaka Box Wiki | 34 |
| 3 | Absolute Anime | 29 |
| 4 | Baka-Tsuki | 28 |
| 5 | ANN | 26 |
| 6 | One Piece Wiki | 25 |
| 7 | Bulbapedia | 23 |
| 8 | TV Tropes | 21 |
| 9 | FUNimation's Official Basilisk Website | 19 |
| 10 | the manga habit | 17 |

**Ni l'un ni l'autre, et la forme de la queue le dit** : 493 provenances
distinctes pour 1 342 citations ; les dix premières n'en couvrent que **25,6 %**,
et **63,9 % des provenances (315) ne sont citées qu'une seule fois**.

**Mais la moitié du travail se fait par une ligne.** Les entrées 1, 2, 6 et 7
sont elles-mêmes des wikis — *Narutopedia*, *Bulbapedia*, *One Piece Wiki*
—, hébergés par Fandom et sous le même régime CC BY-SA. Élargir la règle aux
motifs `\bwiki\b` et `pedia` fait passer la couverture de **81,9 % à 90,0 %**, et
réduit le reste à **744 citations pour 345 provenances**.

Ce qui subsiste alors n'est plus une queue de wikis mais un ensemble hétérogène
— *Absolute Anime*, *Baka-Tsuki*, *ANN*, *TV Tropes*, un site officiel FUNimation
— dont chacun a son propre régime. **Ceci est une tâche d'instruction, bornée à
environ 345 sources dont la plupart sont anecdotiques, et non une impasse** ;
mais c'est une tâche qui ne se termine pas par une règle, seulement par un
arbitrage de seuil : à partir de combien de citations une provenance mérite-t-elle
d'être instruite ?

**L'élargissement de la règle n'est PAS appliqué** : la règle validée est celle du
point C ci-dessus. L'élargissement est proposé, mesuré, et laissé à décision.

## §8-E — distribution des longueurs, aucun filtrage appliqué

| | |
|---|---:|
| Descriptions non vides | 29 108 (84,9 %) |
| min / médiane / p95 / max | 3 / **316** / 1 832 / 18 842 |
| Sous 80 caractères | **4 461 — 15,3 %** des non vides, 13,0 % du corpus |

## Contrôle 11 — écart motivé

L'attendu du §7 est **9 878** formes `alias`. Trois valeurs coexistent, et il
fallait choisir :

| Définition | Valeur |
|---|---:|
| Entrées brutes de `otherNames` | **9 878** |
| Entrées non vides (chaîne non falsy) | 9 870 |
| **Entrées non vides après `trim`** | **9 868** |

L'écart de 10 vient de **8 chaînes vides** et **2 blancs seuls**, portés par 10
personnages. **9 868 est chargé** : une forme vide n'est ni recherchable ni
comparable, donc ce n'est pas une forme. La migration le garantit par contrainte
(`character_forms_non_vide`) plutôt que par discipline du chargeur.

## Définition du volume de personnages Wikipédia — sixième occurrence du motif

La collecte du 2026-09-11 a détecté que **4 articles servis correspondent à 10
enregistrements** : trois séries MS distinctes résolvent vers *L'Attaque des
Titans*, trois vers *Baki (manga)*, deux vers *Kimengumi*, deux vers
*Princesse Saphir*.

**Règle d'écriture, désormais obligatoire.** Tout chiffre de personnages issu de
Wikipédia précise son grain :

| Grain | Valeur | Quand l'employer |
|---|---:|---|
| **par série** | **9 002** | volume rattaché au catalogue — chaque série porte ses personnages |
| **par article unique** | **8 662** | volume de matière textuelle distincte collectée |

Écart : **340 personnages, 3,8 %**. Les deux chiffres sont justes ; aucun ne
s'emploie sans son grain. Le rattachement reste correct dans les deux cas —
chaque enregistrement porte son `series_id`.

> C'est la **sixième occurrence** du même motif dans ce projet, après `items`
> pour des liens, « 3 187 critiques » sans sa date, « 5 894 œuvres » pour un
> périmètre interrogé, « 8,1 % » pour un gisement d'articles, et « 9 878 alias »
> ci-dessus. Les cinq premières ont coûté une correction de document ; la
> cinquième a produit une règle d'écriture ; celle-ci est inscrite avant d'avoir
> rien coûté.
