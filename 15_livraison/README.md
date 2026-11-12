# 15_livraison — la livraison continue

`livrer` construit les images de la pile **à une étiquette**, les déploie sur une
**cible** (`staging` ou `production`), vérifie la **santé** de ce qui tourne, écrit un
**journal de déploiement** versionné, et sait **revenir en arrière** vers une étiquette
précédente. Une seule commande, paramétrée par sa cible ; la cible « serveur » est la
même commande sur un autre hôte.

```bash
make livrer ACTION=construire                      # RECONSTRUIRE=1, A_BLANC=1
make livrer ACTION=deployer CIBLE=staging          # A_BLANC=1 : ce qui serait fait
make livrer ACTION=sante    CIBLE=production       # ETIQUETTE=… : la référence jugée
make livrer ACTION=retour   CIBLE=production VERS=chaine-livraison MOTIF="…"
make livrer ACTION=arreter  CIBLE=staging
make livrer-etat                                   # les deux cibles
```

Codes de sortie : `0` fait et vert ; `1` déployé mais un contrôle de santé manque ; `20`
un préalable manque ; `40` une construction échoue ; `80` la pile ne se lève pas ou ne
répond pas ; `90` erreur inattendue, ou un journal qui porterait un secret.

## Le chemin d'un commit jusqu'à la production

| # | Étape | Commande ou geste | Preuve laissée |
|---|---|---|---|
| 1 | Un commit sur une branche `feature/…` | `git commit` ; les hooks `.githooks/` refusent auteur, mention, fichier exclu, clé | l'historique de la branche |
| 2 | Une demande vers `develop` | `gh pr create` ; la chaîne `verify` joue une tâche par module et la tâche `statique` | les contrôles de la demande sur GitHub |
| 3 | La fusion vers `develop` | par le mainteneur, dans l'interface (branches protégées, aucune exemption) | le commit de fusion |
| 4 | La livraison vers `main` | demande `develop → main` après `make verify-poste` et la sauvegarde de la base ; fusion par le mainteneur ; **étiquette annotée** poussée | l'étiquette, `main` et `develop` alignées |
| 5 | Le staging | depuis `main` à l'étiquette : `make livrer ACTION=construire`, puis `ACTION=deployer CIBLE=staging` ; vérification par l'interface ; `ACTION=arreter CIBLE=staging` | `journal/<horodatage>_staging_<étiquette>.md` |
| 6 | La production | les migrations en attente, appliquées par le mainteneur après sa sauvegarde (`database/README.md`) ; `make livrer ACTION=deployer CIBLE=production` | `journal/<horodatage>_production_<étiquette>.md`, et Grafana |

**Construire** exige un arbre propre, sur `main`, exactement à une étiquette annotée
(`git describe --exact-match`). **L'arbre doit être propre, les journaux exceptés : ils
sont ce que la commande écrit** — `git status --porcelain` est jugé hors de
`15_livraison/journal/` (journaux non suivis et `index.md` modifié admis là, et là
seulement) ; tout autre écart refuse, et le refus nomme le premier fichier en cause. Les
journaux ne participent à aucune image ni à aucun Compose : ce que l'étiquette décrit
reste ce qui tourne. Les versions viennent des `pyproject.toml` de l'étiquette
(02, 11, 12), et le Compose de l'étiquette doit désigner les mêmes. **Une étiquette = une
construction** : chaque image est construite sous une étiquette provisoire
(`<version>-candidat`), puis son empreinte est comparée à celle de l'étiquette d'image
existante. Si elles sont égales, rien ne change ; si elle est absente, l'image est
étiquetée ; si elles diffèrent, **refus avant toute réécriture**, sauf `RECONSTRUIRE=1`.
La réécriture est alors notée avec l'ancienne empreinte, et le journal du déploiement
suivant la reprend.

**Dette — constructions reproductibles.** Deux constructions de la même étiquette
donnent deux empreintes (horodatages des couches) : toute construction d'une étiquette
d'image déjà présente refuse, et `RECONSTRUIRE=1` est de règle, journalisé avec
l'ancienne et la nouvelle empreinte. La règle « une étiquette = une construction » est
donc tenue **par le journal**, pas par l'empreinte. La rendre vraie par l'empreinte
demande des images reproductibles (`SOURCE_DATE_EPOCH`, couches sans horodatage) :
étape du serveur privé virtuel.

**Déployer** vérifie d'abord, sans rien écrire :

- l'étiquette ;
- les images présentes ;
- **l'autre cible arrêtée** ;
- les fichiers d'environnement présents (jamais ouverts) ;
- **zéro migration en attente** sur la base de la cible : `livrer` ne migre jamais.

