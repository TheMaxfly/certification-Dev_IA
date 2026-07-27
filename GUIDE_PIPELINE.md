# Guide du pipeline ELT — commande par commande

Ce guide sert à **dérouler la chaîne de données devant un jury**. Chaque
commande citée ici existe, a été exécutée, et sa durée a été mesurée. Rien
n'est reconstitué de mémoire.

Deux façons de s'en servir :

- **à la main**, en copiant les commandes de ce guide ;
- **avec la console** `manga-pipeline`, qui affiche la vraie commande avant de
  la lancer (voir [demo/README.md](demo/README.md)) — elle orchestre ces mêmes
  CLI, elle n'en réimplémente aucune.

> **Convention** : tout Python passe par `uv run`. Le module 05 est en layout
> `src/` sans point d'entrée déclaré, d'où le `PYTHONPATH=src`. La connexion
> vient toujours de `DATABASE_URL` — aucun identifiant n'est écrit nulle part.
>
> ```bash
> export DATABASE_URL='postgresql://postgres@localhost:5432/apimanga'
> ```

---

## 1. Le pipeline en un schéma

```
┌─ E ─ EXTRACT ────────────────────────────────────────────────────────┐
│  04 Manga Sanctuary (Scrapy)   03 Kitsu (API)   01 Manga-News        │
│  Wikidata (SPARQL/entités)     Manga Insight (parquet)               │
│                    ↓ écrit, ne transforme pas                        │
│           data/raw/<AAAA-MM>/*.jsonl   — ARCHIVE IMMUABLE            │
└──────────────────────────────────────────────────────────────────────┘
                                 ↓
┌─ L ─ LOAD ───────────────────────────────────────────────────────────┐
│  charger_ms / charger_kitsu / charger_wikidata / charger_mi          │
│  → staging.*  : tables TOUT-TEXT, aucun typage, aucun rejet          │
│    « on charge d'abord, on typera après »                            │
└──────────────────────────────────────────────────────────────────────┘
                                 ↓
┌─ T ─ TRANSFORM ──────────────────────────────────────────────────────┐
│  INSERT … SELECT typé  →  manga.ms_series_enriched, ms_volumes_…     │
│  formes normalisées (une seule normalisation, en Python)             │
│                                 ↓                                    │
│  CASCADE D'IDENTITÉ, append-only, chaque décision journalisée :      │
│    étage 0  pont Kitsu ....... identifiants externes, score 1.0      │
│    étage 1  exact ............ forme exacte + auteur + année         │
│    étage 2  référentiel Kitsu                                        │
│    étage 3  flou ............. seuil calibré → needs_review          │
│    étage R  juge LLM ......... AVIS, promu séparément et borné       │
│  → manga.match_decision (journal)  /  manga.v_match_current (état)   │
└──────────────────────────────────────────────────────────────────────┘
                                 ↓
┌─ Q ─ QUALITÉ (module 07, consultatif) ───────────────────────────────┐
│  lakehouse : bronze → silver → gold   (Spark + Delta, conteneurisé)  │
│  → métriques comparées entre snapshots + rapport qualité             │
│  → lu hors Spark par DuckDB (~0,1 s) : la boucle de contrôle         │
└──────────────────────────────────────────────────────────────────────┘
```

### Où est le E, le L, le T — et pourquoi ELT

- **E** = les collecteurs écrivent des fichiers datés sous `data/raw/`. Ils ne
  nettoient rien : leur seul travail est de rapporter fidèlement.
- **L** = les chargeurs déversent ces fichiers dans des tables `staging.*`
  **entièrement en TEXT**. Aucun typage, donc aucune ligne perdue à l'entrée.
- **T** = la transformation se fait **dans la base**, en SQL typé
  (`INSERT … SELECT`), puis par la cascade d'identité.

**Pourquoi ELT et pas ETL ?** Parce que le raw est immuable et rejouable : on
peut re-transformer sans re-collecter. Deux exemples vécus sur ce projet :

1. **Les alias tronqués.** Le crawl de décembre ne captait qu'une partie des
   titres alternatifs. Le correctif a été appliqué **sans re-crawler** : le
   fichier contenait déjà la matière, seule la transformation était fautive.
   En ETL, l'information aurait été perdue à la collecte.
