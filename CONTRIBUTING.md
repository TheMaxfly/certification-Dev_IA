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
| `make verify` | pour chaque module : Ruff, format, tests ; puis actionlint sur les workflows |
| `make verify MODULE=<nom>` | la même chose pour un seul module : `01` … `10`, `database`, `demo`, `workflows` |
| `make verify-indicatif` | Bandit et pip-audit sur les modules existants — jamais exigé ; réussit dès que les rapports sont produits, quel que soit le nombre de constats |
| `make verify-poste` | `make verify`, puis les harnais du poste seul : hooks, fidélité du schéma, intégration Compose du 02 |

- Chaque module tourne avec son verrou (`uv run --locked`), dans un environnement
  vidé : aucune suite ne reçoit la base réelle.
- Arrêt au premier échec, avec le module et la commande ; une ligne par module :
  passés, sautés, en échec, durée.
- **Un test sauté n'est pas un test passé** : il est compté. Au-delà du plafond écrit,
  avec sa raison, dans `verification/sauts_attendus.tsv`, c'est un échec.
- Les volumes Docker créés par la vérification, et eux seuls, sont supprimés.

Avant une demande de fusion vers `develop` : `make verify`. Vers `main` :
`make verify-poste`, et une sauvegarde de ce qui vit hors du dépôt.

## La chaîne d'intégration continue — `.github/workflows/verify.yml`

- **Déclencheurs** : push sur `main` et `develop`, toute demande de fusion, lancement
  manuel.
- **Une tâche par module** (`verify <module>`), qui lance `make verify MODULE=<nom>`
  après `uv sync --locked` ; l'image PostgreSQL des bases jetables est tirée par son
  empreinte, Java 8 est installé pour le 07.
- **Une tâche `indicatif`** (Bandit, pip-audit), jamais exigée pour fusionner. Elle
  réussit dès que les deux outils ont tourné et produit leurs rapports, quel que soit
  le nombre de constats, qu'elle affiche par module et par outil dans le résumé et
  publie comme pièces ; elle n'échoue que si un outil plante ou si un rapport manque.
  La synthèse d'un commit dont les contrôles passent reste ainsi verte.
- JUnit, couverture et journaux sont publiés comme pièces de chaque exécution, même en
  échec ; le résumé reprend la ligne de chaque module.
- Actions épinglées par l'empreinte de leur commit, exécuteur à version fixe,
  permissions en lecture seule.

**Tout module neuf entre dans la matrice ET dans les contrôles obligatoires, dans la
même étape** : sa ligne dans `verification/verifier.sh`, son entrée dans la matrice de
`verify.yml`, et son contrôle `verify <module>` dans l'ensemble de règles.

## Les protections de `main` et `develop`

Un ensemble de règles GitHub s'applique aux deux branches, **sans exemption, pas même
pour l'administrateur** :

- pas de push direct : une demande de fusion est obligatoire ;
- les tâches `verify <module>` de la matrice doivent être vertes — jamais `indicatif` ;
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
bash .githooks/tester.sh   # 35 scénarios sur 35
```

- `pre-commit`, avant que le commit existe : mentions interdites, fichiers sous
  l'exclusion locale, jetons.
- `pre-push`, avant que le distant change : auteur et validateur `TheMaxfly` pour
  chaque commit, mêmes mentions, fichiers interdits dans l'arbre poussé. Seule
  exception, le commit de fusion créé par GitHub : exactement deux parents, validateur
  `GitHub <noreply@github.com>`, auteur `TheMaxfly` ou le nom de profil GitHub du
  mainteneur. Les autres contrôles s'appliquent aussi à ce commit.

Ils s'appliquent à l'identique sur toutes les branches et ne lancent aucun test.
**Jamais de `--no-verify`.**

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
secret — le message du commit de fusion passe ensuite par le `pre-push`.

### Étiquettes

Annotées, posées sur `main`, une par livraison de `develop` dans `main` :
`e3-etape1`, `e3-etape2`… ; pour une étape sans numéro, le nom de sa spec
(`methode-branches`). Si plusieurs étapes passent ensemble, l'étiquette prend le nom
de la dernière. Message : `<étiquette> — <titre>`, puis la liste des étapes apportées.

Le `pre-push` ne juge que des commits : **le message d'une étiquette n'est relu par
aucun hook**. Il se relit avant le push.

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
