# wikipedia_fr — definition de « section personnages exploitable »

Statut : VALIDE

> Valide le 2026-09-11 par la conception, les quatre parametres retenus tels
> que proposes. Ce statut deverrouille `reconnaissance --etape mesures`.

## Ce sur quoi la definition s'appuie

Mesures de l'inventaire du 2026-09-11, 40 articles, tirage stratifie a graine
fixe (20 premier decile, 20 dernier decile du perimetre de 1 368 series).
**Aucune section exploitable n'a ete comptee pour les produire.**

| Fait mesure | Valeur |
|---|---:|
| Articles repondant | **40 / 40** |
| Redirections a resoudre | 0 |
| `revid` manquants | 0 |
| Titres de section distincts | 291 |
| Articles portant une section dont le titre contient « personnage » | **32 / 40** |
| — en tete de catalogue | 15 / 20 |
| — en queue de catalogue | **17 / 20** |

**La tete n'est pas plus riche que la queue.** C'est contre-intuitif et cela ne
contredit pas la decision figee 3 : celle-ci porte sur la *concentration des
articles francais* en tete de catalogue, pas sur la richesse de chacun.

### Les sept formes de titre rencontrees

| Titre | Occurrences | Niveau |
|---|---:|---|
| `Personnages` | 26 | 2 |
| `Personnages principaux` | 3 + 2 | 3 et 2 |
| `Personnages secondaires` | 2 | 3 |
| `Personnages et trame principale` | 1 | 2 |
| `Les personnages` | 1 | 2 |
| `Techniques de combat des personnages` | 1 | 3 |

Le dernier est un **faux positif** : il decrit des techniques, pas des
personnages. Un motif large sur « personnage » le capterait.

### Les 8 articles sans section « personnage »

Leurs sections de niveau 2 montrent deux cas distincts :

- **renvoi vers un article dedie** — `Ken le Survivant` (qui lie *Personnages de
  Hokuto no Ken*), `Negima`, `Claymore`, `Kuroko's Basket` ;
- **absence reelle** — `MF Ghost`, `Chocolat (manga)`, `Blazer Drive` n'ont ni
  section ni article dedie ; `L'Ere des Shura` porte un *Arbre genealogique de
  la famille Mutsu*, et `Ken le Survivant` une *Distribution* qui liste des
  comediens de doublage, **pas des personnages**.

### Detecter l'article dedie : deux methodes, deux biais mesures

| Methode | Trouve | Defaut mesure |
|---|---:|---|
| Construire le titre `Liste des personnages de X` / `Personnages de X` | 3 / 40 | **sous-detecte** : *Personnages de Hokuto no Ken* est manque, l'article de serie s'appelant *Ken le Survivant* |
| Balayer les liens sortants contenant « personnage » | 7 / 40 | **sur-detecte** : 4 faux positifs sur 7 — *Personnages de Love Hina* lie depuis Negima (meme auteur), *Paladin (personnage)*, une categorie, et la liste d'une oeuvre parente |

Aucune des deux n'est fiable seule. **3 articles sur 40 portent un article dedie
authentique** — 7,5 %, tous en tete de catalogue.

## Definition proposee

Un article est **porteur d'une section personnages exploitable** si C1, C2 et C3
sont reunies.

**C1 — Localisation.** L'article contient au moins une section dont le titre
normalise (minuscules, sans accents) satisfait le motif retenu *(parametre 1)* ;
ou renvoie vers un article dedie *(parametre 2)*.

**C2 — Isolation.** La section individualise les personnages : nom en tete
d'element de liste, de sous-section, de ligne de tableau, ou de paragraphe
ouvert par le nom en gras. *(parametre 4 pour la prose continue)*

**C3 — Densite.** Au moins **3 personnages** satisfont C2 et portent un texte
descriptif atteignant le seuil retenu *(parametre 3)*.

## Les quatre parametres a trancher

| # | Question | Proposition |
|---|---|---|
| 1 | Quels titres de section comptent ? | Motif `^(les )?personnages?( (principaux|secondaires|et .*))?$` sur le titre normalise — capte les 6 formes reelles, ecarte *Techniques de combat des personnages* |
| 2 | Un article dedie compte-t-il ? | Oui, detecte par **lien sortant confirme par le contexte** — le lien doit partir d'une section dont le titre satisfait C1, ce qui elimine les 4 faux positifs mesures |
| 3 | Seuil de texte par personnage | **80 caracteres** — le raw Kitsu montre que 15,3 % de ses descriptions sont sous ce seuil et se reduisent a « Sacchan's mother. » |
| 4 | Prose continue non isolee | Comptee **a part**, categorie `prose`, ni exploitable ni ignoree — sa part se mesure et se decide apres |


## Parametres retenus — validation du 2026-09-11

| # | Parametre | Valeur retenue |
|---|---|---|
| 1 | Titres de section | Motif ancre `^(les )?personnages?( (principaux|secondaires|et .*))?$` sur le titre normalise |
| 2 | Article dedie | Compte, detecte par **lien confirme par sa section** — le renvoi doit partir d'une section satisfaisant C1 |
| 3 | Seuil de texte | **80 caracteres** apres nettoyage du balisage |
| 4 | Prose continue | Comptee **a part**, categorie `prose` — ni exploitable, ni ignoree |

Consequence de mesure : le rapport de phase A portera **trois taux** — exploitable,
prose, aucun — et non deux.