2. **Les 59 volumes perdus.** L'ELT historique en oubliait 59 du fichier de
   décembre. C'est en **rejouant le fichier** qu'on a pu trancher : le raw
   faisait foi, la base avait tort. Sans archive immuable, l'écart aurait été
   indécidable — chacun aurait défendu son chiffre.

C'est le sens de la règle du projet : **le raw est l'archive, tout le reste en
est une projection reconstructible.**

---

## 2. Étape par étape

Pour chaque étape : la commande exacte, ce qu'elle affiche, comment vérifier
qu'elle a réussi, et la phrase à dire.

### 2.1 Extract — collecter

```bash
cd 04_scraping_manga_sanctuary
uv run python scripts/run_scrape.py --smoke 5
```

- **Affiche** : la progression Scrapy, puis les seuils de validation.
- **Vérifier** : un dossier `data/raw/<AAAA-MM>/` contient les `.jsonl`.
- **À dire** : « Le crawl est reprenable et ne remplace le snapshot mensuel
  qu'après avoir passé des seuils de volume. Un crawl tronqué ne peut pas
  écraser une bonne référence — c'est un garde-fou né d'un incident réel. »

> ⚠️ Sortie réseau : **ne pas lancer en soutenance**. La console la présente
> comme *action documentée* et refuse de l'exécuter.

Le **canari** (`04_.../canari/01_echantillon.py` … `07_verifier.py`) re-scrape
un échantillon et le compare au snapshot. C'est lui qui a fait tomber le bug de
sélecteur des critiques **avant** de relancer un crawl complet.

### 2.2 Load — charger sans typer

```bash
cd 05_nettoyage_agregation_bdd
PYTHONPATH=src uv run python -m identity.charger_ms          # ~3 min
PYTHONPATH=src uv run python -m identity.charger_kitsu       # ~4 min
PYTHONPATH=src uv run python -m identity.charger_wikidata    # ~1 min
PYTHONPATH=src uv run python -m identity.charger_mi          # ~1 min
```

- **Affiche** : les comptes staging puis les comptes promus, étape par étape.
- **Vérifier** :
  ```sql
  SELECT count(*) FROM manga.ms_series_enriched;   -- 14 670
  SELECT count(*) FROM manga.ms_volumes_enriched;  -- 104 107
  ```
- **À dire** : « Le fichier entre d'abord dans une table entièrement en texte.
  Rien n'est typé, donc rien n'est rejeté à l'entrée : une date mal formée ne
  fait pas perdre la ligne. Le typage vient après, en SQL, où l'on peut décider
  quoi faire des valeurs douteuses. »

> Ces quatre commandes **écrivent en base** et n'ont pas de `--dry-run`
> (vérifié sur leur `--help`). La console les masque en mode lecture seule.
> `charger_ms` promeut en **upsert-merge, jamais de DELETE** ; `charger_mi`
> recharge en transaction avec un **plancher de qualité** et un rollback prouvé.

### 2.3 Transform — typer, puis identifier

La promotion typée est enchaînée par `charger_ms` (`--promouvoir`, actif par
défaut). Vient ensuite la cascade, étage par étage :

```bash
cd 05_nettoyage_agregation_bdd
PYTHONPATH=src uv run python -m identity.pont_kitsu   --dry-run   # étage 0
PYTHONPATH=src uv run python -m identity.etage1_exact --dry-run   # étage 1
PYTHONPATH=src uv run python -m identity.etage2_kitsu --dry-run   # étage 2
PYTHONPATH=src uv run python -m identity.etage3_flou  --dry-run   # étage 3
```

- **Affiche** : la calibration, la matrice de décision, l'entonnoir des cas,
  puis `⚠ DRY-RUN : transaction annulée, aucune écriture en base.`
- **Vérifier** : en `--dry-run`, la base est **intacte** (`ROLLBACK`) ; le
  rapport est écrit sous `data/rapports/<étage>/<horodatage>/`.
