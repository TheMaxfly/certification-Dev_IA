# Registre des traitements de données personnelles — base `apimanga`

**Responsable de traitement** : Maxime — projet de certification RNCP37827,
Bloc 1. Traitement à finalité pédagogique et de démonstration technique.
**Base concernée** : PostgreSQL `apimanga`, schémas `manga` et `staging`, et
les instantanés bruts qui l'alimentent.
**Version** : 1.2 — 2026-09-29.
**Périmètre** : ce document couvre le **RGPD**. Il ne traite ni du droit
d'auteur ni du droit du producteur de base de données, qui relèvent d'un autre
régime et font l'objet de la section 7.

---

## 1. Inventaire — où sont les données personnelles

| Catégorie | Volume | Localisation | Personnes concernées |
|---|---|---|---|
| Pseudonymes de rédacteurs et leurs critiques | **11 073 critiques, 54 pseudonymes distincts** | `manga.ms_reviews_all`, `manga.ms_reviews`, `manga.rag_reviews_docs` | rédacteurs du staff Manga Sanctuary |
| Noms et rôles de créateurs | **30 678 crédits promus**, 53 183 en brut, **15 397 identifiants distincts** | `manga.kitsu_staff`, `staging.kitsu_staff`, raw Kitsu | auteurs, dessinateurs, scénaristes |
| Identifiants et formes de noms d'auteurs | **2 780 QID**, 9 090 labels et alias, 5 453 relations œuvre-auteur | `manga.wd_auteurs`, `manga.wd_auteurs_formes` | auteurs référencés sur Wikidata |
| **Noms de créateurs des sources francophones** | **7 285** formes distinctes (MS), **1 098** (MI), dont **3 092 n'existent dans aucune autre source** | `ms_series_enriched`, `ms_volumes_enriched`, `mi_sorties` | auteurs et dessinateurs du catalogue |
| Noms d'auteurs intégrés au texte RAG | ligne `Auteurs:` de **20 831** documents Kitsu sur 37 049 (staff Kitsu de juillet, rôles Scénario / Dessin) — v1.2 | `bench.corpus_docs`, `bench.corpus_chunks` | idem |
| Libellés de consommateurs de l'API | 1 (`app_backend`) | journaux applicatifs | aucune personne physique |

**Ne sont pas des données personnelles** : les personnes décédées (hors champ
du RGPD, sous réserve des règles françaises post mortem) ; les collectifs,
studios et sociétés d'édition ; les données de catalogue (titres, EAN, prix,
tomaison).

**Point de qualification retenu.** La signature des critiques Manga Sanctuary
**n'est pas collective**. Chaque critique porte un pseudonyme individuel assorti
d'un badge « STAFF » (exemple observé : `MassLunar`, 16 août 2026). Il s'agit
donc de 54 personnes physiques identifiables indirectement, dont la base
conserve les **opinions et appréciations** — un traitement plus intrusif que le
seul rattachement d'un nom d'auteur à une œuvre.

---

## 2. Registre des traitements

### T1 — Collecte et stockage des critiques signées (Manga Sanctuary)

| | |
|---|---|
| **Finalité** | Constituer un référentiel éditorial de critiques permettant un conseil de lecture argumenté et attribué |
| **Base légale** | Intérêt légitime — art. 6.1.f RGPD (mise en balance en §3) |
| **Personnes concernées** | 54 rédacteurs du staff Manga Sanctuary — 11 073 critiques signées sur 11 074, une sans signature |
| **Catégories de données** | Pseudonyme (`review_author`), titre, note, date brute et date ISO, texte intégral, URL de la critique, URL de la série et du tome |
| **Données non collectées** | Aucun identifiant de membre, aucune URL de profil, aucune adresse électronique, aucun nom civil. Le spider lit le lien `membre.php` mais n'en conserve que le **texte** |
| **Source** | Scraping de pages publiques, sans compte, sans cookie (`COOKIES_ENABLED = False`), `robots.txt` respecté (`ROBOTSTXT_OBEY = True`). Crawl complet : **135 745 requêtes au total, dont 130 814 réponses HTTP 200** ; une requête `robots.txt` répondue en HTTP 200 ; aucune interdiction robots enregistrée |
| **Destinataires** | Aucun tiers. Accès local par rôles PostgreSQL ; exposition via API par clé (T2) |
| **Transfert hors UE** | Aucun |
| **Durée de conservation** | Voir §4 — **à mettre en œuvre**, aucune purge n'existe à ce jour |
| **Mesures de sécurité** | §5 |

