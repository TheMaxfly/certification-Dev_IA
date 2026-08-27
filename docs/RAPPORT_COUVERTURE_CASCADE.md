# Rapport de couverture de la cascade d'identité — synthèse finale

> État remesuré au **2026-08-26**, après l'arrêté initial du 2026-07-21.
> Ce n'est pas un document de communication — les orphelines et la file
> résiduelle sont montrées telles quelles.

## Traçabilité des mesures

Une seule commande recalcule les chiffres et émet un JSON confrontable, sans
horodatage afin que deux exécutions identiques soient comparables par `diff` :

```bash
cd 05_nettoyage_agregation_bdd
DATABASE_URL='postgresql://manga_api@localhost:5432/apimanga' \
  uv run python -m identity.mesure_couverture_cascade \
  --output /tmp/couverture.json
```

Le script
[`mesure_couverture_cascade.py`](../05_nettoyage_agregation_bdd/src/identity/mesure_couverture_cascade.py)
impose le rôle `manga_api`, vérifie que la transaction est en lecture seule et
contrôle le contrat de colonnes via `pg_catalog`. Sa requête unique est livrée
dans
[`mesure_couverture_cascade.sql`](../05_nettoyage_agregation_bdd/src/identity/sql/mesure_couverture_cascade.sql).
Les références utilisées ci-dessous correspondent aux clés du JSON :

- **[M2]** `section_2` et `section_2_comparaison` : catalogue, décisions
  courantes, orphelines, rejet et somme de contrôle ; couvre aussi les nombres
  repris sans modification aux §1, §5 et §8.
- **[M3]** `section_3` : contribution de chaque étage et strates de promotion.
- **[M4]** `section_4` : recomptage de la grille humaine versionnée et de ses
  strates ; les trois libellés humains de confirmation sont définis dans le
  script.
- **[M6]** `section_6` et `section_6_comparaison` : pivot, sitelinks et
  identifiants exposables.
- **[M7]** `section_7` et `section_7_comparaison` : file résiduelle recalculée.
- **[M38]** `section_38_genres` : couverture et absence de genre aux sources.
- **[M44]** `section_44_formes_auteur` : mesure certifiée de la dette 22.3 ; le
  même exécuteur appelle la requête dédiée déjà versionnée.

---

## 1. Ce que « couverture » veut dire ici (le principe, avant les chiffres)

Le projet part d'**un seul** catalogue de référence : les **14 670 séries**
de Manga Sanctuary (la source qui définit le périmètre). « Couvrir » une
série, ce n'est pas la retrouver dans sa propre source — elle y est par
construction. C'est établir un **pont d'identité** entre cette série et les
référentiels externes (Wikidata, Kitsu, et à travers eux MyAnimeList,
AniList) : « la série MS n° X *est* l'œuvre Q… chez Wikidata, le manga n°…
chez Kitsu ». Une fois ce pont établi, la série hérite d'une identité
mondiale — et devient enrichissable (synopsis Wikipédia, tags AniList,
page japonaise…).

**Le taux de couverture mesure donc la proportion du catalogue reliée, de
façon sûre et automatique, au savoir mondial sur ces œuvres.** Son plafond
n'est pas 100 % : c'est la taille de l'**intersection** entre le catalogue
MS et ce que les référentiels connaissent. Une œuvre que Wikidata et Kitsu
ignorent ne peut être reliée à rien — il n'existe pas d'autre bout de fil à
nouer.

---

## 2. Le résultat global

Mesure **[M2]** :

| Population | Séries | % du catalogue | Nature |
|---|---:|---:|---|
| **Identités automatiques (sûres)** | **8 413** | **57,3 %** | reliées, enrichissables |
| En file de revue humaine | 952 | 6,5 % | candidats trouvés, arbitrage en cours |
| Orphelines (aucun candidat) | 5 304 | 36,2 % | absentes des référentiels externes |
| Rejetée (correction humaine) | 1 | — | faux positif du pont corrigé |
| **Total** | **14 670** | 100 % | |

La confrontation n'a produit aucun écart depuis l'arrêté initial :

| Chiffre porté au 2026-07-21 | Attendu | Mesuré au 2026-08-26 | Écart |
|---|---:|---:|---:|
| Identités automatiques | 8 413 | 8 413 | 0 |
| En revue | 952 | 952 | 0 |
| Orphelines | ~5 304 | 5 304 | 0 |
| Rejetées | 1 | 1 | 0 |
| Total catalogue | 14 670 | 14 670 | 0 |
| Cas confirmés dans l'échantillon stratifié | 100/100 | 100/100 | 0 |

