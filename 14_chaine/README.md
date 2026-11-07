# La chaîne de livraison du modèle (module 14)

La chaîne enchaîne, en une commande, les gestes qui existent déjà un par un dans les
modules 05, 09, 10 et 11 : tester les données, encoder le corpus, tester l'encodage,
mesurer, appliquer la règle de promotion, construire l'image de l'API. Elle s'arrête
**avant** la promotion, avec un rapport de livraison versionné.

- **Le modèle qu'elle porte** : le modèle d'embedding, celui qui transforme les textes du
  corpus en vecteurs. Un nouvel encodage du corpus, c'est l'« entraînement » de ce projet.
  Le modèle de langage n'est pas livré par la chaîne : c'est une dépendance surveillée.
- **Ce qu'elle ne fait jamais** : promouvoir un encodage, ni viser la base de production.
  La promotion et le retour arrière sont des gestes de Max, en préproduction.

## Les trois environnements

Un environnement, c'est une base et les fichiers qui vont avec.

| Environnement | Où | Sert à | La chaîne y écrit |
|---|---|---|---|
| développement | bases jetables des tests (`make verify`) | les tests, dont ceux de ce module | oui, et ces bases sont jetées après |
| préproduction | la base `apimanga_preprod`, copie de la production sur le même serveur, et le dossier `09_service_embedding/mesures/equivalence_preprod/` | les passages réels de la chaîne, les promotions, les retours arrière | oui |
| production | la base `apimanga` et le dossier `09_service_embedding/mesures/equivalence/` | l'API et l'application en service | **jamais** : elle n'est que lue |

**Les fichiers suivent la base.** Un passage de préproduction écrit ses échantillons
d'équivalence dans le dossier de la préproduction. Il n'écrit jamais dans celui que lisent
la sonde du monitorage et `make verify-poste`.

## Les étapes

| N° | Étape | Tâche | Commande appelée | S'exécute si | Code d'arrêt |
|---|---|---|---|---|---|
| 0 | Préalables | cible = préproduction ; dossier d'export de la préproduction ; arbre propre et poussé ; candidat commité et conforme ; pile du monitorage arrêtée ; instance d'embedding en marche, `/info` conforme ; état de départ de la préproduction | git, docker, `/info`, psql | toujours | 20, 21 |
| 1 | Tester les données | jeu d'évaluation gelé ; empreintes du corpus ; corpus reconstruit à blanc contre sa règle, pseudonymes compris | `corpus.construire_v2 --dry-run` (05) | toujours | 30 |
| 2 | Construire | la nouvelle version du corpus | `corpus.construire_v2` (05) | le candidat déclare une nouvelle version | 40 |
| 3 | Encoder | un nouvel encodage du corpus, sur la carte graphique | `service_embedding.encodage` (09) | le candidat demande un nouvel encodage | 50 |
| 4 | Tester le modèle | échantillon exporté dans le dossier de la préproduction, puis test d'équivalence | `service_embedding.equivalence exporter`, `comparer` (09) | toujours | 60 |
| 5 | Évaluer | mesure sur le jeu gelé ; rejeu d'une référence si le candidat en déclare une | `mesures_recherche.executer` (10) | toujours | 70 |
| 6 | Valider | la règle de promotion, commitée avant la mesure | `mesures_recherche.promotion` (10) | toujours | 10, 11 |
| 7 | Packaging | l'image de l'API, construite au commit ; son empreinte | `docker compose build api` (11) | verdict « promotion proposée » | 80 |
| 8 | Rapport et arrêt | le rapport de livraison, attaché au run MLflow ; la chaîne s'arrête là | `mlflow artifacts log-artifact` (10) | toujours, même après un arrêt | — |
| 9 | Déployer (Max) | la promotion au journal de la préproduction ; l'API de préproduction levée ; `/v1/info` désigne l'encodage promu | `journal_promotions promouvoir` (10) ; Compose du 11 | geste de Max, sur « promotion proposée » | 20, 80 |
| 10 | Vérifier | encodage 3 : équivalence de l'API, 69 questions sur 69 ; encodage 2 : la mesure 8 rejouée, 104 métriques sur 104 | `tests/test_egalite_banc.py` (11) ; `mesures_recherche.executer --rejeu-de` (10) | après 9 et après R | 70 |
| R | Revenir (Max) | le retour arrière au journal de la préproduction ; l'API levée ; la vérification | `journal_promotions revenir` (10) | geste de Max | 20, 70, 80 |

Aucune logique des modules appelés n'est recopiée : chaque étape lance leur commande, dans
leur environnement (`uv run` dans le module), et lit leur sortie.

