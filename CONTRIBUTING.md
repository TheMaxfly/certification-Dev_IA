# Contribuer — méthode de travail

Ce dépôt est public. Le travail y entre par trois niveaux de branches, toujours par
des fusions locales, sous le contrôle de deux hooks versionnés dans `.githooks/`.

## Les branches

| Branche | Rôle | Comment on y écrit |
|---|---|---|
| `main` | l'état stable, celui qu'on démontre ; branche par défaut | par fusion locale de `develop`, sur demande explicite du mainteneur, suivie d'une étiquette annotée |
| `develop` | l'intégration : ce qui est vérifié | par fusion locale d'une branche de travail, après accord du mainteneur |
| branches de travail | une étape, une spec | commit par commit |

Après la fusion de `develop` dans `main`, `develop` est avancée sur `main` par avance
rapide (`git merge --ff-only main`) : les deux branches pointent le même commit.

## Les hooks — à activer une fois par clone

```bash
ln -sf ../../.githooks/pre-push .git/hooks/pre-push
ln -sf ../../.githooks/pre-commit .git/hooks/pre-commit
bash .githooks/tester.sh   # 26 scénarios sur 26
```

- `pre-commit`, avant que le commit existe : mentions interdites, fichiers sous
  l'exclusion locale, jetons.
- `pre-push`, avant que le distant change : auteur et validateur `TheMaxfly` pour
  chaque commit, mêmes mentions, fichiers interdits dans l'arbre poussé.

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

### Commits de fusion

Créés par `git merge --no-ff -F <fichier>`, jamais avec le message par défaut.

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
branche apporte : chacun porte déjà son propre commit.

### Étiquettes

Annotées, posées sur `main`, une par fusion de `develop` dans `main` : `e3-etape1`,
`e3-etape2`… ; pour une étape sans numéro, le nom de sa spec (`methode-branches`).
Si plusieurs étapes passent ensemble, l'étiquette prend le nom de la dernière.
Message : `<étiquette> — <titre>`, puis la liste des étapes apportées.

Le `pre-push` ne juge que des commits : **le message d'une étiquette n'est relu par
aucun hook**. Il se relit avant le push.

## Trois règles imposées par les hooks

1. **Toutes les fusions se font en local**, par `git merge --no-ff`, puis push.
   **Aucune fusion par l'interface de GitHub** : ni bouton de fusion, ni écrasement,
   ni rebase. *Motif : le `pre-push` exige `TheMaxfly` comme auteur **et**
   validateur de chaque commit ; un commit de fusion créé par GitHub a pour
   validateur « GitHub », et ferait refuser le push suivant de toute branche qui le
   ramène.*
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
4. Fin d'étape : sauvegarde, rapport, puis **accord du mainteneur**, puis fusion
   locale dans `develop` et push.
5. `develop` vers `main` : **seulement sur demande explicite du mainteneur**, par
   fusion locale et push, suivie d'une étiquette annotée sur `main`, poussée.
6. La branche de travail est supprimée après fusion, en local et sur le distant ;
   ses commits restent atteignables.

L'historique n'est jamais réécrit : pas de push forcé, pas de rebase d'une branche
poussée, pas de fusion par écrasement.

**Ce qui vit hors du dépôt ne suit pas les branches.** La base `apimanga`, le
magasin MLflow, les données brutes, le journal et les rapports sont communs à toutes
les branches. Une migration appliquée depuis une branche de travail modifie la base
commune : elle est précédée d'une sauvegarde.

## Un cycle complet

Les fichiers de message (`-F`) s'écrivent hors du dépôt.

```bash
# ouvrir l'étape
git fetch origin
git switch -c feature/e3-etape1-corpus-v2 origin/develop
# … un bloc = un commit vérifié ; premier push :
git push -u origin feature/e3-etape1-corpus-v2

# fin d'étape, après accord : fusionner dans develop, supprimer la branche
git switch develop && git pull --ff-only
git merge --no-ff -F ../fusion.txt feature/e3-etape1-corpus-v2
git push
git branch -d feature/e3-etape1-corpus-v2
git push origin --delete feature/e3-etape1-corpus-v2

# sur demande : livrer develop dans main, étiqueter, réaligner develop
git switch main && git pull --ff-only
git merge --no-ff -F ../livraison.txt develop
git push
git tag -a e3-etape1 -F ../etiquette.txt
git push origin e3-etape1
git switch develop && git merge --ff-only main && git push
```
