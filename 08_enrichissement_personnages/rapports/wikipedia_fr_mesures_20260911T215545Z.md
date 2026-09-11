# wikipedia_fr — mesures — 20260911T215545Z

Statut : VALIDE

> Taux valide le 2026-09-12 par la conception : la phase B est autorisee,
> avec deux mesures exigees au rapport de collecte — le taux reel contre
> cette projection, et la part d'articles porteurs d'un renvoi sur tout le
> perimetre, cette seconde ne figurant pas a la spec.

## 1. Environnement declare

| | |
|---|---|
| definition appliquee | wikipedia_fr_definition_20260911T214215Z.md |
| point d'acces | https://fr.wikipedia.org/w/api.php |
| cadence | 1.0 s, maxlag=5 |
| requetes emises | 41 |
| attentes maxlag | 0 |
| releve brut | wikipedia_fr_mesures_20260911T215545Z.json |

## 2. Perimetre

| | |
|---|---|
| series du perimetre | 1368 |
| echantillon | 40, stratifie, graine 20260911 |

## 3. Resultats

| | |
|---|---|
| articles mesures | 40 |
| EXPLOITABLES (C1+C2+C3) | 18/40 (45.0 %) |
| — en tete | 10/20 (50.0 %) |
| — en queue | 8/20 (40.0 %) |
| PROSE continue (parametre 4) | 4/40 (10.0 %) |
| AUCUN des deux | 18/40 (45.0 %) |
| articles renvoyant a un article dedie | 3/40 (7.5 %) |
| personnages isoles | 333 |
| personnages decrits (>= 80 car.) | 237 |
| personnages par article exploitable | 13.2 |
| personnages portant une graphie CJK | 231/333 (69.4 %) |
| longueur description min / mediane / max | 80 / 429 / 6918 |
| articles a marqueur de denouement | 6/40 (15.0 %) |
| PROJECTION sur le perimetre | 616 series exploitables sur 1368 — 45.0 % ± 15.4 pts |

## 4. Ecarts aux attendus

- Samurai Deeper Kyo : prose continue, personnages non isoles
- Negima ! Le Maître magicien : aucune section satisfaisant C1
- Claymore (manga) : aucune section satisfaisant C1
- Ken le Survivant : aucune section satisfaisant C1
- Rosario + Vampire : aucune section satisfaisant C1
- Monster Hunter Orage : 1 personnage(s) decrit(s) sur 1 isole(s) — seuil C3 non atteint
- D.N.Angel : prose continue, personnages non isoles
- Food Wars! : prose continue, personnages non isoles
- Kuroko's Basket : aucune section satisfaisant C1
- Blazer Drive : aucune section satisfaisant C1
- Iron Wok Jan! : 1 personnage(s) decrit(s) sur 1 isole(s) — seuil C3 non atteint
- MF Ghost : aucune section satisfaisant C1
- Keijo!!!!!!!! : 0 personnage(s) decrit(s) sur 0 isole(s) — seuil C3 non atteint
- Servant × Service : 0 personnage(s) decrit(s) sur 6 isole(s) — seuil C3 non atteint
- Danshi kōkōsei no nichijō : 2 personnage(s) decrit(s) sur 9 isole(s) — seuil C3 non atteint
- Historiē : 0 personnage(s) decrit(s) sur 6 isole(s) — seuil C3 non atteint
- Kin-iro Mosaic : 0 personnage(s) decrit(s) sur 10 isole(s) — seuil C3 non atteint
- Baby Steps : 0 personnage(s) decrit(s) sur 0 isole(s) — seuil C3 non atteint
- L'Ère des Shura : aucune section satisfaisant C1
- Pyū to fuku! Jaguar : prose continue, personnages non isoles
- Chocolat (manga) : aucune section satisfaisant C1
- Sengoku Youko : 0 personnage(s) decrit(s) sur 14 isole(s) — seuil C3 non atteint

## 5. Ce que je n'ai pas pu etablir

- La marge est un intervalle de Wald a 95 % sur n=40 : grossier, il indique un ordre de grandeur, pas une precision.
- Le taux de denouement repose sur des marqueurs lexicaux explicites : c'est une appreciation, elle sous-detecte une revelation formulee autrement et sur-detecte un emploi anodin de « trahit ».
- La qualite d'isolation des personnages n'est pas verifiee article par article : le parseur reconnait quatre formes frwiki courantes, une cinquieme lui echapperait sans signal.