## Les déclencheurs

Un déclencheur est ce qui lance quelque chose : un geste, un événement, une heure. Ils ont
été définis et datés le 7 novembre 2026, avant le code ; un test de ce module vérifie que ce
tableau dit vrai.

| N° | Déclencheur | Où il est configuré | Ce qu'il lance | Ce qu'il produit |
|---|---|---|---|---|
| 1 | chaque commit | `.githooks/pre-commit` | mentions interdites, fichiers exclus, clés et jetons | le commit, ou un refus |
| 2 | chaque push | `.githooks/pre-push` | auteur et validateur, mentions, fichiers interdits, clés, noms des branches et des étiquettes | le push, ou un refus |
| 3 | une demande de fusion (ouverte, mise à jour, rouverte, modifiée) | `verify.yml` (`pull_request`) | `perimetre`, une tâche `verify <module>` par module concerné, `statique` | les contrôles obligatoires ; fusion bloquée si l'un est rouge |
| 4 | un push sur `develop` | `verify.yml` (`push`, `develop`) | les mêmes tâches | le statut de `develop` |
| 5 | à la main, sur GitHub | `verify.yml` (`workflow_dispatch`), `indicatif.yml` (`workflow_dispatch`) | les mêmes tâches, ou Bandit et pip-audit | un statut |
| 6 | chaque lundi, à 06:00 UTC | `indicatif.yml` (`schedule`, `0 6 * * 1`) | Bandit et pip-audit sur tous les modules | des rapports indicatifs |
| 7 | à la main, sur le poste | `Makefile` (`preprod-creer`) | la copie de la production vers `apimanga_preprod`, et ses contrôles | la préproduction, prête ou recréée |
| 8 | à la main, sur le poste, quand une règle de corpus ou une configuration du modèle change | `Makefile` (`chaine`), `14_chaine/candidats/*.toml` | les étapes 0 à 8, en préproduction | un rapport de livraison, un run MLflow, une ligne `eval_runs` de préproduction, un code de sortie ; jamais de promotion |
| 9 | Max : la promotion | `Makefile` (`chaine-promouvoir`) | les étapes 9 et 10 | une ligne au journal de la préproduction ; l'API vérifiée |
| 10 | Max : le retour arrière | `Makefile` (`chaine-revenir`) | l'étape R | une ligne au journal de la préproduction ; la vérification |
| 11 | à la main, avant une demande vers `main` | `Makefile` (`verify-poste`, `verify-mutations`) | l'état de la production (en lecture seule), les équivalences, les pseudonymes, les mutations | un feu vert, ou un arrêt |

**Ce qui n'est pas un déclencheur, mais peut motiver le 8** : une alerte du monitorage
(l'équivalence du service d'embedding, des sorties refusées), une règle de corpus modifiée,
un modèle à essayer. C'est la boucle de retour : le monitorage signale, un humain lance la
chaîne. Le déclenchement est manuel par choix : un déclencheur planifié réencoderait un
corpus qui n'a pas bougé, et un exécuteur GitHub sur le poste ferait tourner du code
extérieur, le dépôt étant public.

Les deux passages de l'étape 6 d'E3 sont lancés pour démontrer la chaîne, pas parce qu'une
règle a changé.

## Installation

1. **Les outils** : `uv`, Docker avec Docker Compose, la carte graphique et son pilote (le
   service d'embedding tourne dessus), le client PostgreSQL (`psql`, `pg_dump`,
   `pg_restore`, `createdb`, `dropdb`).
2. **L'environnement du module** : `cd 14_chaine && uv sync --locked --extra dev`. La
   chaîne n'a aucune dépendance à l'exécution : la bibliothèque standard de Python suffit.
3. **Les mots de passe**, dans `~/.pgpass`. Un mot de passe appartient au rôle, pas à la
   base : chaque ligne nouvelle copie la ligne `apimanga` du même rôle, et seul le nom de
   la base change. La chaîne vérifie ces lignes par une connexion ; elle n'ouvre jamais le
   fichier. Leur forme, sans le mot de passe :

   ```text
   localhost:5432:apimanga_preprod:postgres:<mot de passe du rôle postgres>
   localhost:5432:apimanga_preprod:verif_lecture:<mot de passe du rôle verif_lecture>
   localhost:5432:apimanga_preprod:modele_api:<mot de passe du rôle modele_api>
   localhost:5432:postgres:postgres:<mot de passe du rôle postgres>
   ```

   La dernière ligne ouvre la base d'entretien du serveur, celle où l'on se connecte pour
   créer ou supprimer une base.