### T2 — Mise à disposition des critiques par l'API

| | |
|---|---|
| **Finalité** | Permettre l'exploitation du référentiel par les composants aval du projet |
| **Base légale** | Intérêt légitime, dans le prolongement de T1 |
| **Données exposées** | `GET /series/{series_id}/reviews` : `review_id`, numéro et URL du tome, **`review_url`**, titre, note, pseudonyme, dates, type, grain, texte intégral. Liste arrêtée et confrontée par test |
| **Règle de minimisation** | Les deux chemins de mise à disposition — API et export RAG — exposent la même chose. `review_url` pointe la **critique publiée**, non le profil du rédacteur : l'attribution d'une citation suppose que sa source soit vérifiable. Ce qui n'est pas exposé — identifiant de membre, URL de profil — ne l'est **nulle part**. Décision rendue et implémentée le 2026-08-25 (`ca66d50`) |
| **Destinataires** | Consommateurs applicatifs authentifiés par clé d'API (`X-API-Key`). Le client final n'en détient jamais |
| **Durée** | Identique à T1 : l'exposition cesse avec la donnée |

### T3 — Collecte et stockage des données de créateurs (Kitsu, Wikidata, Manga Sanctuary, Manga Insight)

*Périmètre étendu le 2026-09-07 : la mesure a établi que **3 092 noms de
créateurs n'existent que dans Manga Sanctuary** — ni Kitsu, ni Wikidata, ni
Manga Insight. Ce ne sont pas les mêmes personnes vues par une source de plus.
Même finalité, même base légale, même catégorie de données : le traitement est
étendu plutôt que dupliqué.*

| | |
|---|---|
| **Finalité 1** | **Identification et rapprochement des œuvres** entre référentiels |
| **Finalité 2 — PROJETÉE** | **Contextualisation** : enrichir la présentation d'une œuvre par la trajectoire publiée de son auteur. **Non implémentée à ce jour** ; aucun composant ne consomme `description`. **Échéance : bloc 2. À défaut de mise en œuvre, le champ est retiré du brut.** |
| **Exclusions explicites** | Aucun profilage de créateur, aucune évaluation, aucune mesure d'activité. **Une recherche à facettes biographiques est écartée** : la mesure la plafonne à **17,5 % du catalogue** (2 571 séries sur 14 670 atteignent un auteur Wikidata), et les données structurées nécessaires — date, lieu, nationalité — n'existent ni en base ni au brut |
| **Base légale** | Intérêt légitime — données professionnelles publiées à des fins de notoriété éditoriale. La notoriété ne retire pas la qualification de donnée personnelle ; elle rend l'intérêt légitime aisé à établir |
| **Personnes concernées** | Créateurs identifiables. Kitsu : **53 183 crédits, 15 397 identifiants distincts** au brut, **30 678 crédits promus**. Wikidata : **2 780 QID**. Manga Sanctuary : **7 285 noms distincts**. Manga Insight : **1 098**. Union des quatre sources : **20 591 formes distinctes** |
| **Part de personnes morales** | **0,1 à 0,8 % selon la source** — collectifs, studios et sociétés d'édition. Marqueurs explicites côté MS (`COLLECTIF`, `COLLECTIF CHINOIS`, `Japonais COLLECTIF`), directement exploitables pour l'exclusion. La quasi-totalité des noms sont donc des personnes physiques |
| **Catégories de données — production** | Nom, nom normalisé, rôle, œuvre associée, QID, variantes de forme (labels et alias en ja / en / fr). **Aucune colonne biographique n'existe dans le schéma** — vérifié par recherche sur le catalogue système |
| **Catégories de données — brut conservé** | `person_id`, `name`, `role`, `work_id`, `description` (**6 932 personnes**), `image` (**2 301 personnes**), `malId`, horodatages `createdAt` / `updatedAt` |
| **Champs à retirer** | **`image`** — suppression **décidée le 2026-08-26, NON EXÉCUTÉE** à ce jour. **`malId`** — **inexploitable** : les 15 397 personnes portent l'unique valeur `Moved to mappings relationship.` ; le motif « identifiant croisé » est **réfuté par la mesure** |
| **Minimisation — un choix contraint, et assumé** | L'hydratation Wikidata ne demande que `labels\|aliases` **pour les auteurs** — les œuvres, elles, tirent aussi `claims` et `sitelinks`. La restriction s'applique donc **exactement aux entités qui sont des personnes physiques**. Elle n'est pas un réglage fin : l'interface d'entités ne sait pas filtrer par propriété, `props=claims` rend **toutes** les affirmations ou aucune. C'est **le seul point d'arrêt disponible avant le tout**, et c'est l'option minimale qui a été retenue |
| **Durée** | §4 |

