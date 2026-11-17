# Incident — la panne de la base n'était pas détectée sans visiteur

> Fiche d'incident. Défaut révélé le 13 novembre 2026, corrigé et livré en production le
> 17 novembre 2026 (étiquette `e5-incident`). Heures en UTC.
>
> Les chiffres viennent du rapport du lot (`e5_lot1_monitorage_20261112T195153Z`, hors dépôt),
> de ses dossiers de preuves (hors dépôt, cités par leur nom) et des journaux de livraison
> versionnés dans `15_livraison/journal/`.

## 1. Résumé

- Le 13 novembre 2026, pendant une séance de pannes provoquées en préproduction, l'arrêt du serveur
  PostgreSQL n'a été vu par aucune jauge ni aucune alerte pendant 4 min 08 s : il a fallu la première
  requête d'un visiteur pour que l'application le constate.
- Le défaut existait en production depuis la livraison d'E4, le 11 novembre 2026.
- Correctif : l'application relit elle-même ses dépendances toutes les 15 s (« sonde des
  dépendances », application 0.9.2, commit `0c0fe11`).
- Preuve : un test automatique qui échoue avant et passe après ; l'incident rejoué sans aucun
  visiteur le 17 novembre, en préproduction puis une fois en production — jauges en 18 à 37 s,
  notifications en 113 à 157 s.

## 2. Contexte et nature

L'application de conseil en lecture (module `12_application`) dépend de trois services : sa base des
comptes, l'API du modèle (module `11_api_modele`) et l'API du catalogue (module `02_api_manga`). Les
trois lisent le même serveur PostgreSQL. Le monitorage (module `13_monitorage`, Prometheus et Grafana)
relève toutes les 15 s des jauges exposées par l'application — l'état de la base, du catalogue, de
l'API, le coût du jour — et des règles d'alerte les surveillent (F : base, G : catalogue, H : coût du
jour), avec notification par courriel.

Le 13 novembre 2026, une **séance de pannes provoquées** a été jouée en préproduction (le « staging »)
pour éprouver ce monitorage : l'API du modèle, le catalogue, le coût du jour, puis le serveur
PostgreSQL ont été arrêtés ou faussés un à un, et l'on a mesuré ce que voyaient les jauges et les
alertes. **La panne était provoquée ; le défaut qu'elle a révélé est réel** : il était présent dans la
version servie en production depuis le 11 novembre 2026 (étiquette `e4-livraison-1`, application
0.9.1).

## 3. Chronologie de l'« avant » — 13 novembre 2026, préproduction, application 0.9.1

Préproduction levée à l'étiquette `e5-monitorage`, avec **les mêmes images que la production** de ce
jour (`api-modele:1.5.0`, `api-catalogue:0.7.0`, `application-conseiller:0.9.1`) et **le même serveur
PostgreSQL**. Source : rapport, section 03, § G (panne C) ; preuves `2026-11-13_C20_C21_seance_pannes`
(relevé toutes les 5 s de Prometheus et de Grafana, chronologie des événements).

| Heure | Fait | Depuis l'arrêt |
|---|---|---|
| 16:04:08 | serveur PostgreSQL arrêté par le mainteneur | 0 s |
| 16:04:08 → 16:08:16 | **aucune jauge ne bouge** : base, catalogue, API à « ok » ; règles F et G au repos | 248 s |
| 16:08:16 | première requête de visiteur (accueil), jouée exprès | 248 s |
| 16:08:30 | jauges base et catalogue « indisponible », API à 0 | **262 s** |
| 16:10:15 / 16:10:20 | notifications F, puis G | **367 s / 372 s** |
| 16:18:30 | serveur relancé | — |
| 16:18:30 → 16:22:30 | **les jauges restent « indisponible »**, F et G restent actives | — |
| 16:22:45 | une question de visiteur relit la base : jauge « ok » | 255 s après la relance |
| 16:23:20 | notification de résolution de F | 290 s après la relance |
| 16:28:15 | G résolue, après une recherche de visiteur | 585 s après la relance |

