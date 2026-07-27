# `manga-pipeline` — console de démonstration du pipeline ELT

Console terminal pour **dérouler la chaîne de données devant un jury** : un
écran d'état réel, puis un menu d'actions groupé par phase ELT.

> **Principe dur : cette console n'implémente aucune logique métier.**
> Chaque action est un sous-processus vers une CLI **déjà testée** du dépôt, et
> la **commande exacte est affichée avant exécution** — le jury voit la vraie
> commande, pas une abstraction. Le récit complet du pipeline est dans
> [../GUIDE_PIPELINE.md](../GUIDE_PIPELINE.md).

## Lancer

```bash
cd demo
uv sync --extra dev
export DATABASE_URL='postgresql://postgres@localhost:5432/apimanga'

uv run manga-pipeline            # mode LECTURE SEULE (défaut)
uv run manga-pipeline --etat     # écran d'état seul, puis sortie
uv run manga-pipeline --ecriture # débloque les actions qui écrivent en base
```

## Sécurité de démonstration

| Garde-fou | Mise en œuvre |
|---|---|
| Lecture seule **par défaut** | Les actions qui écrivent en base sont **absentes du menu** — pas grisées : absentes. On ne déclenche pas par erreur ce qui n'est pas proposé. |
| Barrière indépendante de l'affichage | Même appelé directement, l'exécuteur lève `RefusEcriture` en lecture seule (testé sur chaque action base). |
| Écriture base = mot tapé | Confirmation par le mot **`ECRIRE`** en majuscules. Un « oui » distrait ne suffit pas. |
| Aucune action destructive | Un test interdit `truncate`, `drop`, `delete`, `rm`, `reset`, `--force` dans tout le registre. |
| Extract jamais exécuté | Le crawl et le canari sortent sur le réseau : ils sont **documentés** (commande affichée + explication), la console refuse de les lancer. |
| Aucun secret | La connexion vient de `DATABASE_URL`, comme partout dans le dépôt. |

**Les quatre niveaux d'impact** affichés en face de chaque action :

- `documenté` — jamais exécuté, on montre la commande et on explique ;
- `lecture` — n'écrit nulle part ;
- `fichiers` — écrit des fichiers reconstructibles **hors base** (tables Delta,
  rapports, CSV de mesure). Disponible en lecture seule, avec confirmation ;
- `base` — écrit dans PostgreSQL. **Masqué** en lecture seule.

> Choix assumé : le mode lecture seule masque les écritures **base**, et laisse
> les actions `fichiers` (lakehouse, rapports) — sinon le scénario de démo de 10
> minutes, qui repose sur le lakehouse, serait injouable. Rien de ce qui est
> écrit dans cette catégorie n'est irréversible : tout est reconstructible
> depuis le raw.

## Ce que montre l'écran d'état

Migrations (appliquées / en attente) · volumétrie base et couverture d'identité
· snapshots du raw et leur taille · tables gold et dernier rapport qualité ·
dernier commit git. Que des `SELECT count(*)`, des `stat()` et un `git log -1` :
aucune écriture, et une panne (base éteinte) devient un message affiché, jamais
une exception qui ferme la console au mauvais moment.

## Tests

```bash
uv run pytest -q     # 78 tests (74 + 4 ignorés sans DATABASE_URL)
uv run ruff check src/ tests/
```

Trois familles :

- **`test_actions.py`** — le registre ne peut pas mentir : chaque action pointe
  un fichier de CLI qui **existe**. Renommer un module `identity`, un job
  lakehouse ou le runner de migrations **casse ces tests** — donc le guide et la
  console ne peuvent pas annoncer une commande morte.
- **`test_lecture_seule.py`** — le mode lecture seule ne peut pas écrire :
  vérifié au niveau du menu *et* de l'exécuteur, plus le mot de confirmation.
- **`test_etat.py`** — deux niveaux. *Sans base* : dégradation propre sans DSN
  et avec un DSN injoignable, et toutes les requêtes sont des `SELECT`.
  *Avec `DATABASE_URL`* (sinon ignorés) : les **comptes affichés sont confrontés
  aux valeurs de référence** — 14 670 séries, 104 107 volumes, 11 074 critiques
  — et le journal des décisions ne peut pas décroître sous son plancher
  (append-only). L'écran de démo subit la même discipline que le reste : un
  chiffre qui dérive fait échouer la suite au lieu de passer inaperçu en
  soutenance.
