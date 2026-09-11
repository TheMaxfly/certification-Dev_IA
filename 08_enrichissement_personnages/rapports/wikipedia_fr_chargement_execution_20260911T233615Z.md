# Chargement Wikipédia FR — rapport d'exécution

Statut : VALIDE

## 1. Environnement déclaré

| | |
|---|---|
| PostgreSQL | 16.15 (Ubuntu 24.04), collation `fr_FR.UTF-8` |
| Python | 3.12.3 |
| Variables `PG*` héritées | aucune |
| Migration | **015, inchangée — aucune migration ajoutée** |
| Raw | `data/raw/wikipedia_fr/2026-09/…ndjson`, 8,4 Mo, 1 367 articles |

## 2. Le test du pari multi-sources — **tenu, avec une réserve nommée**

**Le chargement s'est fait par insertion dans les quatre tables existantes,
sans aucune migration.** `015` reste la dernière ; un test l'exige désormais.

Mieux : les **quatre requêtes de promotion n'ont pas été dupliquées**. Elles
joignent le staging au référentiel par `(source, source_id)` et ne portent rien
de propre à une source — elles ont été déplacées dans `commun/chargement.py` et
servent les deux chargeurs à l'identique.

| | |
|---|---:|
| **Durée** | **1,7 s** |
| **Pic mémoire** | **52 Mo** |

**La réserve, et elle est réelle : `position` n'a pas de colonne.** Le raw porte
la position de chaque personnage dans sa section — seul indice de hiérarchie que
Wikipédia offre, à défaut de rôle. Aucune des quatre tables ne peut l'accueillir
sans abus : `role_source` attend une valeur de rôle, pas un rang.

Elle n'est donc **pas chargée**. L'information n'est pas perdue — le raw est
immuable et la conserve — mais elle n'est pas interrogeable. La réparer demande
une migration `016` ajoutant `position` à `character_work`. **Décision non prise
ici.**

Le pari tient donc pour les **quatre grains** du schéma, et achoppe sur **un
champ d'une source**. C'est exactement ce qu'une deuxième source devait révéler.

## 3. Volumétrie

| Table | Wikipédia | Kitsu | Total |
|---|---:|---:|---:|
| `characters` | **13 101** | 34 293 | 47 394 |
| `character_forms` | **21 398** | 105 048 | 126 446 |
| `character_descriptions` | **10 461** | 29 108 | 39 569 |
| `character_work` | **13 703** | 39 161 | 52 864 |

Formes : 13 101 `canonical` + 8 297 `name_lang`/`ja`. **Aucun alias** —
l'extraction Wikipédia n'isole pas de formes alternatives.

## 4. Trois états légitimes, à ne pas lire comme des défauts

**Le rôle est NULL sur les 13 703 liens.** Wikipédia ne porte aucun vocabulaire
de rôles. Ce n'est pas un chargement incomplet, c'est ce que la source contient.

**La langue est `fr` sur les 10 461 descriptions, affirmée et non inférée.**
L'article vient de `fr.wikipedia.org` : fait de provenance. La règle B reste
cantonnée à Kitsu — appliquée ici, elle rendrait 98,5 % de NULL sur un corpus à
97,0 % de marqueurs français, et 131 faux positifs `en` sur du français.

**La licence est `CC BY-SA` sur les 10 461**, avec en `provenance` le titre de
l'article **et son `revid`** — et l'article retenu est celui d'où le texte vient
réellement, le dédié quand le renvoi l'emporte. Nommer l'article de série aurait
produit une attribution fausse.

## 5. Les deux grains — et une correction

Le double compte est **absorbé par la séparation personnage / lien** : 313
personnages portent 602 liens surnuméraires, et 13 703 − 602 = 13 101.

| Grain | Descriptions ≥ 80 car. |
|---|---:|
| **par lien (par série)** | **9 002** |
| **par personnage (article unique)** | **8 614** |

> **Correction.** J'avais annoncé 8 662 pour le second grain. Ce compte
> dédupliquait par l'article **servi pour la série** ; le chargeur, lui, clé sur
> l'article **d'où le texte vient** — le dédié quand le renvoi l'emporte, qui
> est le seul correct pour l'attribution. Les deux définitions diffèrent de
> **48 personnages**. La valeur en base est **8 614**.
>
> Septième occurrence du motif, et la plus fine à ce jour : ici les deux
> chiffres portaient le même nom — « par article unique » — et désignaient deux
> articles différents.

## 6. Qualité de l'extraction — une limite mesurée

**55 noms sur 13 101 (0,4 %) ne portent aucune majuscule.** Une partie est
légitime (`Ōzen`, `Ōni` — la majuscule `Ō` sort de la classe testée) ; le reste
est de la **fausse isolation** du parseur : `forme 5`, `forme 6`, `4 éléments`,
`adaptation animée`, `une [[`. Des éléments de liste qui ne sont pas des
personnages.

Sous 0,5 %, et corrigeable sans recollecte : le raw est immuable et le parseur
rejouable.

## 7. Ce que je n'ai pas pu établir

- **La position dans la section** : portée par le raw, sans colonne pour
  l'accueillir. La décision d'ajouter `position` à `character_work` reste
  ouverte, et c'est la seule chose que ce chargement n'a pas su ranger.
- **La part exacte de fausses isolations** : 55 noms sans majuscule en sont un
  minorant, pas une mesure. Un nom correctement capitalisé peut aussi n'être pas
  un personnage.
- **Le recouvrement réel avec les personnages Kitsu** : mesurable maintenant que
  les deux sources sont en base, mais c'est le travail de la cascade — hors
  périmètre, et volontairement non entamé.
- **La justesse de `fr` sur les 6 descriptions (0,1 %) réellement anglaises** :
  erreur connue, assumée, et incomparablement moins coûteuse que 98,5 % de NULL.