4. **Le fichier de mots de passe du conteneur de l'API de préproduction** : une copie à
   part de celui de l'API de production, où seul le nom de la base change
   (`host.docker.internal:5432:apimanga_preprod:modele_api:<mot de passe>`). Son chemin se
   donne par la variable `PREPROD_PGPASS_CONTENEUR`, jamais dans le dépôt. Le serveur
   PostgreSQL doit admettre `modele_api` dans `apimanga_preprod` depuis le réseau de l'API
   (`172.30.11.0/24`) : une ligne de `pg_hba.conf`, au modèle de celle d'`apimanga`.
5. **Le dossier d'export de la préproduction** : `make` le donne lui-même, par la variable
   `EQUIVALENCE_DOSSIER` (`09_service_embedding/mesures/equivalence_preprod/`, ignoré par
   git). La chaîne refuse de démarrer sans lui, ou avec le dossier de la production.
6. **La préproduction** : `make preprod-creer`. La commande copie `apimanga` dans
   `apimanga_preprod` (`pg_dump` puis `pg_restore`, sous `postgres`), puis contrôle la copie
   et la production contre `verification/etat_apimanga.tsv`. Si la préproduction existe,
   elle est supprimée et recréée : c'est une réinstallation de l'environnement. Aucun autre
   nom de base n'est admis.

## Configuration : le fichier de candidat

Un candidat est un fichier TOML de `14_chaine/candidats/`. Il dit ce que la chaîne doit
juger. Son schéma est fermé : une clé inconnue, une clé manquante ou un type faux arrête
la chaîne (code 21). Il doit être commité et identique à `HEAD` (sinon, code 20).

| Clé | Type | Sens |
|---|---|---|
| `nom` | texte | le nom du candidat, égal au nom du fichier |
| `motif` | texte | pourquoi on le lance |
| `[corpus] version` | texte | la version du corpus jugée (`v2`) |
| `[corpus] nouvelle_version` | booléen | `true` : l'étape 2 construit cette version |
| `[encodage] nouveau` | booléen | `true` : l'étape 3 encode ; `false` : un encodage existant est réutilisé |
| `[encodage] instance` | texte | l'instance du service d'embedding (`bge-m3`, `embeddinggemma`) |
| `[encodage] numero` | entier | l'encodage existant (seulement si `nouveau = false`) |
| `[encodage] modele`, `revision`, `precision` | texte | ce que l'instance doit servir (seulement si `nouveau = true`) |
| `[mesure] numero` | entier | le numéro de la configuration dans `10_mesures_recherche/config/mesures.toml` |
| `[mesure] reference_run` | texte, facultatif | un run MLflow que la mesure doit reproduire à l'identique |
| `[regle] fichier` | texte | la règle, le fichier unique du module 10 |
| `[preproduction] encodage_en_service` | entier | l'encodage que la préproduction doit servir au départ |

**La règle** est `10_mesures_recherche/config/promotion_corpus_v2.toml`, commitée le
10 octobre 2026, avant toute mesure de la chaîne : au moins 31 questions sur 59 au global et
20 sur 23 en reconnaissance. Elle ne juge que le corpus v2, et jamais l'encodage en service.

**Les deux candidats de l'étape** :

| Candidat | Ce qu'il fait | Attendu |
|---|---|---|
| `refus_bgem3_v2` | encode le corpus v2 avec BGE-M3, l'autre modèle du banc, et le mesure (configuration 7) | un refus (code 10) : sur le corpus v1, ce modèle rendait 22 sur 59 et 16 sur 23 |
| `requalification_v2` | réutilise l'encodage 3, retiré du service en préproduction, et rejoue sa mesure (configuration 8) | une promotion proposée (code 0) : 31 sur 59, 21 sur 23, identique au run de référence |

## Tester la chaîne

- **Les tests** : `make verify MODULE=14`. Ils tournent avec des étapes factices : aucun
  service, aucune base. Ils prouvent l'ordre des étapes, l'arrêt au premier échec, les
  refus, le refus de la production, que `lancer` ne promeut jamais, et la lecture des
  vraies sorties des modules (dossier `tests/sorties_reelles/`).
- **Le passage à blanc** : `make chaine CANDIDAT=14_chaine/candidats/refus_bgem3_v2.toml A_BLANC=1`.
  Il affiche les commandes, leurs arguments, la base visée et ce que chaque commande
  écrirait, sans rien lancer.
- **Sur GitHub** : la tâche `verify 14` joue les mêmes tests à chaque demande de fusion.

## Lancer un passage, et les gestes de Max