### T4 — Collecte Manga-News

| | |
|---|---|
| **Finalité** | Enrichissement du référentiel de séries. Apport propre : points forts éditoriaux et nom du traducteur, absents de toute autre source |
| **Données personnelles** | Noms d'auteurs et de traducteurs, relevant du même régime que T3 |
| **État — collecte REPRISE le 2026-09-07** | **11 781 séries et 50 entrées de classement**, collectées et validées (`overall_success: true`, 100 % des attentes, 0 échec). Volume précédent : 11 717 séries en juillet |
| **L'interruption d'août, et ce qu'elle établit** | Le 2026-08-25, le site a activé une **protection anti-robot générale** ; le collecteur s'est heurté à un refus. Le fichier d'exclusion est resté **inchangé** — il n'interdit qu'un espace privé du forum, et les chemins parcourus restaient autorisés. Il ne s'agissait donc **pas d'une interdiction visant cette collecte**, mais d'un durcissement technique de l'accès. **Aucun contournement n'a été tenté** : ni agent falsifié, ni navigateur automatisé, ni service tiers, ni proxy. La collecte a repris le 2026-09-07, au collecteur inchangé, lorsque l'accès est redevenu ouvert |
| **Contrainte contractuelle — connue et NON levée** | Les conditions d'utilisation limitent l'usage à la **navigation et à la copie privée** ; elles interdisent la reproduction et la diffusion. **Aucune autorisation n'a été sollicitée ni obtenue.** Le respect du protocole d'exclusion ne vaut pas licence de réutilisation |
| **Périmètre d'exploitation, en conséquence** | Strictement **académique** : contenus non redistribués, absents du dépôt, non exposés publiquement. Le déploiement d'évaluation est **restreint par clé et limité à la période d'évaluation**. Le projet ne fait l'objet d'aucun accès public. **Une autorisation écrite serait le préalable à tout usage dépassant ce cadre** |
| **Base de destination** | Base `manganews`, **distincte de `apimanga`**. Les données ne sont pas jointes au référentiel. Le chargement en base n'a pas été exécuté et relève d'une décision ultérieure |
| **Réserve** | Voir §7 — les conditions de ce site sont nettement plus restrictives que celles de Manga Sanctuary |

### T5 — Journalisation des accès à l'API

| | |
|---|---|
| **Finalité** | Traçabilité des accès aux données, détection d'usage anormal |
| **Base légale** | Intérêt légitime — sécurité du traitement |
| **Données** | Libellé du consommateur (`app_backend`), horodatage, route appelée, code de réponse. **La clé d'API n'est jamais journalisée**, ni en succès ni en rejet — vérifié par test |
| **Adresses IP — CONFIRMÉES** | Uvicorn 0.51.0 démarre avec `access_log=True` ; son format par défaut contient `%(client_addr)s`, et le Dockerfile ne désactive pas ce journal. **L'adresse cliente et le port source sont donc journalisés** avec la route et le statut. Ce sont des données personnelles : elles relèvent de ce traitement |
| **Durée** | **90 jours proposés — rotation non mise en œuvre**, §4 |

