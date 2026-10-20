# Contribuer — méthode de travail

Ce dépôt est public. Le travail y entre par trois niveaux de branches et par des
demandes de fusion sur GitHub, sous le contrôle de deux hooks versionnés dans
`.githooks/`, d'une vérification unique (`make verify`) et d'une chaîne d'intégration
continue qui la lance.

## Les branches

| Branche | Rôle | Comment on y écrit |
|---|---|---|
| `main` | l'état stable, celui qu'on démontre ; branche par défaut, protégée | par demande de fusion de `develop`, sur demande explicite du mainteneur, suivie d'une étiquette annotée |
| `develop` | l'intégration : ce qui est vérifié ; protégée | par demande de fusion d'une branche de travail, après accord du mainteneur |
| branches de travail | une étape, une spec | commit par commit, push par push |

**Alignées** : après une livraison (`develop` fusionnée dans `main`),
`git diff origin/main origin/develop` est vide. Une demande de fusion `main → develop`
reste possible, jamais obligatoire.

## « Tests verts » : `make verify`

**Tests verts = `make verify` sans échec, les tests sautés affichés.** C'est la même
commande sur le poste et sur GitHub.

| Commande | Ce qu'elle fait |
|---|---|
| `make verify` | les modules concernés par les changements depuis `origin/develop`, et ceux qui en dépendent (`verification/dependances.tsv`) : Ruff, format, tests ; plus la tâche `statique`, toujours |
| `make verify TOUT=1` | tous les modules |
| `make verify MODULE=<nom>` | un seul module : `01` … `05`, `07` … `11`, `database`, `demo`, `statique` |
| `make verify-indicatif` | Bandit et pip-audit sur les modules existants — jamais exigé ; réussit dès que les rapports sont produits, quel que soit le nombre de constats |
| `make verify-poste` | la mémoire d'abord (5 Go disponibles, 4 Go de mémoire vidéo libre) ; `make verify` complet, sauf si l'arbre, propre, a déjà un verdict complet vert (`FORCER=1` pour le rejouer) ; puis les harnais du poste seul : fidélité du schéma, état d'`apimanga` en lecture seule, équivalence du service d'embedding (instance EmbeddingGemma lancée puis arrêtée, sur les échantillons exportés ; **sans échantillon, échec** : une vérification qui ne tourne pas est un échec), cohérence des contrôles obligatoires de l'ensemble de règles de GitHub avec les tâches de `verify.yml` (relevés par `gh`) |

- Chaque module tourne avec son verrou (`uv run --locked`), dans un environnement
  vidé. **Jamais de DSN réel pour les tests, sauf deux étapes de `verify-poste`, en
  lecture seule** (plus bas) : l'état d'`apimanga`, sous `verif_ro` ; l'équivalence de
  l'API du modèle, sous `modele_ro`.
- **Les exigences d'un module neuf sont bloquantes**, dès son premier commit : Pyright
  strict sur `src/`, Bandit sur `src/`, pip-audit sur le verrou, après ses tests, dans
  `make verify` ; un constat est un échec, et la couverture est mesurée. Elles se
  déclarent dans le 9e champ de sa ligne de `MODULES` (`verification/verifier.sh`) ;
  le module 11 (API du modèle) les porte. Pour les modules d'avant, Bandit et
  pip-audit restent indicatifs.
- La tâche `statique` vérifie ce qui ne s'exécute dans aucun module : Ruff du 06 et de
  `docs/modeles/`, actionlint, shellcheck des scripts de `.githooks/`, de
  `verification/` et de `sauvegarde/`, les essais des hooks (`tester.sh`), du
  périmètre (`verification/tester_perimetre.sh`), du moteur de vérification
  (`verification/tester_verifier.sh` : environnement vidé, arrêt, plafond, volumes…)
  et de la sauvegarde (`sauvegarde/tester_sauvegarde.sh`), la cohérence `MODULES` ↔
  graphe ↔ matrice de `verify.yml` ↔ modules à base jetable, et les règles des
  workflows (`verification/controler_workflows.sh` : actions épinglées par empreinte,
  exécuteurs figés, lecture seule, jeton non persistant, aucun échec avalé).
