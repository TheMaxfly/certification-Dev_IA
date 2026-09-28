# Reconstruction du corpus RAG — règle, décisions, résultats

**2026-09-28.** Le corpus du schéma `bench` est reconstruit sur le référentiel
`manga.ms_reviews_all` et le snapshot Manga Sanctuary 2026-07, **sans aucune
donnée d'auteur**. La règle a été écrite et validée **avant** tout comptage.

Code : `src/corpus/` (`construire`, `mesurer`, `decoupage`, `pseudonymes`).
Rapports d'exécution : `corpus_execution_20260928T175529Z.md` (chargement),
`corpus_execution_20260928T175834Z.md` (rejeu), `corpus_a_blanc_20260928T180105Z.md`
(rechargement complet), `corpus_mesures_20260928T180457Z.md` (mesures).

## Environnement

PostgreSQL 16.15 local, base `apimanga`, DSN par `DATABASE_URL`, catalogue lu
dans `pg_catalog`. Toutes les inspections préalables ont été faites en session
`default_transaction_read_only=on`. Le chargement est une transaction unique,
annulée si un contrôle échoue.

## Pourquoi reconstruire

Le corpus de décembre 2025 (51 880 documents) portait **3 187** critiques : les
47,22 % d'avant le correctif canari, tronquées. Il comptait en outre deux fois
les mêmes textes : un document `ms_hybrid` concaténait toutes les critiques
d'une série et son synopsis Kitsu, déjà présents chacun comme document propre
(4 980 des 5 608 `ms_hybrid` n'étaient que le synopsis recopié). Aucun script
du dépôt n'avait écrit ce corpus.

## Le schéma `bench` est dans la chaîne de migrations

Créé par `database/migrations/000_baseline.sql` ; aucune autre migration n'y
touche ; `pg_dump -n bench` de la base réelle = baseline (diff trié vide), et
checksum de `000` intact. **Aucune migration n'a été nécessaire.**

## La règle

### Sélection — grain critique

| Clause | Énoncé | Restent |
|---|---|---:|
| S1 | source `manga.ms_reviews_all`, jamais `ms_reviews` (table historique, 3 187) | 11 074 |
| S2 | critique présente dans le snapshot 2026-07, lu dans le raw dont l'empreinte est vérifiée contre `MANIFEST.md` | 11 052 |
| S3 | corps non vide, **sans seuil** : aucun corps entre 1 et 108 caractères, un seuil aurait été décoratif | 11 051 |
| S5 | `series_id` non nul — garde qui arrête le chargement, pas un filtre (la FK rend les orphelines impossibles) | 11 051 |
| S6 | un document par (série, corps identique) : 10 textes publiés deux fois | **11 041** |

Le candidat « 11 051 » de la passation correspondait à S1–S3 : il ignorait les
doublons. Le « ~59 767 » annoncé valait 51 880 − 3 187 + 11 074 : il comptait
les critiques hors snapshot, le corps vide, les doublons, et gardait les
`ms_hybrid`.

### Construction

| Type | Document | Nombre |
|---|---|---:|
| `ms_review` | une critique : `ms_review:<id de critique du site>`, texte = `Critique Manga <série> #<tome>` + corps intégral | 11 041 |
| `kitsu_synopsis` | inchangés, régénérés depuis `manga` — identiques ligne pour ligne aux 43 085 de décembre | 43 085 |
| `ms_hybrid` | **non reconstruit** (D1) | 0 |
| **total** | | **54 126** |

Les anciennes clés `ms_review:N` numérotaient `rag_reviews_docs.doc_id`, une
troisième numérotation. Pour 3 184 des 3 187 anciennes critiques, le même
`review_id` désigne une autre critique entre `ms_reviews` et `ms_reviews_all`.
La nouvelle clé est l'identifiant stable du site.

### Anonymisation

- **Aucun identifiant d'auteur** dans `bench`, ni en clair, ni haché, ni
  opaque. Les métadonnées ne portent ni `review_author`, ni `review_url`, ni
  `review_title`.
- **Références à un membre dans le texte : masquées.** 46 critiques contiennent
  un mot égal à l'un des 54 pseudonymes. À la lecture, **20 occurrences sur 19
  critiques** sont de vraies références : remerciements, signatures d'un tiers,
  collègues cités. Elles sont remplacées par `[membre]` ; la critique reste.
  Les 27 autres sont des **homonymes** (personnages, mangakas). Deux listes
  versionnées, `database/donnees/corpus_references_masquees.csv` (20) et
  `corpus_homonymes_admis.csv` (119, dont 92 synopsis Kitsu), portent ce
  classement. Les pseudonymes y sont remplacés par une empreinte, pour ne pas
  être republiés en clair.
- **Test de non-fuite** sur textes, titres, métadonnées et fragments : **0**
  occurrence hors homonymes admis.

**Précision d'application.** Une coupure de fragment peut trancher un mot plus
long et laisser en bord de fragment un début de mot égal à un pseudonyme. C'est
arrivé une fois : un mot de 6 lettres coupé à 4. Une occurrence de fragment
absente du document et située en bord de fragment est donc classée **coupure**
: comptée et rapportée, non bloquante. Toute autre occurrence reste une fuite.

**Limite, à connaître.** Le corpus est **sans pseudonyme**, il n'est **pas
anonyme au sens du RGPD** : un extrait verbatim d'une critique publique se
retrouve par un moteur de recherche, et l'identifiant de critique reconstitue
son URL. Cette limite est à porter à l'arbitrage RGPD.

## Décisions

| | Décision |
|---|---|
| D1 | `ms_hybrid` non reconstruit : double compte par construction, appariement Kitsu de 2025-12 supplanté par la cascade d'identité, grain série déjà décidé ailleurs (`series_profile`) |
| D2 | snapshot lu dans le raw manifesté, non dans `staging` (écrasé à chaque cycle) |
| D3 | dédoublonnage par (série, corps identique) |
| D4 | identifiant de critique = identifiant du site, stable ; `review_id` est une séquence déjà renumérotée une fois |
| D5 | masquage des vraies références, liste d'homonymes admis, tous deux versionnés |
| D6 | le banc de décembre archivé **avant** toute écriture : `data/archives/bench_2025-12/`, restauration prouvée par 14 empreintes identiques |
| D7 | les rares commentaires enregistrés comme critiques restent, signalés : aucun critère mécanique ne les sépare |

## Contrôles

| Contrôle | Résultat |
|---|---|
| §7.1 critiques retenues = documents `ms_review` | différence symétrique **vide** (0 / 0) |
| §7.2 rejeu | **0 écriture** : aucun document, aucun fragment touché |
| §7.3 rechargement complet (vidé puis reconstruit, transaction annulée) | empreintes documents et fragments **identiques** |
| §7.4 non-fuite | **0** ; 119 homonymes admis rencontrés ; 1 coupure de fragment |
| §7.5 type, rattachement, identifiant de source | 0 écart |
| Fidélité du découpage | 43 832 fragments Kitsu **inchangés, `chunk_id` compris** (empreinte = archive) |

Tests : `tests/test_corpus_*.py`. La fidélité du découpage est éprouvée contre la
fonction de décembre, extraite du source du module 06. Les chargements de bout
en bout tournent sur base jetable : rejeu, rechargement, fuite, raw altéré,
critique sans série, liste périmée.

## Mesures (§8)

| | |
|---|---|
| Critiques retenues — longueur du corps | min 109 · médiane 2 033 · p95 4 123 · max 9 667 · 0 sous 80 · 7 sous 200 |
| Fragments | **71 940** : 43 832 Kitsu + 28 108 critiques ; le plus long fait 1 200 caractères, aucune troncature |
| Documents sans fragment | **150** synopsis Kitsu de 30 à 49 caractères — le plancher de 50 du banc de décembre, conservé pour la comparabilité et désormais compté |
| Index, float32, vecteurs seuls | 110,5 Mo (384 d) · 221,0 Mo (768 d) · 294,7 Mo (1 024 d) |
| Auto-référence (A3), mesurée sans filtrage | au moins un marqueur M2–M6 : 122 critiques (1,10 %) |

Les 90 000 à 100 000 fragments attendus supposaient les `ms_hybrid` : leur
retrait (D1) explique l'écart.

Sur l'auto-référence, **1,10 % surestime le phénomène**. Les marqueurs « merci
à » et « signature finale » sont dominés par des faux positifs (remerciements
aux éditeurs, « par l'auteur »). Les exemples d'âge et de prénom lus sont des
citations d'œuvres. Le signal substantiel est l'entourage familial (« mon fils
de 6 ans »), sur 14 critiques au plus. Ce taux sert l'arbitrage RGPD ; il ne
filtre rien.

## Effet sur le banc de décembre

Les qrels et résultats qui visaient un document retiré sont partis par
`ON DELETE CASCADE` : restent **3 qrels sur 157** et **325 résultats sur
1 200**, ceux qui visent des synopsis Kitsu. `queries`, `metrics`,
`embedding_runs` sont intacts. L'état complet est archivé et restaurable.

## Ce que je n'ai pas pu établir

- l'opération qui avait rempli `bench.corpus_docs` en décembre : elle était hors
  dépôt ;
- pourquoi 22 critiques ont disparu du site entre les snapshots (retrait
  d'auteur ? réorganisation ?) ;
- combien de commentaires se cachent parmi les critiques de plus de 200
  caractères — seules les plus courtes ont été lues ;
- le classement des 46 occurrences de pseudonymes et des 92 occurrences Kitsu
  est une lecture humaine, non contre-vérifiée ;
- le taux de faux positifs des marqueurs M2–M6 n'est pas mesuré, seulement
  estimé à la lecture de cinq exemples par marqueur ;
- la stabilité de `ms_reviews_all.review_id` en cas de rechargement complet du
  référentiel n'a pas été testée.

## Ce qui reste

- **Embeddings et index** : aucun n'est calculé sur le nouveau corpus. Les deux
  runs de décembre (`embedding_runs`) et leurs index FAISS ne correspondent plus
  aux fragments en base.
- **Qrels** : à reconstruire au grain entité, sur 30 à 50 requêtes, familles
  adverses comprises. La métrique `recall@K` est à renommer en `hit_rate@K`.
- **Plancher de 50 et découpage 1 200 / 200** : valeurs de contrôle, à contester
  sur le jeu d'évaluation.
- **API** : `/rag/export` sert toujours les vues `manga.rag_*`, fondées sur les
  3 187 anciennes critiques. Le corpus reconstruit n'est pas encore exposé.

## Addendum du 2026-09-29 — la part Kitsu suit le même snapshot

La construction C2 ci-dessus (« `kitsu_synopsis` inchangés, régénérés depuis
`manga` ») laissait les synopsis Kitsu sur la table de décembre 2025, alors que
D2 imposait le raw 2026-07 pour toutes les sources. Corrigé le 2026-09-29 : la
part Kitsu est construite depuis le raw Kitsu de juillet manifesté, **37 049**
documents, synopsis exigé, manga/manhwa/manhua. Le corpus compte désormais
**48 090 documents et 66 290 fragments** ; la part critiques est inchangée.
Règle, mesures et atteignabilité : `corpus_kitsu_juillet_20260929.md`.