### T6 — Citation attribuée dans les réponses du chatbot *(bloc 2, anticipé)*

| | |
|---|---|
| **Finalité** | Restituer un conseil de lecture appuyé sur des critiques, **attribuées nominativement** |
| **Statut** | Traitement **décidé, non encore mis en œuvre**. Déclaré par anticipation |
| **Point d'attention** | L'attribution est simultanément une exigence éditoriale, une exigence de licence et un traitement de données personnelles. Elle ne peut pas être supprimée pour minimiser |

### T7 — Comptes clients et personnalisation *(bloc 3, anticipé)*

| | |
|---|---|
| **Finalité** | Comptes, listes de favoris, historique des conseils reçus |
| **Statut** | **Non mis en œuvre. Données prévues fictives** — scénario simulé, principes RGPD réellement appliqués |
| **Base de données** | `manga_app`, **séparée** de `apimanga` — voir §5 |
| **Profilage** | Des recommandations rattachées à un compte pour favoriser les ventes constituent du **profilage à des fins de prospection commerciale** : information de la personne et droit d'opposition requis, le conseil devant rester utilisable sans profil |
| **Sensibilité propre au domaine** | Un profil de goûts manga peut révéler des catégories particulières — les genres de l'axe LGBT du référentiel en sont l'exemple direct. À calibrer par la durée de conservation |

---

## 3. Mise en balance de l'intérêt légitime (art. 6.1.f)

L'intérêt légitime exige une mise en balance documentée. Elle est conduite ici
pour T1, le traitement le plus intrusif.

**Intérêt poursuivi.** Constituer un référentiel de conseil de lecture appuyé
sur des critiques argumentées, avec attribution de leur auteur. Intérêt réel et
légitime, non contraire à la loi.

**Nécessité.** Le pseudonyme est nécessaire : une critique anonymisée perdrait
sa valeur de conseil et priverait son auteur de l'attribution qui lui revient.
Le texte intégral est nécessaire à la citation. La date et la note qualifient
l'avis. **Aucune donnée collectée n'est superflue au regard de cette finalité**,
à l'exception de l'écart signalé en §6.1.

**Attentes raisonnables des personnes.** Les rédacteurs publient sous
pseudonyme, sur un site public, dans une rubrique explicitement éditoriale, avec
un badge de statut. Ils écrivent pour être lus et cités. La réutilisation aux
fins de conseil de lecture avec attribution se situe dans le prolongement de
cette publication.

**Ce qui pèse dans l'autre sens.** Ils n'ont pas été informés de cette
réutilisation. La collecte est massive et systématique. Le corpus permet, pour
chacun des 54 pseudonymes, de reconstituer l'ensemble de ses opinions publiées —
ce que la consultation du site ne permet pas aussi commodément. `review_url`
maintient une capacité de rattachement vers le profil.

**Mesures compensatoires retenues** : minimisation à la source (aucun
identifiant de membre, aucune URL de profil) ; accès restreint par rôle en
lecture seule et par clé d'API ; attribution systématique, jamais
d'appropriation ; durée de conservation bornée (§4) ; procédure de retrait
(§4.3).

**Conclusion.** L'intérêt légitime est retenu, sous réserve de la mise en œuvre
effective des mesures de §4 et §6 — sans lesquelles la balance ne tient pas.

---

## 4. Procédures de tri et fréquences d'exécution

> **État actuel, énoncé sans complaisance** : il n'existe **aucune politique de
> rétention ni aucune purge planifiée**. Le seul mécanisme temporel est une
> suppression des lignes de `staging` Manga-News de plus de 30 jours,
> **déclenchée uniquement à la fin d'un import** — si aucun import n'a lieu
> pendant six mois, aucune purge n'a lieu pendant six mois. Le principe
> documenté est même explicitement inverse : « raw = archive immuable ».
> Les durées ci-dessous sont donc une **politique à mettre en œuvre**, pas un
> état constaté.

### 4.1 Durées de conservation