- **Durées mesurées ici** : pont 0,3 s, étage 1 1,1 s — **parce que la cascade
  est déjà appliquée** et qu'il ne reste aucun candidat à traiter. Sur une base
  fraîche, comptez plusieurs minutes par étage. C'est une bonne nouvelle pour la
  démo : l'étape est instantanée et la matrice s'affiche quand même.
- **À dire** : « Chaque étage est une méthode de rapprochement différente, de
  la plus sûre à la plus risquée. Aucune ne peut écrire une décision sans la
  journaliser. En cas de doute, le cas part en revue humaine — jamais en
  automatique. »

> ⚠️ **Le défaut de ces quatre commandes est `--no-dry-run`, donc l'écriture.**
> C'est `--dry-run` qu'il faut ajouter pour la démo.

L'**étage R** (juge LLM) est séparé en deux : le juge émet un avis, la
promotion le transforme — ou non — en décision.

```bash
PYTHONPATH=src uv run python -m identity.etage_r_promotion     # dry-run par défaut
PYTHONPATH=src uv run python -m identity.etage_r_promotion --appliquer   # écrit
```

- **À dire** : « Le modèle ne décide pas. Il donne un avis, mesuré contre 60 cas
  d'étalonnage et 100 cas arbitrés à la main. Seule une promotion bornée et
  journalisée transforme cet avis en décision, et on peut la rejouer à
  l'identique. »

### 2.4 Qualité — le lakehouse (module 07, consultatif)

```bash
cd 07_databricks_manga_export/lakehouse
uv run lakehouse pipeline     # bronze → silver → gold → rapport (~1 min 29)
uv run lakehouse verifier     # 12 contrôles imposés            (~24 s)
uv run lakehouse synthese     # lecteur DuckDB, hors JVM        (~0,4 s)
```

- **Vérifier** : `verifier` sort **TOUT VERT** et code retour 0 ; un
  `rapport_qualite_<ts>.md` apparaît dans `07_.../rapports/`.
- **À dire** : « Cette couche ne bloque rien : elle historise et elle alerte.
  Elle compare chaque snapshot au précédent et fait ressortir par système trois
  incidents que le projet avait découverts par accident. »

### 2.5 Base — migrations et fidélité

```bash
cd database
uv run python migrate.py status     # ~0,2 s
bash outils/fidelite.sh             # rejeu sur PostgreSQL jetable (~1 min)
```

- **Vérifier** : `12 appliquée(s), 0 en attente` ; `fidelite.sh` sort un **diff
  vide**.
- **À dire** : « Le schéma est versionné comme du code. Et on le prouve : on
  rejoue toutes les migrations sur une base jetable et on compare le schéma
  obtenu à la vraie base. Un diff vide veut dire que le dépôt sait reconstruire
  la base. »

---

## 3. Scénario de démonstration — 10 minutes

Sur données **déjà en place**, en mode lecture seule. Aucune écriture en base.

| # | Temps | Commande | Ce qu'on montre | Ce qu'on dit |
|---|---|---|---|---|
| 1 | 0:00 | `manga-pipeline --etat` | L'écran d'état complet | « Voici l'état réel du système : 12 migrations appliquées, 14 670 séries, 104 107 volumes, 57,3 % d'identités automatiques. » |
| 2 | 1:30 | `cd database && uv run python migrate.py status` | 12 appliquées, 0 en attente | « Le schéma est versionné et le runner vérifie les checksums : une migration modifiée après coup est refusée. » |
| 3 | 2:30 | `PYTHONPATH=src uv run python -m identity.etage1_exact --dry-run` | La matrice + `⚠ DRY-RUN : transaction annulée` (1,1 s) | « Un étage de la cascade, en dry-run : il calcule tout et n'écrit rien. La base est intacte à la fin. » |
| 4 | 5:00 | la requête d'identité ci-dessous | 5 séries → QID Wikidata | « Aucune plateforme ne partage d'identifiant. On reconstruit l'identité, et chaque décision garde sa méthode et son score. » |
| 5 | 6:00 | `uv run lakehouse verifier` | `TOUT VERT` | « Douze contrôles confrontent le lakehouse aux comptes attendus et aux trois incidents historiques. » |
| 6 | 7:30 | `uv run lakehouse synthese` | La synthèse en ~96 ms | « Le même lakehouse, lu sans Spark, par DuckDB. C'est ce tableau que la revue mensuelle consulte avant de promouvoir. » |
| 7 | 8:30 | ouvrir `07_.../rapports/rapport_qualite_*.md` | Les 3 ⚠️ | « Trois incidents réels, détectés autrefois par accident, aujourd'hui par métrique de routine. » |