- L'intégration Compose du 02 (PostgreSQL jetable, migrations, rôle de lecture, API
  réelle, contrôles HTTP) fait partie de la vérification du 02.
- Arrêt au premier échec, avec le module et la commande ; une ligne par module :
  passés, sautés, en échec, durée.
- **Un test sauté n'est pas un test passé** : il est compté. Au-delà du plafond écrit,
  avec sa raison, dans `verification/sauts_attendus.tsv`, c'est un échec.
- Les volumes Docker créés par la vérification, et eux seuls, sont supprimés.

Avant une demande de fusion vers `develop` : `make verify` (les modules touchés). Vers
`main` : `make verify-poste`, `make verify-mutations`, et une sauvegarde de ce qui vit
hors du dépôt ; si le harnais a changé (`verification/`, `Makefile`, workflows),
`make verify TOUT=1` d'abord.

### Les mutations versionnées

Une mutation casse le code exprès pour prouver qu'un test le voit. Chacune est un
fichier de `verification/mutations/` (TOML) : le fichier visé, le texte exact à
remplacer (présent une seule fois), son remplaçant, et la commande qui doit échouer
une fois la mutation posée. `make verify-mutations` les rejoue dans un arbre jetable
(`git worktree`, au commit courant : l'arbre de travail doit être propre), après un
passage témoin sans mutation où la commande doit passer sans test sauté. La tâche
`statique` contrôle, à chaque vérification, que chaque mutation se lit et vise un
texte toujours présent une fois : un code qui bouge sans sa mutation se voit tout
de suite. Une mutation écrite pour un correctif s'ajoute dans le même commit.

### La sauvegarde

`sauvegarde/sauvegarder.sh` sauvegarde ce qui vit hors du dépôt (bases, magasin
MLflow, raw, rapports) : préparation locale avec manifeste, envoi chiffré
(`duplicity`, phrase secrète au clavier, jamais en variable), relecture contre le
manifeste, essai de restauration dans un PostgreSQL jetable. **Ce qui dépend du poste
n'est pas dans le dépôt** : la liste des objets et les chemins se lisent dans
`sauvegarde/.env`, ignoré par git (modèle : `sauvegarde/.env.example`, chemins relatifs
au dossier personnel). Ses refus — configuration absente ou fautive, échec d'une
commande, chaîne d'aspect jeton, instance du service en cours, phrase secrète en
variable — sont éprouvés par `sauvegarde/tester_sauvegarde.sh`, sur une arborescence
jetable et des doublures de PostgreSQL, Docker et duplicity.

### L'état d'`apimanga`, en lecture seule

La seule étape qui lit la base réelle : `verification/etat_apimanga.sh`, dans
`verify-poste`.

- **Sous `verif_ro`** (migration `022`) : `SELECT` sur `manga`, `bench` et le suivi des
  migrations, rien d'autre. Le rôle de connexion `verif_lecture` est créé par
  `database/outils/creer_role_verification.sh` ; ses sessions sont en lecture seule. Le
  mot de passe est dans `~/.pgpass`, le DSN dans `verification/.env` (modèle :
  `verification/.env.example`), lu par cette étape seule.
- **Ce qu'elle vérifie**, contre `verification/etat_apimanga.tsv` : les empreintes des
  corpus v1 et v2, des mesures, des runs et des promotions ; le jeu v2 ; l'encodage en
  service ; les migrations appliquées, égales aux fichiers. Puis le contrôle du module
  09, et les tests qui ne peuvent tourner que sur la base réelle (05, 08, `demo`) :
  ils doivent passer, aucun ne doit sauter.
- **Une mesure, une promotion ou une migration change l'état** : relever la référence
  (`bash verification/etat_apimanga.sh --relever`) et la commiter avec ce qui l'a
  changée.

### L'équivalence de l'API du modèle, en lecture seule

Seconde exception à « jamais de DSN réel », accordée par le mainteneur le 2026-10-20
(E3, étape 2) : ce que l'API sert doit être ce que le banc a mesuré, et seule la base
réelle porte les vecteurs du corpus en service.

- **Une étape de `verify-poste`** : l'équivalence de l'API, après celle du service, sur
  la même et unique instance EmbeddingGemma. Les questions du jeu gelé, rejouées par
  l'API, doivent rendre les mêmes dix premières séries que le run de référence du
  banc. Sautée partout ailleurs, et comptée comme telle (`sauts_attendus.tsv`).
- **Sous `modele_ro`** (migration `023`) : `SELECT` sur le corpus, les fragments, les
  vecteurs EmbeddingGemma, le registre des encodages et l'encodage en service, le
  rattachement et le titre des séries — rien d'autre. Le rôle de connexion
  `modele_api` est créé par `database/outils/creer_role_modele.sh` ; ses sessions sont
  en lecture seule.
- **Le DSN n'est visible que de cette étape** : lu dans `verification/.env`, sans mot
  de passe (il est dans `~/.pgpass`), et passé au seul test qu'elle nomme.

**Documentation seule.** Pour un changement qui ne touche que des fichiers de
documentation (`.md`), `make verify` complet n'est pas exigé sur le poste avant le
push : les hooks suffisent, et la chaîne de GitHub garde la fusion. Dès qu'un fichier
de code, de configuration, de test ou de workflow est touché, `make verify` complet
reste obligatoire.

## La chaîne d'intégration continue — `.github/workflows/verify.yml`

- **Déclencheurs** : toute demande de fusion, le push sur `develop` (le commit de
  fusion), le lancement manuel. **Pas de push sur `main`** : l'arbre livré dans `main`
  est celui de `develop`, déjà vérifié.
- **Périmètre** : la tâche `perimetre` lit les fichiers changés et le graphe
  `verification/dependances.tsv`. Une tâche de module non concernée réussit sans rien
  faire — ses étapes sont sautées —, et compte comme réussie pour les contrôles
  obligatoires. Un lancement manuel vérifie tout.
- **Une tâche par module** (`verify <module>`), qui lance `make verify MODULE=<nom>`
  après une seule synchronisation (`uv sync --locked`) ; l'image PostgreSQL des bases
  jetables est tirée par son empreinte, Java 8 est installé pour le 07. **La tâche
  `statique`** tourne toujours.
- **Caches** : celui d'`uv`, propre à chaque tâche (Ruff compris) ; le jar Delta (Ivy)
  et l'extension DuckDB du 07.
- **`indicatif`** (Bandit, pip-audit) vit dans `.github/workflows/indicatif.yml` :
  chaque lundi et à la main, jamais exigé. Il réussit dès que les deux outils ont
  tourné et produit leurs rapports, quel que soit le nombre de constats ; il n'échoue
  que si un outil plante ou si un rapport manque.
- JUnit, couverture et journaux sont publiés comme pièces de chaque exécution, même en
  échec ; le résumé reprend la ligne de chaque module.
- Actions épinglées par l'empreinte de leur commit, exécuteur à version fixe,
  permissions en lecture seule.

**Tout module neuf entre dans la matrice ET dans les contrôles obligatoires, dans la
même étape** : sa ligne dans `verification/verifier.sh`, avec ses exigences, ses
dépendances dans `verification/dependances.tsv`, son plafond de tests sautés dans
`verification/sauts_attendus.tsv` (0 s'il n'en saute aucun), son entrée dans la
matrice de `verify.yml`, et son contrôle `verify <module>` dans l'ensemble de
règles.

## Les protections de `main` et `develop`

Un ensemble de règles GitHub s'applique aux deux branches, **sans exemption, pas même
pour l'administrateur** :

- pas de push direct : une demande de fusion est obligatoire ;
- les tâches `verify <module>` de la matrice et `statique` doivent être vertes ;
- commit de fusion seul (ni écrasement, ni rebase) ; aucune approbation d'un tiers : le
  mainteneur valide en fusionnant ;
- ni push forcé, ni suppression.

La branche de travail est supprimée automatiquement après sa fusion.

**Sortie de secours** : si la chaîne est cassée pour une raison extérieure au dépôt
(service tiers, exécuteur, registre), le propriétaire désactive l'ensemble de règles,
répare, le réactive, et le note au journal.

## Les hooks — à activer une fois par clone

```bash
ln -sf ../../.githooks/pre-push .git/hooks/pre-push
ln -sf ../../.githooks/pre-commit .git/hooks/pre-commit
bash .githooks/tester.sh   # 69 scénarios sur 69 ; joués aussi par la tâche statique
```

- `pre-commit`, avant que le commit existe : mentions interdites, fichiers sous
  l'exclusion locale, clés et jetons des fournisseurs.
- `pre-push`, avant que le distant change : auteur et validateur `TheMaxfly` pour
  chaque commit, mêmes mentions, fichiers interdits dans l'arbre poussé, clés et
  jetons dans les lignes ajoutées, nom des branches et des étiquettes poussées,
  message des étiquettes annotées. Seule exception, le commit de fusion créé par
  GitHub : exactement deux parents, validateur `GitHub <noreply@github.com>`, auteur
  `TheMaxfly` ou le nom de profil GitHub du mainteneur. Les autres contrôles
  s'appliquent aussi à ce commit.
- Sur GitHub, la tâche `statique` contrôle le titre et le corps de chaque demande de
  fusion (`.githooks/controler-texte`, mêmes motifs), à l'ouverture et à chaque
  modification.
- Les lignes sont lues en texte : une ligne qui porte un octet hors UTF-8 n'échappe
  à aucun contrôle.

Ils s'appliquent à l'identique sur toutes les branches et ne lancent aucun test.
**Jamais de `--no-verify`.**

## Les secrets

Aucun secret n'entre dans un fichier suivi. Un secret se pose dans l'environnement
ou dans un fichier `.env` hors dépôt, ignoré par git. Les hooks lisent les deux et
comparent la valeur à ce qui part, sans jamais l'afficher. Ils refusent aussi une
chaîne qui a l'aspect d'un secret. Le refus nomme le fournisseur et le fichier.

| Secret | Où il se pose | Ce que les hooks refusent |
|---|---|---|
| jeton Hugging Face | `HF_TOKEN`, ou le fichier `$HF_HOME/token` | `pre-commit` et `pre-push` : sa valeur ; `hf_` suivi de 34 caractères |
| clé Mistral | `MISTRAL_API_KEY`, dans le `.env` du module de l'API du modèle | `pre-commit` et `pre-push` : sa valeur ; une affectation littérale à `MISTRAL_API_KEY` |
| clé OpenAI | `OPENAI_API_KEY`, dans le `.env` du module qui l'emploie | `pre-commit` et `pre-push` : sa valeur ; `sk-` suivi d'au moins 40 caractères |

- Les `.env` lus sont ceux de la racine et des dossiers jusqu'à deux niveaux sous
  elle. Une variable absente ou vide ne compare rien.
- **Une clé Mistral ou OpenAI ne se pose sur un poste qu'après ces contrôles** :
  fusionnés dans `develop`, hooks activés dans le clone.
- La forme d'une clé Mistral n'est pas documentée. Seule sa valeur réelle la garde
  partout ; le motif ne voit qu'une affectation.

## Nommer

### Commits

Conventional Commits : `<type>(<portée>): <description>`.

- Types : `feat`, `fix`, `perf`, `refactor`, `test`, `docs`, `style`, `ci`, `chore`.
- Portée : le domaine touché (`mesures`, `identity`, `database`, `corpus`…).
- Description en français, à l'infinitif, en minuscules, sans point final ; le
  détail après « — ».
- Corps : ce qui a été fait, en puces ; le nombre de tests.

### Branches de travail

`<préfixe>/<étape>-<sujet>` : minuscules, chiffres et tirets, sans accent,
50 caractères au plus. Pour une spec sans numéro d'étape : `<préfixe>/<sujet>`.

Le préfixe suit le type du commit principal de l'étape, celui qu'elle porterait si
elle tenait en un seul commit.

| Type | Préfixe |
|---|---|
| `feat` | `feature/` |
| `fix` | `fix/` |
| `perf` | `perf/` |
| `refactor` | `refactor/` |
| `test` | `test/` |
| `docs` | `docs/` |
| `style` | `style/` |
| `ci` | `ci/` |
| `chore` | `chore/` |

Exemples : `feature/e3-etape1-corpus-v2`, `docs/methode-branches`. Forme contrôlable
par `^(feature|fix|perf|refactor|test|docs|ci|chore|style)/[a-z0-9]+(-[a-z0-9]+)*$`.

### Demandes de fusion et commits de fusion

GitHub reprend **le titre et le corps de la demande** comme titre et corps du commit
de fusion. La demande s'écrit donc dans la forme de la nomenclature :

```text
chore(git): fusionner feature/e3-etape1-corpus-v2 dans develop

E3 étape 1 — <titre de la spec>. Rapport : <nom du rapport>.

- <sha> feat(corpus): …
- <sha> docs(corpus): …
```

```text
chore(git): fusionner develop dans main — e3-etape1
```

Le type `chore` ne compte pas une seconde fois les `feat` et les `fix` que la
branche apporte : chacun porte déjà son propre commit. Titre et corps se relisent
avant de fusionner : aucune mention d'assistant, aucune signature d'outil, aucun
secret. La tâche `statique` refuse une demande dont le titre ou le corps porte une
mention ; le message du commit de fusion passe ensuite par le `pre-push`.

### Étiquettes

Annotées, posées sur `main`, une par livraison de `develop` dans `main` :
`e3-etape1`, `e3-etape2`… ; pour une étape sans numéro, le nom de sa spec
(`methode-branches`). Si plusieurs étapes passent ensemble, l'étiquette prend le nom
de la dernière. Message : `<étiquette> — <titre>`, puis la liste des étapes apportées.

Le `pre-push` relit le nom de l'étiquette et son message : une mention d'assistant
ou de co-auteur refuse le push. Le message se relit quand même avant le push.

## Trois règles imposées par les hooks

1. **Les fusions se font par demande de fusion sur GitHub, par commit de fusion.**
   *Motif : le `pre-push` admet le commit de fusion créé par GitHub (deux parents,
   validateur GitHub exact) ; tout autre commit doit avoir `TheMaxfly` pour auteur et
   validateur.*
2. **Une branche de travail se crée depuis `origin/develop`, avec son amont** :
   `git fetch origin`, puis `git switch -c <branche> origin/develop`. Le premier
   push se fait par `git push -u origin <branche>`. *Motif : sans amont, le
   `pre-commit` ne juge que l'écart au dernier commit et voit moins.*
3. **Les listes d'exceptions des hooks** (`.githooks/mentions-techniques`,
   `.githooks/docs-versionnes`) **sont lues dans le commit poussé** : une branche
   qui a besoin d'une exception la porte dans **sa** version de la liste, et la
   fusion l'amène dans `develop`.

