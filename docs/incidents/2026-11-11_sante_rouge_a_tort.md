# Incident — la santé jugée rouge à tort après une livraison

> Fiche d'incident, courte. Incident **réel, non provoqué**, des 11 et 12 novembre 2026, corrigé le
> 12 novembre (outil de livraison 0.1.2). Heures en UTC. Sources : le rapport du lot
> (`e5_lot1_monitorage_20261112T195153Z`, hors dépôt, section 01) et les journaux de livraison
> versionnés dans `15_livraison/journal/`.

## 1. Résumé

Cinq gestes de livraison en production, entre le 11 et le 12 novembre 2026, ont été jugés « santé
rouge » alors que la pile était saine : l'outil de livraison lisait l'état des cibles de Prometheus
avant que Prometheus ait relevé les conteneurs qui venaient de démarrer. Correctif : ne juger une cible
que sur un relevé postérieur au lever (commit `cb64759`). Depuis, quatre livraisons sur quatre sont
vertes au premier jugement.

## 2. Contexte et nature

L'outil de livraison (module `15_livraison`, commande `livrer`) déploie la pile à une étiquette, puis
juge sa santé : versions servies, `/sante` de l'application, catalogue, API du modèle, migrations et,
en production, **toutes les cibles de Prometheus `up`**. Une santé rouge rend le code de sortie 1 et
propose au mainteneur un retour arrière. L'incident est réel : personne ne l'a provoqué.

## 3. Chronologie

| Date et heure | Geste | Cibles jugées | Journal |
|---|---|---|---|
| 2026-11-11 18:23:21 | déploiement `e4-livraison-1` | 4/5 up (`application` down) | `20261111T182321Z_production_e4-livraison-1.md` |
| 2026-11-11 22:11:16 | retour arrière vers `chaine-livraison` (exercice prévu) | 3/5 up (`api`, `application` down) | `20261111T221116Z_production_chaine-livraison.md` |
| 2026-11-11 22:19:27 | redéploiement `e4-livraison-1` | 4/5 up (`application` down) | `20261111T221927Z_production_e4-livraison-1.md` |
| 2026-11-11 22:27:58 | redéploiement `e4-livraison-1` | 4/5 up (`api` down) | `20261111T222758Z_production_e4-livraison-1.md` |
| 2026-11-12 22:37:02 | redéploiement `e4-livraison-journal` | 3/5 up (`api`, `application` down) | `20261112T223702Z_production_e4-livraison-journal.md` |

À chaque fois, un seul contrôle manque, « cibles up » (le premier journal : 18 tenus sur 19), et
seulement sur les conteneurs qui venaient d'être relancés. Le 11 à 18:24:18, puis le 12, 30 s après le déploiement, `livrer sante`
rejoué rend **vert**.

## 4. Impact

**Aucune indisponibilité** : la pile servie était saine. Le risque était le message lui-même : il
proposait de revenir en arrière sur une production en bon état.

## 5. Détection

Le code de sortie 1 de `livrer`, et le verdict « santé rouge » écrit au journal de livraison, avec la
seule ligne en manque.

## 6. Cause

`sante.cibles_prometheus` (`15_livraison/src/livraison/sante.py`) lisait `/api/v1/targets` **une
seule fois**, dès que `/sante` répondait, et ne regardait que l'état de chaque cible, sans l'heure de
son relevé. Juste après un lever, Prometheus porte encore, un temps, le relevé du conteneur quitté. Un
relevé neuf arrive au plus environ 25 s plus tard (intervalle de 15 s et délai de 10 s,
`13_monitorage/prometheus/prometheus.yml`).

## 7. Correctif

Commit **`cb64759`** — « la santé juge Prometheus sur un relevé postérieur au lever » : une cible n'est
jugée que sur un relevé commencé après l'instant où `/sante` a répondu (champ `lastScrape`) ; tant qu'il
en manque un, la santé relit, dans une borne dérivée de Prometheus (le plus grand intervalle plus le
délai des cibles, plus 5 s : 30 s aujourd'hui). Deux défauts voisins dans le même lot : `c0b0a66` et
`e6acc49`. Outil de livraison **0.1.2**, fusionné le 12 novembre (demande n° 49, `074514f`).

## 8. Preuve, avant et après

| | Avant (0.1.0, 0.1.1) | Après (0.1.2 et suivantes) |
|---|---|---|
| Tests automatiques | aucun ne voyait la course : les doublures n'avaient pas l'heure du relevé | 13 tests (25-a à 25-g) ; deux mutations versionnées, `E5V-01` et `E5V-02`, prouvent qu'ils voient l'ancien comportement |
| Livraisons réelles | **5 sur 5 rouges** (journaux ci-dessus) | **4 sur 4 vertes au premier jugement** : préproduction et production le 13 novembre (14 s et 12 s d'attente d'un relevé), préproduction et production le 17 novembre (14 s et 10 s) |

Le 13 novembre en production, la cible `application` était encore à 0 au premier relevé après le lever :
le jugement immédiat aurait été rouge une sixième fois ; l'attente l'a jugée sur le relevé suivant, `up`.
Journaux : `20261113T142402Z_staging_e5-monitorage.md`, `20261113T164454Z_production_e5-monitorage.md`,
`20261117T015318Z_staging_e5-incident.md`, `20261117T025003Z_production_e5-incident.md`.

## 9. Prévention

La règle du relevé postérieur, avec une borne lue dans Prometheus et jamais recopiée ; des doublures de
test relevées sur le poste avant d'être écrites (les anciennes, sans l'heure du relevé, avaient caché ce
défaut) ; les mutations versionnées ; le README du module 15.

## 10. Limites

Un poste, pas un serveur ; la borne d'attente dépend de la configuration de Prometheus : si elle change,
la borne suit, mais le comportement n'a été observé qu'avec un relevé toutes les 15 s.