Somme de contrôle **[M2]** : **8 413 + 952 + 5 304 + 1 = 14 670**.

**Lecture** : 57,3 % du catalogue est relié automatiquement. Le contrôle humain
du socle antérieur aux promotions LLM n'a détecté aucun faux positif sur 100 cas
stratifiés (§4). 6,5 % supplémentaires ont des candidats et attendent un
arbitrage léger. Les 36,2 % restants n'ont **aucun** candidat — non par échec du
matching, mais parce que l'œuvre n'existe pas dans les référentiels occidentaux
(§5).

La v2 atteint 57,3 % de rattachements sûrs *et* une frontière nette et
expliquée entre le reliable-relié et l'absent-des-référentiels. Le chiffre
n'a pas seulement monté : il est devenu **interprétable**.

---

## 3. D'où viennent les 8 413 identités — la cascade par étages

Le principe directeur : **précision avant rappel**. On prend d'abord tout ce
qui est sûr et gratuit (les identifiants croisés, sans lire un titre), puis
on descend vers l'incertain (les titres, puis le flou), et on ne décide
jamais automatiquement en dessous d'un certain niveau de preuve.

Mesure **[M3]** :

| Étage | Méthode | Comment on reconnaît | Auto |
|---|---|---|---:|
| 0 — pont | `kitsu_bridge` | identifiants croisés publiés par les sources — **zéro titre lu** | 1 689 |
| 1 — exact Wikidata | `exact` / `exact_author` | une forme normalisée commune, désambiguïsée par l'auteur puis l'année | 1 169 |
| 2 — référentiel Kitsu | `exact_kitsu` / `exact_kitsu_author` | idem contre 155 003 formes Kitsu + staff + année | 4 576 |
| 3 — flou | `trgm` | ressemblance approximative (jamais d'auto — propose seulement) | 0 (voulu) |
| R — juge LLM | `llm_review` | dossier complet jugé, verdict structuré, après mesure et arbitrage | 980 |
| — correction | `human_review` | faux positif du pont corrigé à la main | (−1) |

Le pont compte 1 689 décisions automatiques historiques ; après la correction
humaine conservée dans le journal, 1 688 restent courantes. La somme des
contributions courantes retombe sur 8 413 **[M3]**.

**Deux principes de reconnaissance, à retenir pour la soutenance :**

- **Le pont ne lit aucun titre.** Kitsu publie ses correspondances vers
  MyAnimeList/AniList ; Wikidata porte les mêmes identifiants (P4087, P8731).
  Chaîner ces numéros (`ms_kitsu_map → kitsu_mappings → wd_pivot`) relie une
  série à son QID par pures jointures SQL — la langue ne se pose même pas,
  un identifiant est un identifiant.
- **Les titres se reconnaissent sans traduction.** Chaque source liste
  *plusieurs* noms par œuvre (français, japonais, anglais, romanisations).
  La reconnaissance est une **intersection de nuages de formes** : il suffit
  qu'une seule forme MS soit identique à une seule forme externe (après une
  normalisation unique, la même des deux côtés) pour relier. Le titre
  japonais gardé en alias accroche le label japonais de Wikidata caractère
  pour caractère — le multilinguisme est une redondance, pas un obstacle.

### Signal auteur certifié

La dette 22.3 a été certifiée le 2026-08-25 à **100,00 % (1 058/1 058)** : les
1 058 décisions `exact_author` retrouvaient alors une concordance passant par
au moins une forme latine. Le chiffre compagnon corrigé est **21, non 4** :
seules 21 séries concordent sur le nom retenu en D0 sans la table des formes
multiples **[M44]**.

Confrontation **[M44]** :

| Mesure auteur | Certifié au 2026-08-25 | Rejoué au 2026-08-26 | Écart |
|---|---:|---:|---:|
| Décisions `exact_author` | 1 058 | 1 058 | 0 |
| Concordances retrouvées | 1 058 | 749 | −309 |
| Avec au moins une forme latine | 1 058 | 749 | −309 |
| Part latine parmi les concordances retrouvées | 100,00 % | 100,00 % | 0 pt |
| Concordances sur le nom D0 seul | 21 | 21 | 0 |

Le rejeu du 2026-08-26 expose toutefois un écart qui ne doit pas être corrigé
en silence : **749 concordances sur 1 058 décisions sont retrouvées (70,79 %),
soit 309 manquantes** ; les **749/749** retrouvées passent toutes par une forme
latine. La décision courante compte toujours 1 058 lignes et le contrefactuel
D0 reste à 21. L'état des données de référence a donc dérivé depuis la mesure
certifiée, ou son instantané initial n'est plus reconstructible ; la mesure
livrée établit le constat sans attribuer une cause non démontrée **[M44]**.