## La règle de travail

1. Une étape = une spec = une branche de travail.
2. Chaque bloc = un commit vérifié = un push sur cette branche : tests verts,
   périmètre vérifié, documents de pilotage à jour avant le commit.
3. **On commite d'abord, on mesure ensuite** : une mesure enregistrée (MLflow,
   base) se lance sur un arbre propre et commité ; le rapport cite la branche et le
   commit.
4. Fin d'étape : `make verify`, rapport, demande de fusion vers `develop`, chaîne
   verte, puis **le mainteneur fusionne** dans l'interface.
5. `develop` vers `main` : **seulement sur demande explicite du mainteneur** :
   `make verify-poste` et une sauvegarde, demande de fusion, chaîne verte, fusion par
   le mainteneur, puis une étiquette annotée sur `main`, poussée.
6. La branche de travail fusionnée est supprimée sur le distant par GitHub, en local
   à la main ; ses commits restent atteignables.

L'historique n'est jamais réécrit : pas de push forcé, pas de rebase d'une branche
poussée, pas de fusion par écrasement.

**Ce qui vit hors du dépôt ne suit pas les branches.** La base `apimanga`, le
magasin MLflow, les données brutes, le journal et les rapports sont communs à toutes
les branches. Une migration appliquée depuis une branche de travail modifie la base
commune : elle est précédée d'une sauvegarde.