Le même constat, la même séance, sur les autres pannes : catalogue arrêté, **376 s** jusqu'à la
notification, dont 240 s sans requête et sans jauge ; coût du jour au-dessus du seuil puis remis à
zéro, **jauge figée environ 50 min** à l'ancienne valeur (15:32:38 → 16:22:45) ; API du modèle arrêtée 8 min, jauge de santé
**restée à 1** (elle n'était lue qu'au démarrage et par `/sante`).

## 4. Impact

- **Ce que le serveur arrêté coupait** : les trois services. L'application rendait 503 sur les
  comptes et l'administration ; l'API du modèle, sans base, ne cherchait plus de passages, donc **le
  conseiller ne répondait plus** (503 « La recherche est indisponible ») ; le catalogue rendait 503
  sur la recherche. **Seul l'accueil restait servi.** (Rapport, section 03, § G.)
- **Ce que voyait le monitorage** : rien, tant qu'aucun visiteur ne venait. La durée pendant laquelle
  personne n'aurait été prévenu **n'avait pas de borne** : elle valait le temps jusqu'au premier
  visiteur. Après le retour du serveur, les alertes restaient actives **à tort** jusqu'à la requête
  suivante.

## 5. Détection

Le défaut a été vu par la séance de pannes elle-même, conçue pour cela : on y a mesuré qu'aucune jauge
ne changeait sans qu'une requête touche l'application. Le monitorage ne pouvait pas le voir : aucune
cible de Prometheus ne lit la base, et les jauges de dépendances de l'application n'étaient posées
qu'au fil des requêtes. Seule la règle A, sur la disponibilité des cibles relevées, a vu l'arrêt de
l'API du modèle sans requête.

## 6. Cause — une seule

**L'état d'une dépendance n'était relu qu'au passage d'une requête.** Dans l'application 0.9.1
(`12_application/src/application/`) :

- `base.py`, `Base._noter` : la jauge `application_base_etat` n'était posée qu'à l'ouverture d'une
  transaction, donc pendant une requête ;
- `client_catalogue.py`, `ClientCatalogue._noter` : `application_catalogue_etat`, à chaque appel au
  catalogue, donc pendant une recherche ;
- `app.py` : `application_api_sante_ok` n'était posée qu'au démarrage (`cycle`) et par `/sante` ; le
  coût du jour, qu'à la lecture du plafond avant une question.

Sans visiteur, le monitorage ne pouvait rien dire ; après le retour, il disait faux.

## 7. Correctif

**La sonde des dépendances** (`12_application/src/application/sonde.py`, lancée par le cycle de vie de
l'application) : toutes les 15 s (`[sonde] periode_s` dans `12_application/config/reglages.toml`,
avec son motif), une **passe** relit en même temps la base, le catalogue (`/health`), l'API du modèle
(`/v1/sante`) et le coût du jour, qu'il y ait ou non un visiteur. Deux passes ne se chevauchent jamais ;
une lecture en échec n'arrête ni les autres ni la tâche ; le journal ne porte qu'une ligne par
changement d'état d'une dépendance. Une jauge neuve, `application_sonde_derniere_passe_timestamp`,
dit que la sonde tourne.

| Commit | Objet |
|---|---|
| `0c0fe11` | **la sonde des dépendances ; application 0.9.2** — le correctif de l'incident |
| `97fe526` | les compteurs tracés existent à zéro dès le démarrage |
| `591c6c1` | le tableau « Application » affiche l'état courant ; règles I et J ; monitorage 0.3.0 |
| `6ee0e40` | fusion dans `develop` (demande n° 53) |
| `611043e` | livraison dans `main` (demande n° 55), **étiquette `e5-incident`** |

**Ce qui n'a pas changé** : le nom et le sens de chaque jauge existante ; `/sante`, qui lit toujours en
direct ; les expressions des règles F, G et H.

## 8. Preuve, avant et après

**Où l'« avant » a été mesuré** : en préproduction, le 13 novembre 2026, avec les mêmes images que la
production et le même serveur PostgreSQL. Le serveur n'a été arrêté en production qu'**une fois, après
le correctif** : c'est une décision, pour ne pas couper la production deux fois.

