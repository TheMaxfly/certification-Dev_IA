# Jeu d'évaluation v1 — notes de gel

69 questions, onze familles (F11 « par référence » ajoutée par la migration
`019`). Les chiffres des contrôles sont dans le rapport de gel ; cette note
garde ce qu'un chiffre ne dit pas.

## Écartées, avec motif

| Question | Motif |
|---|---|
| Q055 (isekai), retirée le 29/09 | « isekai » est un vocabulaire de client absent de la taxonomie du catalogue ; le numéro a été réattribué |
| Q061 (D4), Q065 (D10) — numéros non attribués | « psychologique » n'existe pas dans les genres Manga Sanctuary ; les genres Kitsu ne sont ni complets (4 819 séries) ni fiables (225 séries portent les genres d'une autre entrée) : une règle mesurerait l'enrichissement, pas le filtre |
| D2 | doublon, plus pauvre, de D24 |
| D5 | « pas football » n'a pas de code : la règle ignorerait l'exclusion |
| D9 | aucun code (F9, F10 impossibles), vocabulaire des critiques (F3 impossible) |
| D12 | ni « dystopie » ni « sombre » ne se filtrent |
| D14 | recoupement avec Q016, dont Kaguya-sama est une réponse |
| D16 | doublon de D24 (même référence, My Hero Academia) |
| D15, D17, D18, D19, D20 | équilibre de F11 — public, genre, décennie |

## Reclassements et réponses retirées

- **Q040** : F8 → F2 ; la note « mot absent des titres français » ne tenait
  plus avec Kings of Shôgi ; *The Ryuo's Work is Never Done* retiré, non
  retrouvé.
- **Q067** : retirés au tri, *Uzaki-chan wants to hang out !* et *Horimiya* ;
  trois ajouts de Max, non retrouvés par titre exact sous leur titre anglais,
  **rétablis sous leur titre au catalogue**, pertinents, avec réserve : « À quoi
  tu joues, Ayumu ?! » (lycéens, shôgi), « Tellement flou d'elle ! »
  (taquineries secondaires), « Kubo-san wa Boku wo Yurusanai » (lycéens).
- **Aharen est indéchiffrable** (proposée pour Q067) : absente du catalogue.

## Le contre-exemple du §4

Les douze requêtes « par référence » de décembre ont été créées le 28 décembre
2025 à 04 h 02, après la construction des index FAISS, sans trace de la façon
dont leurs qrels ont été produits. On ne peut prouver ni qu'elles ont été
ajustées au système, ni le contraire : leurs réponses ont donc été réécrites
sans les qrels. Le jeu v1 porte cinq questions dont l'origine n'est pas tout à
fait propre, et il le dit.

## Sous-groupes calculés, jamais écrits

Le marqueur « atteignable par synopsis anglais seulement » est calculé à chaque
mesure depuis le corpus (`evaluation.atteignabilite.synopsis_seul`). Au gel, sept
séries attendues en F3 le portent (Akira, Amer Béton, Blame !, L'Habitant de
l'Infini, Berserk, Nichijô, Danshi Kôkôsei no Nichijô) : les « critiques » du
corpus sont celles du staff au grain tome, qui couvrent 3 456 séries sur 14 670.

## Ce que je n'ai pas pu établir

- la **représentativité** du jeu face à l'usage réel — elle ne se mesure qu'en
  production ;
- la **production des qrels de décembre**, et donc l'absence d'ajustement des
  reprises au système de décembre ;
- si Manga Sanctuary publie des critiques des grands classiques sous une autre
  forme que la critique staff par tome ;
- l'effet du **biais de modèle** sur F11 : ses réponses ont été proposées par un
  assistant, puis tranchées par Max.