La cause réelle est mesurée séparément sur les auteurs MS : **28 272 graphies
romanisées sur 28 274, soit 99,993 %** ; seules **2** valeurs contiennent une
graphie japonaise **[M44]**. Le résultat porte donc sur la graphie utilisée par
le prédicat, pas sur la nationalité ni sur le statut de la personne.

---

## 4. Contrôle humain du socle automatique — 100 cas sur 7 434

**Aucun faux positif détecté sur 100 cas stratifiés [M4].** Le 21 juillet 2026,
avant les promotions LLM, 100 décisions ont été tirées sans remise parmi les
**7 434 décisions automatiques alors courantes**. L'échantillon était
volontairement **surpondéré en risques**, et non proportionnel : 25 cas
historiques parmi 2 677, 20 scores bas parmi 729, 15 cas du pont parmi 1 689 et
40 cas standard parmi 2 339. La graine `20260719` rend le tirage contrôlable.

L'arbitrage a été réalisé par l'humain **en aveugle** : colonnes du juge
masquées avant jugement, sources primaires uniquement et aucune IA dans la
boucle des verdicts. Les 100 rapprochements ont été confirmés. Cette mesure
satisfait le critère interne « ≥ 95 % sur l'échantillon », mais ne prouve pas
une précision de 100 % sur toute la population.

La grille et son protocole sont versionnés sous
`05_nettoyage_agregation_bdd/arbitrage/grille_c3_20260721T162953Z/`. Le script
recompte directement ses lignes et ses libellés. Les **980** décisions
`llm_review`, promues le 24 juillet, sont postérieures au tirage et ne sont donc
pas directement couvertes par ce 100/100.

**Le juge LLM corrige le socle dans les deux sens** — la valeur de l'étage R
n'est pas de trancher la file, c'est d'attraper les erreurs des étages
« sûrs » :
- *faux positif attrapé* : la série 1428 « Sister » était reliée à tort par
  le pont à « Chocotto Sister » — deux œuvres, auteurs différents. Corrigée en
  `human_review`/`rejected`, la décision fautive **conservée** dans le journal
  (append-only), l'identité remise à NULL, le couple fautif exclu des futures
  re-passes.
- *faux négatif récupéré* : Lupin III recalé par le socle pour « auteurs
  discordants » — Kazuhiko Katō *est* Monkey Punch, un pseudonyme ; même
  cas pour Electric Hands (Taishi Zaō = Mikiyo Tsuda). Le disambiguateur SQL
  ne connaît que les chaînes ; le juge apporte une connaissance du monde
  (pseudonymes, romanisations) qu'aucune table ne contient.

**Discipline d'écriture du run 2** (première écriture décisionnelle du juge) :
sauvegarde complète avant écriture, promotion des seuls `same_work` en
confiance **haute** (les moyennes réservées, les dossiers incomplets exclus),
par strates traçables (**67** du seau adjacent + **272**
pseudonymes/romanisations + **641** autres = **980**, **[M3]**), collisions
d'unicité jamais résolues par ordre d'arrivée (**56** exclues vers la file
humaine, **[M7]**).

---

## 5. Ce que sont vraiment les 36 % orphelines (le point à ne pas mal lire)

Une série est orpheline quand **aucun étage n'a trouvé le moindre candidat**,
même flou. Ce n'est pas un échec du matching : c'est que **l'œuvre n'existe
pas dans les référentiels externes**. Chaque source a son propre catalogue,
constitué indépendamment : Manga Sanctuary (français, riche en niche) répertorie
des titres que Kitsu (international, orienté populaire) et Wikidata (œuvres
notables) n'ont jamais recensés. On ne relie pas une série à une entrée qui
n'existe pas en face.

**Image** : trois annuaires — ta ville (MS), la France (Kitsu), le monde
(Wikidata). Tu pars des habitants de ta ville, tous dans l'annuaire local par
définition ; tu cherches lesquels figurent *aussi* dans les annuaires national
et mondial. Les gens connus y sont, ton voisin discret n'y est pas — pas parce
qu'il n'existe pas, mais parce que ces annuaires ne l'ont jamais recensé. Les
5 304 orphelines sont ces voisins discrets : bien réels, bien dans MS, absents
des grands référentiels.