Puis il lance `docker compose … up -d --no-build`, attend `/sante` (180 s : l'API charge
le corpus), vérifie la santé et écrit le journal. **Une santé rouge n'arrête pas la
pile** : on la regarde. Le code de sortie vaut 1, le journal porte le verdict, et la suite
est un retour arrière proposé au mainteneur.

**La santé** (`ACTION=sante`) donne une ligne par contrôle, et le code 1 si l'un manque :

- les images servies, une par service, à la version de l'étiquette ;
- `/sante` de l'application : version, API, base, catalogue, `cles_api` (`deux` en
  production ; `une` admise en staging), profil actif ;
- `/health` du catalogue : base, `normalisation` égale à `fonction` ;
- `/v1/sante` et `/v1/info` de l'API, lus **dans son conteneur** (l'API n'est pas
  publiée) : la clé reste dans le conteneur, `livrer` ne reçoit que quatre champs ;
- l'encodage servi, égal à l'encodage en service de la base ;
- `migrate.py status` : 0 en attente ;
- en production, toutes les cibles de Prometheus `up`, **sur un relevé postérieur au
  lever**.

**Prometheus juge sur un relevé postérieur au lever.** Juste après un `up`,
`/api/v1/targets` porte encore, un temps, le relevé d'avant — celui du conteneur quitté,
`down` comme `up`. Après un déploiement ou un retour, une cible n'est donc jugée que sur un
relevé commencé après l'instant où `/sante` a répondu (`lastScrape`, heure murale UTC) ;
tant qu'il en manque un, la santé relit `/api/v1/targets`. La borne de cette attente se
dérive de Prometheus, elle ne se recopie pas : le plus grand `scrapeInterval +
scrapeTimeout` de ses cibles, plus une marge de 5 s (aujourd'hui 15 + 10 + 5 s) ; sans ces
champs, l'intervalle écrit dans `13_monitorage/prometheus/prometheus.yml` à l'étiquette ;
sinon, la santé juge sans attendre et le dit. Une cible `down` sur un relevé postérieur est
un manque réel ; une cible sans relevé postérieur à la borne est « non relevée depuis le
lever ». `livrer sante`, seul, n'a pas d'instant de référence : une lecture.

**La référence de `livrer sante`** est ce qui a été déployé, pas l'arbre de travail :
l'étiquette du **dernier journal de la cible**, quel que soit son verdict — après un retour
arrière, l'étiquette visée. `ETIQUETTE=…` (`--etiquette`) l'impose ; sans journal pour la
cible, l'étiquette de HEAD ; sans l'une ni l'autre, refus (code 20) qui demande
`--etiquette`. L'étiquette retenue doit être annotée et connue. La dernière ligne dit la
référence et d'où elle vient :

```text
Santé production (e4-livraison-journal — dernier journal de la cible) : verte.
```

Les deux autres provenances : `— demandée`, `— étiquette de HEAD, aucun journal pour la
cible`.

## Revenir en arrière

```bash
make livrer ACTION=retour CIBLE=production VERS=chaine-livraison MOTIF="…"
make livrer ACTION=deployer CIBLE=production     # puis le retour à l'étiquette courante
```

- **Quand** : une santé rouge après un déploiement, ou une régression vue en production
  — sur décision du mainteneur, jamais d'office.
- **Comment** : la cible est arrêtée, puis relevée avec **les fichiers Compose et les
  images de l'étiquette visée**. Les fichiers sont lus par `git show` dans un dossier
  temporaire ; les chemins relatifs restent ceux du dépôt, par `--project-directory`. Une
  image absente du poste est construite depuis un arbre de travail temporaire à cette
  étiquette, retiré ensuite, et c'est journalisé. La santé est jugée **aux versions de
  l'étiquette visée** : pour `chaine-livraison`, l'API 1.4.0 et l'application 0.1.0, sans
  catalogue ni comptes.
- **Ce qui ne revient pas** : les migrations de la base. Il n'y a pas de `down` : une
  migration se corrige en avant, ou la base se restaure depuis une sauvegarde, sur
  décision humaine. Le journal du retour le dit, avec le nombre de migrations appliquées.
  Les migrations d'E4 sont additives : la version précédente fonctionne sur la base
  migrée.
- **Images de l'oral d'E3** : `api-modele:1.4.0` et `application-conseiller:0.1.0`.
  `livrer` ne supprime jamais une image ; il ne retire que ses étiquettes provisoires
  `…-candidat` (tests : la construction, et l'enchaînement déployer, arrêter, revenir,
  redéployer).
- **Joué en production le 2026-11-11**, le mainteneur présent : le retour vers
  `chaine-livraison` en **42 s** (l'application d'E3 de nouveau sur 8020, la base restée
  à 30 migrations), puis le redéploiement d'`e4-livraison-1` en **23 s** ; comptes,
  critiques et coûts intacts. Les deux journaux sont dans `journal/`.

## Les deux cibles sur un poste

| | `staging` | `production` |
|---|---|---|
| Projet Compose | `senpai-staging` | `api-modele` (la pile du monitorage) |
| Base | `apimanga_preprod` | `apimanga` |
| Fichiers Compose | 11 + 12 | 11 + 12 + 13 |
| Application, catalogue | `127.0.0.1:8021`, `127.0.0.1:8001` | `127.0.0.1:8020`, `127.0.0.1:8000` |
| Prometheus, Grafana | absents | `127.0.0.1:9090`, `127.0.0.1:3000` |
| Clé de session | `12_application/.env.staging` (exigé) | `12_application/.env` |
| Fichier de mots de passe de l'API | `PREPROD_PGPASS_CONTENEUR` (celui de la chaîne) | celui du module 11 |

**Une cible à la fois** : les deux partagent la carte graphique, le conteneur
`tei-api-modele` (nom fixe) et les sous-réseaux fixes qu'admet `pg_hba.conf`. Déployer
l'une est refusé tant que l'autre tourne. C'est une limite du poste ; sur un serveur, il
y a deux hôtes.

**Le fichier de cible** (`cibles/<nom>.env`) ne porte que ce qui diffère : nom de projet,
base (dans `DB_NAME` et dans les DSN des conteneurs, sans mot de passe), ports,
environnement de l'application, monitorage ; en staging, aucun contrôle des modèles au
démarrage (`API_MODELE_VERIFIER_MODELES=false`, comme le 13 en production). Il est passé
**en dernier** à `docker compose --env-file`, après ceux des modules (11, 12, 02 ; 13 en
production s'il existe) et `12_application/.env.<cible>` s'il existe. **Aucun secret** :
un test y cherche les noms de secrets des fichiers d'exemple des modules, et les motifs du
hook y sont rejoués.

**Ce que le fichier de cible ne peut pas surcharger** : le contenu d'un fichier de mots de
passe monté. Le staging passe donc le chemin du fichier de la préproduction par
l'environnement du processus (`API_MODELE_PGPASS`, depuis `PREPROD_PGPASS_CONTENEUR`).
Celui de l'application (`12_application/.pgpass.conteneur`) porte une ligne par base.

Les variables de l'application, telles que `12_application/src/application/config.py`
les lit — la liste contre laquelle relire les fichiers d'exemple :
`APPLICATION_CLE_API`, `APPLICATION_CLE_API_COMPTES`, `APPLICATION_ENVIRONNEMENT`,
`APPLICATION_BASE_DSN`, `APPLICATION_CLE_SESSION`, `APPLICATION_CLE_CATALOGUE`,
`CATALOGUE_ADRESSE`, `API_MODELE_ADRESSE`, `APPLICATION_ECOUTE`,
`APPLICATION_METRIQUES_RESEAUX`. Le Compose lit en plus : `APPLICATION_PORT_PUBLIE`,
`CATALOGUE_PORT_PUBLIE`, `APPLICATION_BASE_DSN_CONTENEUR`, `APPLICATION_PGPASS`,
`CATALOGUE_PGPASS`.

## La cible serveur — procédure écrite, non jouée

La même commande, sur un autre hôte : soit lancée **sur le serveur** depuis un clone du
dépôt à l'étiquette, soit depuis le poste avec `DOCKER_HOST=ssh://<utilisateur>@<hôte>`
(le démon distant reçoit les commandes ; `livrer` reste local). Ce qui change :

1. **Les fichiers d'environnement** des modules sont ceux du serveur, créés sur lui à
   partir des fichiers d'exemple, avec ses propres clés. Rien ne passe par le dépôt.
2. **Les fichiers de mots de passe** des conteneurs (API, catalogue, application) visent
   le PostgreSQL du serveur. `pg_hba.conf` y admet les trois sous-réseaux fixes, rôle par
   rôle et base par base.
3. **Les ports** restent publiés sur `127.0.0.1` ; un proxy inverse TLS (Caddy ou nginx)
   publie l'application seule en HTTPS, et Grafana derrière une authentification.
4. **Deux hôtes** (ou deux machines virtuelles) pour staging et production : la limite
   « une cible à la fois » disparaît, chaque fichier de cible garde ses ports.
5. **Le mode processeur** si le serveur n'a pas de carte graphique : réglage
   `COMPOSE_FILE` du module 11 (README du 11).

Le choix de l'hébergeur (un serveur privé virtuel, prévu en janvier) est hors de ce
module.

## Le journal

`journal/<horodatage UTC>_<cible>_<étiquette>.md`, un fichier par déploiement ou retour,
et `journal/index.md` (une ligne chacun). Un arrêt s'ajoute au dernier journal de la
cible. Chaque journal porte :

- qui (l'utilisateur du poste) et quand ;
- l'étiquette et son commit ;
- les images et leurs **empreintes** (et une réécriture, avec l'ancienne) ;
- l'état des migrations avant et après ;
- le tableau de santé, et l'attente d'un relevé de Prometheus postérieur au lever ;
- deux durées : **« Pile prête »**, du lancement des commandes Compose à `/sante` 200, et
  **« Durée »**, le geste entier, santé et attente de Prometheus comprises (les durées des
  journaux d'avant la 0.1.2 — 37 s, 42 s, 23 s — sont des « Durée ») ;
- les appels facturés comptés par l'API, avant et après. **Après un lever, le compteur ne
  vient que du conteneur levé** : `/metrics` lu dans son conteneur (hors production) ; en
  production, où `/metrics` n'admet que le réseau de collecte (404 dans le conteneur), la
  valeur de Prometheus n'est acceptée que si l'API y a un relevé réussi postérieur au lever
  — sinon « non relevé », jamais un chiffre de l'ancien conteneur. Si la pile n'a jamais
  répondu, l'instant de référence est la fin des commandes Compose ;
- pour un retour : le motif, ce qui a été quitté, et la phrase sur les migrations.

**Les étiquettes de livraison d'E4.** `e4-livraison` est la première étiquette de
livraison, module 15 en 0.1.0 : le staging ne s'y est pas levé, et le premier journal
rendait l'arbre « sale » pour le geste suivant (le journal dans l'arbre, la propreté
exigée). `e4-livraison-1` porte le correctif 0.1.1 ; c'est l'étiquette de la production.
Une étiquette ne se déplace pas : `e4-livraison` reste, l'histoire se lit.

**Versionné**, parce que c'est la preuve d'un déploiement effectif : on le relit dans
l'historique, on le commite après le déploiement. **Aucun secret, aucune adresse autre
que `127.0.0.1`** : avant d'écrire, `livrer` relit le texte avec les motifs du hook
lui-même (`.githooks/cles-fournisseurs`, `.githooks/controler-texte`). Un motif trouvé, et
le journal n'est pas écrit.

## Limites connues

- **`main` doit être à une étiquette pour déployer.** Les journaux commités après une
  livraison avancent `main` au-delà de l'étiquette : un nouveau déploiement demande une
  étiquette annotée sur ce commit.

**Corrigé en 0.1.2.** Les cinq journaux de production « santé rouge » — quatre le
2026-11-11 (trois déploiements, un retour arrière), un le 2026-11-12 — le sont sur le seul
contrôle « cibles up » : la santé lisait Prometheus avant son relevé suivant, pour une pile
saine au relevé d'après (le 12, `livrer sante` rejoué 30 s plus tard : vert). La 0.1.2 juge sur un relevé postérieur au lever (« La santé »),
prend pour référence de `livrer sante` le dernier journal de la cible, et ne prend plus le
compteur « après » du conteneur quitté. **La preuve réelle sera le premier déploiement de
production en 0.1.2**, à la livraison `e5-monitorage`. Les journaux écrits ne se réécrivent
pas.

## Vérifier

```bash
make verify MODULE=15    # Ruff, tests sur doubles, Pyright strict, Bandit, pip-audit
```

Les tests remplacent `git`, `docker`, Compose, `psql`, `uv` et le bouclage par des doubles.
Aucune commande réelle ne tourne, sauf la relecture par le hook, jouée dans un dépôt
jetable — et `git`, réel, dans `tests/test_arbre_reel.py` : un outil qui écrit dans
l'arbre se teste sur un dépôt réel, deux gestes à la suite. Les doublures reprennent ce que
le poste rend réellement : `/api/v1/targets` et `/api/v1/query` de Prometheus dans la forme
relevée le 2026-11-12, `/metrics` à 404 dans le conteneur de l'API en production. Mutations
`E4V-01` à `E4V-06` et `E5V-01` à `E5V-04` (`verification/mutations/`) :

- la santé qui passerait avec un service manquant ;
- `construire` sur un arbre sale ;
- `deployer` qui ignorerait l'autre cible ;
- un journal sans empreintes ;
- un retour qui toucherait aux migrations ;
- le filtre des journaux qui admettrait tout fichier de l'arbre ;
- la santé qui jugerait une cible de Prometheus sans relevé postérieur au lever ;
- une cible `down` sur un relevé postérieur qui passerait pour tenue ;
- `livrer sante` qui ignorerait le dernier journal et jugerait à l'étiquette de HEAD ;
- le compteur « après » qui accepterait une valeur de Prometheus sans relevé postérieur
  de l'API.