| Mesure | Avant — application 0.9.1 | Après — application 0.9.2 | Source |
|---|---|---|---|
| Test automatique `12_application/tests/test_sonde_incident.py` (base, catalogue, API, coût du jour ; aucune requête) | **4 échecs** (2026-11-16, code `4439873`) | **4 passés** ; joué par `make verify` | rapport, section 04 ; preuves `2026-11-16_C21_correctif_sonde` |
| Préproduction, serveur arrêté → jauges | **262 s**, après une requête | **18 s**, sans requête | sections 03 et 05 ; preuves du 13 et du 17 novembre |
| Préproduction, serveur arrêté → notifications | **367 s** (F), 372 s (G) | **113 à 118 s** (F, G, I) | idem |
| Préproduction, retour → jauge de la base | **255 s**, après une question | **11 s** | idem |
| Préproduction, retour → résolution de F notifiée | 290 s | **46 s** | idem |
| Préproduction, catalogue arrêté → jauge / notification | 251 s / 376 s | **8 s / 103 s** | idem |
| Préproduction, coût du jour remis → jauge | figée ≈ 50 min, jusqu'à une question | **4 s** | idem |
| **Production**, serveur arrêté une fois (17 novembre, 03:05:53) → jauges | non mesuré (voir plus haut) | **22 s** (base, catalogue), **37 s** (API) | section 05, § K ; preuves `2026-11-17_C21_incident_rejoue` |
| Production → notifications | — | **122, 127, 157 s** (F, G, I) | idem |
| Production, retour → jauge de la base ; résolution de F notifiée | — | **15 s ; 55 s** | idem |

Sans visiteur, de l'arrêt à la résolution des alertes. En préproduction comme en production, aucun
conteneur n'a été relancé ; les trois services ont retrouvé leur base seuls ; seules les règles qui
décrivent la panne ont notifié (18 courriels en tout, 12 en préproduction, 6 en production).

## 9. Prévention et suites

**Fait** : la règle **I** (l'API du modèle dégradée vue de l'application, pendant 1 min) — le 13
novembre, l'API restait une cible relevée sans pouvoir chercher, et la règle A ne voyait rien ; la règle
**J** (la sonde ne tourne plus : dernière passe au-delà de quatre périodes, 60 s), dont le seuil suit la
période par un test. Une application absente déclenche E, et J se résout alors : pas de double alerte
pour une même panne.

**Reste à faire, non fait** :

- **un contrôle extérieur à la machine** (un signal de vie sortant) : un poste éteint ou une pile
  arrêtée ne prévient personne — constaté le 12 novembre ;
- **une politique de redémarrage** sur un serveur : la pile ne repart pas seule après l'arrêt de la
  machine ;
- **la rotation des journaux des conteneurs** : aucune n'est configurée ; la sonde ajoute à elle seule
  environ 1,7 Mo par jour au journal de l'API du modèle sur le poste ; reportée au serveur de janvier ;
- **le serveur PostgreSQL unique** : son arrêt coupe les trois services ; la panne de la seule base des
  comptes n'a pas pu être mesurée sur ce poste ;
- **la reconnexion du catalogue et de l'API** : après le retour du serveur, ils ont mis 62 à 94 s à
  retrouver leur base (le pool du catalogue réessaie avec un délai croissant) ; la sonde les a suivis
  dès leur retour, mais le service rendu a attendu ce délai ;
- **les constructions d'images non reproductibles** : deux constructions du même code donnent deux
  empreintes, et l'outil de livraison ne sait pas construire une seule image ; à traiter avant le
  serveur de janvier ;
- **la latence de la première question** n'est pas dessinée par le tableau : la série naît avec la
  question et le calcul ignore son premier relevé.

## 10. Limites de la preuve

- **Un poste, pas un serveur** : préproduction et production partagent la machine, la carte graphique
  et le serveur PostgreSQL.
- **Peu de trafic** : aucun visiteur pendant les pannes — c'est la condition de la preuve — et une seule
  question réelle, après la panne de production.
- **Une panne franche** (le serveur arrêté), **pas une base lente** : une base qui répond au-delà des
  délais de la sonde (3 à 10 s selon la dépendance) n'a pas été éprouvée.