| Catégorie | Durée | Fréquence d'exécution |
|---|---|---|
| Raw Manga Sanctuary (critiques, pseudonymes) | 12 mois, ou les 2 derniers instantanés validés | après chaque promotion mensuelle |
| Raw Kitsu complet (descriptions, images, horodatages) | **90 jours après promotion** | contrôle quotidien |
| Raw Wikidata (hydratation auteurs) | 6 mois | après chaque hydratation |
| Partitions Delta bronze / silver | durée de la source correspondante | purge mensuelle + `VACUUM` différé |
| `staging` SQL | 30 jours | **quotidienne, indépendamment des imports** |
| Sauvegardes PostgreSQL | 30 jours, minimum 2 sauvegardes valides conservées | quotidienne |
| Journaux techniques de collecte | 30 jours | rotation quotidienne |
| Journaux d'accès et d'autorisation API | 90 jours | rotation quotidienne |
| Exécutions réussies, points de reprise devenus inutiles | 7 jours | quotidienne |
| Exécutions échouées ou interrompues | 30 jours | quotidienne |
| Rapports contenant noms, décisions ou exemples | 24 mois | mensuelle |
| Manifestes, empreintes SHA-256, métriques agrégées | longue durée | — aucune donnée personnelle |
| Données finales de créateurs | tant que l'œuvre figure au catalogue | réexamen annuel |
| Critiques et pseudonymes en base | tant que la critique reste publiée à la source | réconciliation mensuelle |

### 4.2 Procédure de retrait d'une critique disparue de la source

Une critique absente d'un instantané ne doit **pas** entraîner une suppression
immédiate : un crawl peut être incomplet. Règle retenue :

1. absence constatée dans **deux instantanés complets successifs**, ou
   vérification ciblée de l'URL ;
2. retrait de l'exposition par l'API ;
3. suppression définitive sous 30 jours.

### 4.3 Exercice des droits

Aucun canal de contact avec les rédacteurs n'existe. En cas de demande reçue —
opposition, effacement, accès — la procédure est : identification de la ou des
lignes par `review_author`, suppression dans `ms_reviews_all`, `ms_reviews` et
`rag_reviews_docs`, retrait des documents RAG dérivés, et **inscription du
pseudonyme dans une liste d'exclusion** consultée à chaque promotion — sans
quoi le crawl suivant réintroduirait la donnée.

### 4.4 Outillage à construire

Le critère exige que les procédures détaillent les traitements de conformité
**et leur fréquence**. Cela suppose une commande dédiée, non encore écrite :

- `retention audit` — inventaire et calcul de l'âge par catégorie, sortie JSON
  horodatée, sans écriture ;
- `retention purge` — exécution, avec **liste blanche stricte** des répertoires
  et tables autorisés ;
- conservation des manifestes et empreintes après suppression du contenu brut ;
- journal du nombre de fichiers, lignes et octets supprimés ;
- alerte si la dernière exécution réussie remonte à plus de 48 heures ;
- planification par timer quotidien — le workflow CI actuel ne comporte aucun
  `schedule`.

**Point connexe** : plusieurs points de reprise Scrapy sont versionnés dans Git.
Une suppression locale ne les retirerait pas de l'historique, qui constitue
lui-même une conservation sans durée. À sortir du suivi.

---

## 5. Mesures de sécurité

Les trois premières sont **prouvées par test**, ce qui est rare dans un dossier
de ce type.

| Mesure | Preuve |
|---|---|
| Rôle PostgreSQL en lecture seule (`manga_ro` / `manga_api`) | témoin positif : 6 refus en `SQLSTATE 42501` (INSERT, UPDATE, DELETE, TRUNCATE, accès `staging`, `nextval`), DSN construit par le code de production lui-même |
| Autorisation de l'API par clé | 401 sans clé, avec clé invalide et avec clé valide à un caractère près ; refus de démarrer si la configuration est absente ou malformée |
| Non-journalisation des secrets | ni la clé valide ni la clé rejetée n'apparaissent dans les journaux ; seul le libellé du consommateur y figure |
| Aucun secret versionné | vérifié par contrôle pré-commit à témoin positif ; DSN par variable d'environnement ou `~/.pgpass` |
| **Privacy by design — séparation des bases** | Le référentiel (`apimanga`, reconstructible, impersonnel) et l'applicatif (`manga_app`, irremplaçable, personnel) sont deux bases distinctes, avec des cycles de vie, des sauvegardes et des régimes juridiques séparés |
| Absence d'écriture par l'API | structurelle : aucun endpoint d'écriture, et le rôle de connexion en est incapable |