## Écrire dans le corpus ou les vecteurs

Depuis la migration `021`, plusieurs versions du corpus (`bench.corpus`) et
plusieurs encodages d'un même modèle coexistent. La base donne encore `'v1'` par
défaut à `corpus_id`, pour que le chargeur du corpus v1 et les tests antérieurs
écrivent comme avant : c'est une dette. Le code, lui, ne s'y fie jamais.

1. **Une seule fonction d'écriture par module écrivain.** Toute écriture d'un
   document (`bench.corpus_docs`), d'un fragment (`bench.corpus_chunks`) ou d'un
   vecteur (`bench.vecteurs_*`) passe par elle : ajouts, modifications et
   suppressions. Aujourd'hui : `corpus.ecriture.ecrire_corpus` (module 05) et
   `service_embedding.encodage.ecrire_vecteurs` (module 09).
2. **Le corpus y est un argument nommé, obligatoire, sans valeur par défaut** :
   `corpus_id=…`. Toute inscription d'un encodage (`bench.encodages`) nomme aussi
   son corpus.
3. **Un test par module le vérifie** sur le code source :
   - aucune autre écriture de ces tables n'existe dans le module ;
   - aucune écriture dont la table n'est connue qu'à l'exécution ;
   - le corpus de la fonction n'a pas de défaut.

   Un module qui se met à écrire dans ces tables arrive avec sa fonction et son
   test, dans la même étape.