**Ces 5 304 sont donc une donnée, pas un trou** : c'est le **gisement chiffré
de la v2**. Le jour où une source japonaise (MADB) est ajoutée, ce sont
précisément ces séries-là qu'elle ira nourrir — et on sait déjà lesquelles.
C'est aussi ce qui fonde le positionnement professionnel de l'outil : le
libraire n'a pas besoin d'aide sur One Piece, il en a besoin sur le titre de
niche invisible en France (cf. Butterfly Beast, documenté seulement en italien,
rencontré dans l'échantillon aléatoire).

---

## 6. Couverture par identifiant (pour l'enrichissement à venir)

Le rattachement ne se limite pas au QID Wikidata : une identité peut être
partielle et légitime (un identifiant MyAnimeList/AniList sûr sans entrée
Wikidata — l'« étage 0bis » réalisé par le pont, les colonnes de
`work_identity` étant indépendantes).

Confrontation de la couverture du pivot **[M6]** :

| Mesure | Attendu au 2026-07-21 | Mesuré au 2026-08-26 | Écart |
|---|---:|---:|---:|
| Œuvres du pivot Wikidata | 8 214 | 8 214 | 0 |
| Sitelink japonais | 57,9 % (4 755) | 57,9 % (4 755) | 0 |
| Sitelink anglais | 44,6 % (3 664) | 44,6 % (3 664) | 0 |
| Sitelink français | 22,0 % (1 803) | 22,0 % (1 803) | 0 |
| Japonais seulement | 1 664 | 1 664 | 0 |

**« Japonais seulement »** signifie : sur le dénominateur des **8 214** lignes
de `manga.wd_pivot`, `wiki_ja` contient un sitelink non vide, tandis que
`wiki_fr` et `wiki_en` sont tous deux vides ou NULL. Ce sont donc **1 664
œuvres ayant un article japonais et aucun article français ou anglais**. La
définition et les trois prédicats sont livrés dans la requête **[M6]** ; ce
chiffre quantifie directement le différenciateur d'enrichissement japonais.

Les identifiants actuellement portés par `work_identity` sont également
recomptés **[M6]** :

| Identifiant non NULL | Séries |
|---|---:|
| Wikidata QID | 3 136 |
| Kitsu | 7 028 |
| MyAnimeList | 7 978 |
| AniList | 8 051 |

Le référentiel d'identité est désormais lisible par
`GET /identity/{work_uid}` : un tiers peut confronter une œuvre exposée aux
identifiants dont dérivent les mesures du rapport.

L'enrichissement des genres après le §38 est lui aussi rejoué **[M38]** :

| Mesure genres | Séries | Part du catalogue |
|---|---:|---:|
| `series_genres_enriched` non vide | 12 952 | 88,3 % |
| Aucun genre dans MS ni Kitsu | 1 694 | 11,5 % |

Ces deux populations ne sont pas strictement complémentaires : la première
mesure la valeur enrichie, la seconde l'absence dans les deux sources.

> Décision produit tenue (2026-07-21) : l'enrichissement reste **fr / en / ja**
> pour l'instant ; l'échelon multilingue élargi (ex. italien via les sitelinks
> déjà présents dans les entités dumpées) est consigné comme option de phase 3,
> motivé par le cas Butterfly Beast — non ouvert maintenant.

---

## 7. Ce qui reste — régime de croisière, pas dette bloquante

La file humaine résiduelle et les avis de confiance moyenne ne bloquent rien :
ils se videront au fil des cycles mensuels. La mesure actuelle ne présume pas
leur stabilité ; elle les confronte à l'arrêté initial **[M7]** :

| Population | Attendu au 2026-07-21 | Mesuré au 2026-08-26 | Écart | Traitement |
|---|---:|---:|---:|---|
| `undecidable` (juge s'est abstenu) | 53 | 53 | 0 | arbitrage humain, au fil de l'eau |
| Conflits multi-candidats (« lequel ? ») | 54 | 54 | 0 | arbitrage humain |
| Fusibles (même œuvre via 2 sources) | 6 | 6 | 0 | promotion différée |
| Collisions d'unicité | 56 | 56 | 0 | arbitrage humain |
| Avis `same_work` **moyenne** non promus | 516 | 516 | 0 | décision de politique ultérieure |

Le régime de croisière mensuel ne fera passer au juge que les *nouveaux*
douteux de chaque cycle — quelques dizaines de dossiers, quelques centimes.
L'humain reste sur les exceptions, jamais sur les files : c'est la doctrine
de l'outil, appliquée d'abord à son propre pipeline.

---

## 8. En une phrase

*Sur les 14 670 séries du catalogue, 57,3 % sont reliées automatiquement et
sans erreur détectée au savoir mondial sur ces œuvres ; 6,5 % attendent un
arbitrage léger ; et les 36,2 % restantes sont, de façon mesurée et assumée,
absentes des référentiels occidentaux — la frontière exacte de ce que ces
sources permettent, et le gisement identifié de la prochaine version.*