```bash
make chaine CANDIDAT=14_chaine/candidats/refus_bgem3_v2.toml
make chaine-promouvoir LIVRAISON=<horodatage>_<candidat> MOTIF="…"
make chaine-revenir MOTIF="…"
make chaine-etat
```

- Un passage écrit son rapport dans `14_chaine/livraisons/`. **Un passage = un rapport
  commité** avant le suivant : la chaîne refuse un arbre qui n'est pas propre.
- Le `.md` se lit : les nombres à la française (quatre décimales pour les mesures, huit
  pour le cosinus minimal, une pour les durées, la mémoire en Go), la reconnaissance et la
  proposition avec leur dénominateur, une ligne par étape de 0 à 7, « non lancée » avec sa
  raison. Dans le motif d'un arrêt, texte libre, seuls les réels à cinq décimales ou plus
  (huit décimales) et les dictionnaires (dits en mots, valeurs entières) sont réécrits.
  Le `.json` garde les valeurs brutes, pour la commande de promotion.
- La promotion exige un rapport commité et poussé, au verdict « promotion proposée », et un
  motif écrit par Max. Le motif n'a jamais de valeur par défaut, et il ne porte pas de
  guillemet droit.
- La lecture du rapport, le déploiement, le retour arrière et la procédure de mise en
  production seront décrits avec les passages réels.

## Les codes de sortie, et que faire

| Code | Nom | Sens | Que faire |
|---|---|---|---|
| 0 | `PROMOTION_PROPOSEE` | toutes les étapes tenues, règle tenue | commiter le rapport ; Max lit, et promeut ou non |
| 10 | `REFUS_REGLE` | la règle n'est pas tenue | commiter le rapport ; aucune promotion : le rapport nomme les conditions qui manquent |
| 11 | `PAS_DE_VERDICT` | la règle refuse de juger (encodage en service, autre corpus) | changer l'état de départ (retour arrière en préproduction) ou le candidat |
| 20 | `PREREQUIS` | un préalable manque | lire le motif : cible, dossier, arbre, candidat, pile, instance, état de départ, mot de passe |
| 21 | `CANDIDAT_INVALIDE` | le candidat ne suit pas son schéma | corriger le fichier, le commiter |
| 30 | `DONNEES` | le test des données a échoué | ne pas passer outre : lire le rapport du module 05 |
| 40 | `CONSTRUCTION` | la construction du corpus a échoué | lire le rapport du module 05 |
| 50 | `ENCODAGE` | l'encodage a échoué, ou n'a rien créé | lire le bilan de l'encodeur (dossier de travail du passage) |
| 60 | `TEST_MODELE` | équivalence de l'encodage sous 0,99 | ne pas mesurer : comparer l'instance au registre |
| 70 | `MESURE` | la mesure a échoué, ou le rejeu n'est pas identique | lire la sortie de la mesure ; un rejeu différent est un résultat à comprendre |
| 80 | `PACKAGING` | l'image, ou l'API de préproduction, a échoué | lire la sortie de Docker Compose |
| 90 | `INTERNE` | erreur inattendue de la chaîne | lire le motif ; c'est un défaut de la chaîne |

Un refus de la règle n'est pas une panne : toutes les étapes ont tourné, la dernière a dit
non.

## Les rôles

Chaque étape qui écrit le fait **en préproduction, sous le rôle `postgres`** : l'encodeur
(module 09 : l'encodage et ses vecteurs), le banc (module 10 : `eval_runs`,
`eval_mesures`), le journal des promotions (module 10). Les lectures de contrôle se font
sous `verif_lecture`, en lecture seule ; la vérification de l'API sous `modele_api`. MLflow
écrit dans son magasin local (`10_mesures_recherche/mlflow/`), expérience
`E3-chaine-preprod`. Un rôle limité à l'insertion dans `eval_runs` et `eval_mesures` est
prévu (dette, migration 027).

Aucun secret n'entre dans un candidat ni dans un rapport. La clé de l'API, lue par
`chaine-promouvoir` et `chaine-revenir` dans le `.env` du module 11, n'est jamais affichée
ni écrite.

## Limites

- Le déclenchement est manuel, par choix (voir les déclencheurs).
- Une seule machine : la préproduction est une base du même serveur que la production.
- Une seule instance d'embedding à la fois sur la carte : la pile du monitorage est arrêtée
  pendant un passage.
- Pas de registre d'images distant : l'image de l'API est locale, son empreinte fait foi.
- La règle de promotion ne juge que le corpus v2 ; une nouvelle version du corpus
  demandera une règle qui la juge, écrite avant sa mesure.
- La mise en production d'un candidat n'est pas faite par la chaîne.