Requête du pas 4 (~0,2 s) :

```sql
SELECT s.series_id, left(s.series_title, 32) AS titre,
       c.wikidata_qid, c.method, c.score, c.status
  FROM manga.ms_series_enriched s
  JOIN manga.v_match_current c USING (series_id)
 WHERE c.status = 'auto'
 ORDER BY c.score DESC NULLS LAST, s.series_id
 LIMIT 5;
```

**Repli si la base est indisponible** : les pas 5, 6 et 7 ne touchent pas
PostgreSQL — le lakehouse et son rapport suffisent à tenir la démonstration.

---

## 4. FAQ jury

**ELT ou ETL ? Pourquoi ce choix ?**
ELT. Le raw daté est immuable, donc toute transformation est rejouable sans
re-collecter. Deux incidents l'ont validé : les alias tronqués (re-transformés
sans re-crawl) et les 59 volumes perdus (arbitrés en rejouant le fichier). En
ETL, une erreur de transformation détruit l'information à la source.

**Spark nécessite-t-il un compte, une licence, un cloud ?**
Non. Apache Spark et Delta Lake sont libres. Le module 07 tourne dans un
conteneur Docker local (image officielle `apache/spark:3.5.9` + `delta-spark
3.3.2`), sans aucun compte : `docker compose run --rm lakehouse pipeline`. Le
format Delta est ouvert — c'est d'ailleurs pourquoi **DuckDB peut lire les mêmes
tables sans Spark du tout**.

**Pourquoi pas Databricks, alors ?**
Databricks a servi de premier jet (le POC est conservé). Il a été remplacé
parce qu'une plateforme propriétaire ne doit pas être un point de passage
obligé : le lakehouse doit tourner sur le poste, être testable et versionné.
Le portage Databricks reste versionné en bonus (`lakehouse/bonus_databricks/`)
pour montrer que **le même job tourne ailleurs sans réécriture** — format
ouvert, plateforme optionnelle.

**Que se passe-t-il si je relance une commande ?**
Rien de cassé : les jobs sont **idempotents**.
- Le lakehouse réingère par `replaceWhere` sur la partition du snapshot : les
  comptes ne bougent pas, aucun doublon. Démonstration :
  ```bash
  uv run lakehouse ingest --source ms_reviews --snapshot 2025-12   # 2 fois
  uv run lakehouse verifier                                        # comptes identiques
  ```
- La cascade est **append-only** : une décision n'est jamais modifiée ni
  supprimée, on en ajoute une nouvelle qui fait autorité (`v_match_current`).
  Rejouer la promotion de l'étage R promeut **0** cas : c'est prouvé.
- Les migrations vérifient un checksum : déjà appliquée, une migration n'est
  pas rejouée.

**Comment savez-vous que la base est conforme au dépôt ?**
`bash outils/fidelite.sh` rejoue toutes les migrations sur un PostgreSQL
jetable et compare le schéma obtenu à la base réelle. Le diff est vide.

**Le LLM décide-t-il des identités ?**
Non. Il émet un **avis**, écrit dans une table séparée. Sa qualité a été mesurée
(60 cas d'étalonnage, puis 100 cas arbitrés à la main : 100 % de précision,
98 % d'accord). Seule une promotion bornée et journalisée transforme un avis en
décision, et l'humain garde le dernier mot — une série a d'ailleurs été
corrigée à la main contre l'avis du modèle.

**Pourquoi 57,3 % seulement d'identités automatiques ?**
Parce que le reste est en revue plutôt qu'en faux positif. Le principe tenu
partout : **le doute part en `needs_review`, jamais en automatique.** Un taux
plus élevé s'obtiendrait en baissant les seuils — au prix de rapprochements
faux, invisibles et durables.