4. **Lire, c'est désigner.** Une lecture de vecteurs nomme son encodage. Une
   lecture du corpus nomme le sien ; sinon, c'est celui de l'encodage en service
   (`bench.v_encodage_en_service`), sinon le seul corpus de la base. Plusieurs
   corpus et aucun en service : la lecture refuse de deviner.

*Motif : un corpus omis tombe dans `v1` sans bruit. La base refuse déjà de mêler
deux corpus (clés composites) et d'écrire dans un corpus clos ; la règle ferme la
voie au défaut côté code.*

## Un cycle complet

Les fichiers de corps (`--body-file`, `-F`) s'écrivent hors du dépôt.

```bash
# ouvrir l'étape
git fetch origin
git switch -c feature/e3-etape1-corpus-v2 origin/develop
# … un bloc = un commit vérifié ; premier push :
git push -u origin feature/e3-etape1-corpus-v2

# fin d'étape : vérifier, demander la fusion vers develop
make verify
gh pr create --base develop \
  --title "chore(git): fusionner feature/e3-etape1-corpus-v2 dans develop" \
  --body-file ../demande.md
# chaîne verte → le mainteneur fusionne dans l'interface (commit de fusion)
git switch develop && git pull --ff-only
git branch -d feature/e3-etape1-corpus-v2 && git fetch --prune

# sur demande : livrer develop dans main, étiqueter
make verify-poste      # et une sauvegarde
gh pr create --base main --head develop \
  --title "chore(git): fusionner develop dans main — e3-etape1" --body-file ../livraison.md
# chaîne verte → le mainteneur fusionne
git fetch origin
git tag -a e3-etape1 -F ../etiquette.txt origin/main
git push origin e3-etape1
git diff --quiet origin/main origin/develop && echo "main et develop alignées"
```