---

## 6. Écarts identifiés — à corriger

### 6.1 `review_url` — RÉSOLU le 2026-08-25 (`ca66d50`)

L'écart consistait en ce que `GET /series/{id}/reviews` n'exposait pas
`review_url` alors que le circuit RAG le plaçait dans ses métadonnées : deux
chemins de mise à disposition, deux règles de minimisation différentes.

**Arbitrage rendu : aligner par le haut.** `review_url` est exposé par les deux
chemins, avec une description OpenAPI précisant qu'il pointe la **critique
publiée à la source**, non le profil du rédacteur. Motif : l'attribution d'une
citation suppose que sa source soit vérifiable — supprimer le lien aurait
affaibli un engagement du projet sans rien retirer d'identifiant, le profil
n'étant ni stocké ni exposé.

Liste des champs arrêtée et confrontée par test ; égalité de `review_url` avec
`ms_reviews_all` vérifiée sur trois séries ; 401 sans clé et absence de tout
champ supplémentaire vérifiés au harnais.

### 6.2 Raw Kitsu — arbitrage rendu, puis **corrigé par la mesure**

**Constat.** Le brut conserve `description`, `image`, `malId` et horodatages sur
15 397 identifiants distincts (53 183 crédits), alors que le chargeur ne lit que
l'identifiant, le nom et le rôle. Manquement à la minimisation **invisible** :
rien ne dysfonctionne, aucun test ne rougit, la production est propre.

**Premier arbitrage (2026-08-26)** : conserver `description` sous une finalité
de recommandation déclarée, conserver `malId` comme identifiant croisé,
supprimer `image`. **L'audit a réfuté deux de ces trois motifs.**

| Champ | Mesure d'audit | Statut corrigé |
|---|---|---|
| `person_id`, `name`, `role`, `work_id` | consommés par le chargeur | conservés |
| `description` | **6 932 personnes**, **aucune consommation actuelle** | finalité de recommandation **projetée, non implémentée** : soit elle est mise en œuvre, soit le champ est retiré |
| **`image`** | **2 301 personnes** | suppression **décidée, NON EXÉCUTÉE** — ni sur l'existant, ni dans la collecte future |
| **`malId`** | **0 exploitable** — les 15 397 personnes portent l'unique valeur `Moved to mappings relationship.` | motif « identifiant croisé » **réfuté**. À retirer, sans arbitrage à rendre |
| `createdAt` / `updatedAt` | conservés | métadonnées de source **rattachées à une personne** : leur durée doit être justifiée, pas seulement leur nature |

**Obstacle d'exécution, découvert par l'audit.** Le manifeste du run référence
`relations/staff.ndjson` par sa taille (**120 534 066 octets**) et son SHA-256
(`53499f88…`). Il **dépend donc du fichier à minimiser**. Réécrire le brut
existant contredirait simultanément le manifeste et la doctrine « raw immuable ».

**Deux procédures traçables, à trancher avant exécution** : produire un
**instantané dérivé minimisé** doté de son propre manifeste, ou **transformer
l'existant** en documentant la rupture et en recalculant taille et empreinte.
Dans les deux cas, `image` et `malId` sont exclus des collectes futures.

> **Correction de raisonnement à retenir.** Il avait été posé que déclarer une
> finalité suffisait à légitimer la conservation. C'est incomplet : une finalité
> **déclarée mais non implémentée** reste un usage hypothétique, et la
> minimisation ne s'en satisfait pas. Soit `description` est consommée, soit
> elle part.

### 6.2bis Profilage des rédacteurs de critiques — ÉCARTÉ

La possibilité d'inférer un profil de goûts pour chacun des 54 rédacteurs, à
partir de leurs 11 073 textes, afin d'affiner la recommandation, a été
**examinée et écartée**. Consigné ici parce qu'un traitement écarté
explicitement vaut mieux qu'un traitement non envisagé.

Trois motifs, dans l'ordre de solidité :

1. **C'est du profilage de personnes physiques** à des fins de recommandation
   commerciale, sur 54 personnes identifiables qui n'ont jamais été informées de
   la réutilisation de leurs écrits.
2. **L'apport est quasi nul** : ce que le système a besoin de savoir, c'est si
   *cette critique* parle favorablement d'un genre — pas si *ce rédacteur* aime
   ce genre. L'information est déjà dans le texte, déjà dans le corpus. Le
   profil de l'auteur est un détour vers une donnée déjà détenue.
3. **La distinction avec T3 est de nature, pas de degré** : la biographie d'un
   créateur décrit une activité publique de création, publiée à cette fin ; un
   profil de goûts inféré décrit des préférences individuelles que personne n'a
   déclarées.

**Le même mécanisme reste légitime ailleurs** : les avis libraires (§14 du
journal projet, bloc 3) prévoient un profil de genres de prédilection
**déclaré par le libraire lui-même**, avec consentement et droit de retrait.
Profil consenti et déclaré d'un côté, inféré et subi de l'autre — même
mécanique, situations opposées.

### 6.3 Affirmation non étayée — RÉSOLU le 2026-08-25 (`b7783fd`)

Le README de `01_scraping_manganews/` affirmait « crawls autorisés », sans
courriel, contrat ni accord pour l'appuyer. Remplacé par l'état factuel : pages
publiques sans compte ni cookie, `ROBOTSTXT_OBEY = True`, **aucune autorisation
sollicitée ni obtenue**, droits de reproduction et de diffusion réservés par les
CGU.

### 6.4 Preuves de conformité hors suivi Git

`status.json`, `run.json` et les HTML figés attestent la consultation de
`robots.txt` mais sont ignorés par Git : modifiables et susceptibles de
disparaître. Aucune copie datée du `robots.txt` ni des CGU n'a été conservée.

**À constituer** : un dossier d'audit non public archivant, avec leurs
empreintes SHA-256, les `robots.txt`, les CGU, les en-têtes HTTP et les fichiers
d'état des collectes.

**Conséquence tant qu'il n'existe pas** : les constats juridiques du §7 doivent
être présentés comme **datés et à consolider**, jamais comme une preuve pérenne.
Un `robots.txt` ou des CGU peuvent changer sans préavis ; ce qui n'est pas
archivé n'est pas opposable.

### 6.5 Adresses IP dans les journaux — **CONFIRMÉES**

Vérification faite. Uvicorn 0.51.0 démarre avec `access_log=True` ; son format
par défaut contient `%(client_addr)s`, et le Dockerfile ne désactive pas ce
journal. **L'adresse cliente et le port source sont journalisés** avec la route
et le statut HTTP.

Ce sont des données personnelles. Elles sont déclarées dans T5. La rotation de
90 jours reste **à mettre en œuvre** — comme toutes les durées de §4.

---

## 7. Réserve hors RGPD — droit d'auteur et droit du producteur de base

**Ce point ne relève pas du présent registre et n'est pas résolu par lui.** Il
est consigné ici pour ne pas être présenté comme traité.

| Source | Constat |
|---|---|
| Manga Sanctuary | Pages publiques, `robots.txt` respecté. **Aucune interdiction explicite du scraping identifiée dans les CGU**, mais celles-ci réservent la reproduction et la diffusion à une autorisation écrite de l'éditeur. Aucune autorisation n'a été sollicitée |
| Manga-News | `robots.txt` respecté et **inchangé**. **CGU nettement plus restrictives** : usage limité à la navigation et à la copie privée. Interrompue le 2026-08-25 par une protection anti-robot générale, **sans contournement** ; **reprise le 2026-09-07**. Contrainte contractuelle connue et non levée ; exploitation bornée au cadre académique — cf. T4 |
| Kitsu | API publique. La mention de licence Apache 2.0 porte vraisemblablement sur l'API ou sa documentation, non sur le contenu du catalogue. Copie datée des CGU à constituer |
| Wikidata | **CC0** — mais CC0 règle les droits d'auteur et de base, **pas** les droits relatifs aux données personnelles |
| Sources citées par les textes Kitsu *(v1.2)* | Les synopsis Kitsu du corpus citent leur provenance, retirée du texte et conservée en `metadata_json.source_citee` : **« MU » désigne MangaUpdates** (6 408 mentions sous MU, M-U, MangaUpdates), puis ANN 732, MangaHelpers 667, Tapas 429, MangaDex 407, Tokyopop 301. **Le régime de licence de MangaUpdates sur ses synopsis n'a pas été instruit.** Même dossier que les descriptions de personnages Kitsu citant un wiki (Wikipédia, Wikia, Fandom), présumées CC BY-SA avec attribution requise, les autres provenances restant « à instruire » |

S'ajoutent, indépendamment des CGU, les articles **L342-1 et L342-2 du CPI**
(extraction d'une partie substantielle, extractions répétées excédant
l'utilisation normale). L'accès public et le respect de `robots.txt` ne
constituent pas une licence de réutilisation.

**Formulation retenue pour le dossier** :

> Les pages collectées étaient publiquement accessibles sans compte. Le crawler
> respectait automatiquement `robots.txt`, dont la consultation est attestée
> dans les statistiques d'exécution. Les CGU ne comportaient pas d'interdiction
> explicite du scraping identifiée, mais réservaient les droits de reproduction
> et de diffusion. En l'absence d'autorisation écrite, la réutilisation
> exhaustive des contenus textuels reste juridiquement non sécurisée.

Mesure la plus solide, si le projet devait dépasser le cadre pédagogique :
solliciter une autorisation écrite des éditeurs, et archiver le dossier d'audit
de §6.4.

---

## 8. Plan de correction

| # | Action | État au 2026-09-07 |
|---|---|---|
| 1 | Corriger l'affirmation d'autorisation Manga-News (§6.3) | **fait — `b7783fd`** |
| 2 | Aligner `review_url` entre API et RAG (§6.1) | **fait — `ca66d50`** |
| 3 | Vérifier la journalisation des adresses IP (§6.5) | **fait — présence confirmée** ; rotation à mettre en œuvre |
| 4 | Étendre T3 aux noms de créateurs MS et MI — 3 092 personnes non recensées | **fait — v1.1** |
| 5 | Reformuler la finalité 2 en contextualisation, avec échéance de retrait | **fait — v1.1** |
| 6 | Corriger les noms de tables inexacts (`wd_auteurs`, `wd_auteurs_formes`) | **fait — v1.1** |
| 7 | Actualiser T4 : reprise de collecte, périmètre d'exploitation | **fait — v1.1** |
| 8 | Trancher la procédure de minimisation du raw Kitsu (§6.2) | **à réarbitrer** : le manifeste dépend du fichier, `malId` est inexploitable |
| 9 | Retirer `image` et `malId` de la collecte future | à faire |
| 10 | Implémenter la finalité de contextualisation, ou retirer `description` | **échéance bloc 2** |
| 11 | Constituer le dossier d'audit horodaté (§6.4) | à faire |
| 12 | Écrire et tester `retention audit` / `retention purge` (§4.4) | à faire |
| 13 | Sortir les points de reprise Scrapy du suivi Git (§4.4) | à faire |
| 14 | Planifier l'exécution quotidienne (timer ou `schedule` CI) | après 12 |

**Ce que le critère C4 exige et ce que ce document apporte** : le registre
couvre l'ensemble des traitements impliqués dans la base (§2) ; les procédures
de tri sont rédigées (§4) ; leur fréquence d'exécution est détaillée (§4.1).

**Ce qu'il ne faut pas laisser croire** : les durées de §4.1 sont **projetées**,
non appliquées. L'outillage de §4.4 n'existe pas, aucune planification n'est
active, et la seule purge existante ne se déclenche qu'à l'occasion d'un import.
Présenter ces fréquences comme effectives serait faux. **Les déclarer avec leur
plan et leur état réel est ce que le critère demande.**
