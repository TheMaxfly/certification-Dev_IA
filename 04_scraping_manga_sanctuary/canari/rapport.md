# Canari Manga Sanctuary — rapport

> **Date** : 2026-07-14 · **Module** : `04_scraping_manga_sanctuary`
> **Référence** : `data/raw/2025-12/manga_sanctuary_volumes.jsonl` (89 188 volumes,
> 13 211 séries, 38 champs) et `manga_sanctuary_reviews.jsonl` (6 749 critiques).
> **Objet** : valider les sélecteurs du spider avant re-crawl complet.
>
> **Statut au 2026-07-14 (mise à jour)** : le diagnostic ci-dessous a d'abord été
> produit **sans toucher au spider**. Les correctifs 5.1, 5.2, 5.4, 5.5 ont
> ensuite été **appliqués et vérifiés** (§8) ; la variante 5.3 reste **non
> appliquée**, en attente d'arbitrage. Aucun commit.

---

## 1. Méthode

25 séries tirées du dump de référence (grain volume → grain série par
déduplication sur `series_id`), en 4 strates disjointes, graine fixe `20260714`
(`canari/01_echantillon.py`, rejouable) :

| Strate | n | Intention |
|---|---:|---|
| `populaire` | 8 | fiches riches (rang ≤ 150 ; rang 1 = Death Note) |
| `alias_natif` | 8 | `series_other_titles` contenant kana/kanji — enjeu cascade d'identité |
| `sans_alias` | 5 | absence d'alias : distinguer « champ vide » de « sélecteur cassé » |
| `synopsis` | 4 | synopsis rempli en référence |

Les 25 fiches ont été re-téléchargées puis passées **au vrai `parse_series` du
spider en production**, importé et non réimplémenté (`canari/02_rescrape.py`) :
le canari teste le code réel, pas une copie susceptible de diverger.

**Politesse** : User-Agent du projet (`manga-sanctuary-scraper/0.1`), 2 s entre
requêtes, aucune concurrence, arrêt net programmé sur 403/429/503. Total ~48
requêtes. **Résultat : 48× HTTP 200, aucun blocage, aucun challenge anti-bot.**

> **Point robots.txt à connaître.** Le fichier autorise `/bdd/` et
> `fiche_serie_critique.php` pour `User-agent: *`, mais contient aussi une
> directive nominative `User-agent: Claude-Web → Disallow: /`. Le scraper du
> projet, sous son propre UA, relève du groupe `*` : la collecte est autorisée.
> La directive vise le crawler d'indexation d'Anthropic, pas ce module.

---

## 2. Résultat principal : aucun sélecteur n'a cassé en 7 mois

Comparaison champ par champ des 25 séries (`canari/03_comparer.py`) :

| Champ | STABLE | **CASSÉ** | ÉVOL. | GAIN | vide 2× |
|---|---:|---:|---:|---:|---:|
| `series_title` | 25 | **0** | 0 | 0 | 0 |
| `series_other_titles` | 19 | **0** | 0 | 0 | 6 |
| `series_year` | 25 | **0** | 0 | 0 | 0 |
| `series_dessinateur` | 23 | **0** | 0 | 0 | 2 |
| `series_scenariste` | 23 | **0** | 0 | 0 | 2 |
| `series_synopsis` | 18 | **0** | 1 | 1 | 5 |
| `series_genres` | 0 | **0** | 0 | 0 | **25** |
| `series_tags` | 0 | **0** | 0 | 0 | **25** |
| `series_statuses` | 22 | **0** | 0 | 0 | 3 |
| `series_type` / `series_category` | 25 / 25 | **0** | 0 | 0 | 0 |
| `series_mag_prepub` | 20 | **0** | 0 | 0 | 5 |
| `series_popularity_rank` | 3 | **0** | **22** | 0 | 0 |
| `series_members_rating` | 15 | **0** | 0 | 1 | 9 |
| `series_members_votes` | 19 | **0** | 6 | 0 | 0 |
| `series_experts_rating` | 12 | **0** | 4 | 0 | 9 |
| `series_experts_votes` | 20 | **0** | 5 | 0 | 0 |
| `series_related_works` | 7 | **0** | 3 | 0 | 15 |

**SÉLECTEUR CASSÉ : 0 champ, 0 série.** La structure du site n'a pas bougé
depuis la collecte de 2025-12.

**ÉVOLUTION DE DONNÉE** (attendu, non bloquant) : `series_popularity_rank` bouge
sur 22/25 séries (c'est un classement vivant), les votes s'accumulent
(`members_votes` 6/25, `experts_votes` 5/25), 1 synopsis réécrit, 1 synopsis
ajouté, 3 listes d'œuvres liées enrichies.

> ⚠️ **Limite de lecture à ne pas escamoter.** `series_genres` et `series_tags`
> ressortent en « vide 2× » : vides avant **et** après. Une comparaison
> avant/après est structurellement **incapable de détecter un bug présent depuis
> l'origine** — elle ne voit qu'une stabilité. C'est l'inspection du HTML
> (§3, §4) qui tranche. Les quatre anomalies ci-dessous sont **pré-existantes,
> pas des régressions**.

---

## 3. Quatre anomalies pré-existantes, confirmées sur pièces

### 3.1 🔴 `review_body` — 52,8 % des critiques perdent leur texte (impact maximal)

**Mesuré sur la référence** : 3 562 / 6 749 critiques (**52,8 %**) ont un
`review_body` vide, dont **3 558 portent pourtant un score**.

**Verdict : hypothèse (a) — le texte est sur la page, le sélecteur le rate.**

Sur 5 pages à corps vide, le conteneur ciblé `post post-single text-justify`
**existe** (5/5) et la page porte 3 800 à 5 600 caractères de texte. Mais :

```html
<!-- fiche_serie_critique.php?id=29576 — conteneur trouvé, 0 balise <p> -->
<div class="post post-single text-justify"> Luno était un manga plutôt attendu
car écrit et dessiné par Kei TOUME qui a son lot de fans grâce à des oeuvres
comme les lamentations de l'agneau ou encore Sing yesterday for me.<br> <br>
En tout cas, de prime abord, ce one shot a de quoi nous séduire.<br> ...
```

Le texte est un **enfant texte direct du `<div>`, séparé par des `<br>`** :
`nb <p> dans le conteneur = 0`. Or le sélecteur exige `//p//text()` :

```python
"//div[contains(@class,'post-single') and contains(@class,'text-justify')]//p//text()"
```

**Deux structures coexistent** : les critiques récentes utilisent des `<p>`
(5-6 par page → le sélecteur actuel fonctionne), les anciennes des `<br>`
(→ perte totale). Ce n'est donc pas une casse récente mais une couverture
partielle depuis l'origine.

**Ampleur mesurée** (15 critiques à corps vide, tirage systématique) :

| Indicateur | Valeur |
|---|---|
| Corps récupérés en retirant l'exigence `<p>` | **15/15 = 100 %** |
| Longueur médiane du texte récupéré | **1 591 c** (min 625, max 6 035) |
| Projection | **3 562 critiques récupérables** |
| Corpus reviews | **3 187 → ~6 749** (≈ +112 %) |

**Non-régression vérifiée** : sur 3 critiques qui *ont déjà* un corps, le
sélecteur proposé rend un **sur-ensemble strict** contenant l'intégralité de
l'actuel (2 567 → 2 586 c ; 2 572 → 2 620 c ; 4 349 → 4 395 c — le surplus est
le chapô situé hors `<p>`). **Il ne casse rien.**

### 3.2 🔴 `series_genres` / `series_tags` — vides à 100 %, et c'est bien le scraper

**Le site expose les genres.** Sur les 25 fiches : **22/25 en ont** (0 → 51
genres), **7/25 ont des tags** (0 → 42).

```html
<li> <span>Genres</span> <span>
     <a href="/bdd/genre/221/action.html">action</a>
     <a href="/bdd/genre/222/aventure.html">aventure</a> </span> </li>
<li> <span>Tags</span> <span>
     <div ...><a tag-id="78" href="#">#super pouvoirs</a></div> </span> </li>
```

**Cause exacte** : le sélecteur se termine par `parent::p//a`. Le parent des
`<a>` est un `<span>` (genres) ou un `<div>` (tags) — **jamais un `<p>`**.
L'XPath ne peut donc rien matcher, ce qui explique le 0,0 % *exact* sur 89 188
lignes : une donnée réellement absente produirait un taux résiduel non nul.

**Conséquence de conception à acter.** L'ETAT §3 justifie l'enrichissement
Kitsu par « Manga Sanctuary seul était insuffisant — tags/genres souvent
vides ». Le canari **invalide cette prémisse pour les genres** (~88 % de
couverture native VF, vocabulaire de 20 genres distincts sur 25 séries :
`fantastique`, `comédie`, `romance`, `action`, `Suspense`…) et **la confirme
pour les tags** (7/25 ≈ 28 %, concentrés sur les séries populaires : Akira 15
tags, Berserk 12, Rosario 6). À corriger dans l'ETAT.

### 3.3 🟠 `series_other_titles` — un seul alias retenu sur plusieurs affichés

**Signature dans la référence** : distribution `{0 alias: 3 755 séries,
1 alias: 9 456 séries}`. **Aucune série n'a jamais 2 alias.** Une moyenne de
1,00 sans aucune variance n'est pas une donnée, c'est une troncature.

**Confirmé sur pièces** : les alias suivants occupent des `<li>` **frères dont
le `<span>` de label est vide** :

```html
<!-- Parmi Eux - Hanakimi : 4 alias affichés, 1 seul collecté -->
<li><span>Autres titres</span><span><img alt="drapeau Japon">花ざかりの君たちへ</span></li>
<li><span></span><span><img alt="drapeau Japon">Hanazakari no Kimitachi e</span></li>
<li><span></span><span><img alt="drapeau États-unis">Hana-Kimi</span></li>
<li><span></span><span><img alt="drapeau Allemagne">Hana-Kimi - For you in full blossom</span></li>
```

```html
<!-- Fruits Basket : le natif est jeté, le romaji conservé -->
<li><span>Autres titres</span><span><img alt="drapeau Japon">Furutsu Basuketto </span></li>
<li><span></span><span><img alt="drapeau Japon">フルーツバスケット </span></li>
```

`following::span[1]` ne prend que le **premier** span de valeur ; l'ordre
d'affichage étant arbitraire, le spider garde **le premier venu**.

**Ampleur mesurée** : **19 → 28 alias** (+47 %), **6/25 séries tronquées** (24 %).

**Nuance importante, contre-intuitive.** La troncature ne détruit pas
principalement le japonais : le sélecteur actuel retient déjà une forme native
dans **17/19 cas (89 %)**. Ce qu'il perd, c'est surtout le **romaji, l'anglais
et le coréen** (`Rosario to Vampire`, `Hana-Kimi`, `로자리오와 뱀파이어`) — soit
7 des 9 formes récupérées. **C'est une bonne nouvelle pour la cascade** : le
pivot Wikidata est à **54,2 % de formes `en`** contre 10,6 % `fr`, donc les
alias récupérés tombent précisément dans son vivier dominant.

**Bonus non anticipé** : chaque alias porte **son drapeau de langue**
(`alt="drapeau Japon|États-unis|Corée|Allemagne"`). La langue de chaque alias
est donc collectable, alors que le pivot Wikidata est **indexé par langue**.
Cela permettrait de comparer `ja` contre `ja` et `en` contre `en` au lieu d'un
sac de chaînes. Proposé en **variante** au §5.3 car cela change le type du
champ JSONB `manga.ms_series_enriched.series_other_titles`.

### 3.4 🟠 EAN-13 — présent sur les fiches tome, jamais collecté

`items.py` ne déclare **aucun** champ `ean`/`isbn` ; la référence en contient 0.
Or la fiche tome l'affiche :

```html
<li><span>Editeur</span><span><a href="bdd/editeurs/40-ki-oon.html">Ki-oon </a></span></li>
<li><span>Collection</span><span>shonen</span></li>
<li><span>EAN-13</span><span>9782355929489</span></li>
<li><span>Prix</span><span>6,60 EUR</span></li>
```

**Mesuré sur 5 fiches tome** : **3/5 affichent un EAN-13**, et **3/3 passent la
clé de contrôle EAN-13**. Aucun `ISBN`, aucune balise `<meta>`, aucun JSON-LD
porteur d'ISBN : le `<li><span>EAN-13</span>` est **la seule source**.

⚠️ **À corriger dans le plan (ETAT §12).** La jointure volume↔volume avec Manga
Insight y est qualifiée d'« acquise ». Elle est **possible mais partielle** :
côté MI 79,9 % d'EAN, côté MS **~50-60 % seulement** (3/5 ici, 4/8 sur un
sondage indépendant — échantillons petits, à confirmer sur le crawl complet).
Le recouvrement réel sera **le produit des deux couvertures**, pas 79,9 %.
Un repli (titre + éditeur + date) restera nécessaire pour la fraction sans EAN.

**Champs adjacents disponibles et non collectés** : `Collection`, `Prix`
(signalés, non proposés au diff — hors périmètre du canari).

---

## 4. Synthèse de l'impact

| Correctif | Effet mesuré | Portée |
|---|---|---|
| `review_body` sans exigence `<p>` | 3 187 → **~6 749** docs (+112 %) | corpus RAG |
| `series_genres` | 0 % → **~88 %** | taxonomie native VF ; prémisse Kitsu à réviser |
| `series_tags` | 0 % → **~28 %** | tags (prémisse « souvent vides » confirmée) |
| `series_other_titles` | **+47 %** de formes | rappel de la cascade d'identité |
| `volume_ean` | 0 → **~50-60 %** | jointure Manga Insight (partielle) |

---

## 5. Correctifs (diffs)

> 5.1, 5.2, 5.4 et 5.5 sont **appliqués** ; 5.3 est une **variante non
> appliquée**. Vérification au §8.

### 5.1 `spiders/manga_sanctuary_volumes.py` — genres et tags

```diff
         # Genres
         series_genres = response.xpath(
-            "//text()[contains(., 'Genres')]/following::a[1]/parent::p//a/text()"
+            "//li[normalize-space(span[1])='Genres']/span[2]//a/text()"
         ).getall()
         series_genres = [g.strip() for g in series_genres if g.strip()]
 
         # Tags (si présents)
         series_tags = response.xpath(
-            "//text()[contains(., 'Tags')]/following::a[1]/parent::p//a/text()"
+            "//li[normalize-space(span[1])='Tags']/span[2]//a/text()"
         ).getall()
-        series_tags = [t.strip() for t in series_tags if t.strip()]
+        # Le « # » est décoratif dans le libellé du lien.
+        series_tags = [t.strip().lstrip("#") for t in series_tags if t.strip()]
```

### 5.2 `spiders/manga_sanctuary_volumes.py` — alias complets

```diff
         # ---------- Autres titres ----------
+        # Les alias suivants occupent des <li> frères dont le <span> de label est
+        # vide. Le test porte sur span[1] : une ligne de continuation a bien un
+        # span non vide, celui de la valeur. L'intersection Kayessian
+        # (count(A | B) = 1 <=> même noeud) borne la collecte au bloc alias en
+        # exigeant que la ligne étiquetée la plus proche en amont soit
+        # « Autres titres » — sans quoi la collecte déborderait sur « Type ».
+        ancre = "//li[normalize-space(span[1])='Autres titres']"
         other_titles = response.xpath(
-            "//text()[contains(., 'Autres titres')]/following::span[1]//text()"
+            f"{ancre}/span[2]//text()"
+            f" | {ancre}/following-sibling::li["
+            "not(normalize-space(span[1]))"
+            f" and count(preceding-sibling::li[normalize-space(span[1])][1]"
+            f" | {ancre}) = 1"
+            "]/span[2]//text()"
         ).getall()
         other_titles = [t.strip() for t in other_titles if t.strip()]
```

### 5.3 Variante (à trancher) — alias avec leur langue

Change le type du champ (`list[str]` → `list[dict]`) et donc le JSONB en base.
**À ne retenir que si l'étage 1 de la cascade sait exploiter la langue.**

```python
other_titles = []
for li in response.xpath(
    f"{ancre} | {ancre}/following-sibling::li["
    "not(normalize-space(span[1]))"
    f" and count(preceding-sibling::li[normalize-space(span[1])][1] | {ancre}) = 1]"
):
    titre = " ".join(t.strip() for t in li.xpath("span[2]//text()").getall() if t.strip())
    pays = li.xpath("span[2]//img/@alt").get(default="").replace("drapeau", "").strip()
    if titre:
        other_titles.append({"titre": titre, "pays": pays or None})
```

### 5.4 `items.py` + `parse_volume` — EAN-13

```diff
--- a/manga_sanctuary/manga_sanctuary/items.py
+++ b/manga_sanctuary/manga_sanctuary/items.py
     volume_editeur = scrapy.Field()
     volume_format = scrapy.Field()
     volume_pages = scrapy.Field()
+    # EAN-13 en CHAÎNE : clé de jointure Manga Insight. Ne jamais le typer en
+    # int (clé de contrôle et cadrage 13 chiffres à préserver) — donc à tenir
+    # hors de `int_fields` dans CleanAndTypePipeline.
+    volume_ean = scrapy.Field()
```

```diff
--- a/manga_sanctuary/manga_sanctuary/spiders/manga_sanctuary_volumes.py
+++ b/manga_sanctuary/manga_sanctuary/spiders/manga_sanctuary_volumes.py
         # Éditeur
         item["volume_editeur"] = response.xpath(
             "normalize-space(//text()[contains(., 'Editeur')]/following::a[1]/text())"
         ).get()
 
+        # EAN-13 : absent d'environ 40-50 % des fiches (mesure canari) ; seule
+        # source de la page — ni ISBN, ni <meta>, ni JSON-LD.
+        ean = response.xpath(
+            "//li[normalize-space(span[1])='EAN-13']/span[2]/text()"
+        ).get()
+        item["volume_ean"] = ean.strip() if ean else None
+
```

### 5.5 `parse_staff_review` — corps des critiques

```diff
         body_parts = response.xpath(
             "//div[contains(@class,'post-single') "
-            "and contains(@class,'text-justify')]//p//text()"
+            "and contains(@class,'text-justify')]//text()"
         ).getall()
         review["review_body"] = " ".join(p.strip() for p in body_parts if p.strip())
```

Exiger `//p` perdait les critiques anciennes, rédigées en texte + `<br>` sans
aucun `<p>` (52,8 % du corpus). Retirer l'exigence rend un sur-ensemble strict :
les critiques déjà collectées sont inchangées (vérifié sur 3 cas).

> **Réserve à arbitrer** : les `<br>` disparaissent au profit d'espaces, donc les
> sauts de paragraphe sont perdus. Sans impact sur la recherche vectorielle,
> mais si le chunking RAG doit respecter les paragraphes, remplacer les `<br>`
> par `\n` avant extraction plutôt que de joindre par espace.

---

### 5.6 `settings.py` — User-Agent identifiable (appliqué)

Sans cela, un crawl de ~89 k pages partirait sous l'UA Scrapy par défaut, alors
que le canari a validé `manga-sanctuary-scraper/0.1` en 48/48 HTTP 200.
Convention alignée sur le module 01 (surchargeable par variable d'environnement).

```diff
+import os
+
 BOT_NAME = "manga_sanctuary"
 ...
-# Crawl responsibly by identifying yourself (and your website) on the user-agent
-# USER_AGENT = "manga_sanctuary (+http://www.yourdomain.com)"
+USER_AGENT = os.getenv("MANGA_SANCTUARY_USER_AGENT", "manga-sanctuary-scraper/0.1")
```

Contrôlé par Scrapy lui-même : `scrapy settings --get USER_AGENT`
→ `manga-sanctuary-scraper/0.1`.

---

## 6. Conclusion : **GO conditionnel** pour le re-crawl complet

**Feu vert sur la faisabilité** — rien ne s'oppose techniquement au full crawl :

- **0 sélecteur cassé** sur 18 champs × 25 séries ; structure du site stable ;
- 48/48 requêtes en HTTP 200, aucun 403/429/503, aucun challenge anti-bot ;
- robots.txt autorise le périmètre visé pour l'UA du projet.

**Mais NO-GO en l'état**, car lancer maintenant re-collecterait ~89 k volumes en
reconduisant 4 anomalies connues et corrigées en quelques lignes. Le crawl coûte
trop cher pour être fait deux fois.

### Correctifs à appliquer avant le full crawl

| # | Correctif | § | Bloquant ? | État |
|---|---|---|---|---|
| 1 | `review_body` sans `<p>` | 5.5 | **Oui** — +112 % de corpus RAG | ✅ appliqué |
| 2 | `series_genres` / `series_tags` | 5.1 | **Oui** — 0 % → ~88 % | ✅ appliqué |
| 3 | `volume_ean` (+ `items.py`) | 5.4 | **Oui** — jointure Manga Insight | ✅ appliqué |
| 4 | `series_other_titles` complets | 5.2 | **Oui** — +47 % de rappel cascade | ✅ appliqué |
| 5 | `USER_AGENT` identifiable | 5.6 | **Oui** — posture de collecte | ✅ appliqué |
| 6 | Alias avec langue | 5.3 | Non — variante | ⏸ à trancher |

### Conditions d'exploitation restantes (non traitées)

Hors périmètre du canari mais bloquantes pour un crawl de ~89 k pages
(cf. brief §3.3) : **aucun `JOBDIR`**, donc aucune reprise possible sur
interruption ; **arrêt explicite sur 403/429/503** à câbler dans le spider (le
canari l'implémente dans ses propres scripts, pas dans Scrapy) ; sortie `FEEDS`
à harmoniser vers `data/raw/<AAAA-MM>/`. Ces trois points appellent un lanceur
dédié, sur le modèle de `scripts/run_scrape.py` du module 01.

> **Correction au brief §5** : `uv run scrapy list` ne fonctionne **pas** depuis
> `04_scraping_manga_sanctuary/` — `scrapy.cfg` vit dans `manga_sanctuary/`.
> La commande doit être lancée depuis `04_scraping_manga_sanctuary/manga_sanctuary/`.

### Recommandations de séquence

1. ~~Appliquer les correctifs 1-5~~ → **fait**, vérifié au §8. Reste à trancher 5.3.
2. **Tests unitaires** : `canari/07_verifier.py` verrouille déjà les correctifs
   sans réseau, mais s'appuie sur `canari/html/`, qui est gitignoré (données du
   site). Pour une suite pérenne façon module 01, rejouer les mêmes assertions
   sur des **fixtures HTML écrites à la main** reproduisant les deux structures
   de critiques, les alias multi-`<li>` et le bloc genres/tags.
3. **JOBDIR + arrêt sur blocage + `FEEDS` vers `data/raw/<AAAA-MM>/`** via un
   lanceur dédié, sur le modèle de `scripts/run_scrape.py` (module 01).
4. Lancer le full crawl.
5. **Conserver le dump 2025-12** comme référence de comparaison (réponse à la
   question §8 du brief : oui — c'est ce canari qui vient d'en démontrer la
   valeur ; aligner sur le module 01 : snapshot archivé + MANIFEST SHA-256).
6. Mettre à jour l'ETAT : §3 (prémisse genres invalidée, tags confirmée), §12
   (jointure EAN « acquise » → partielle), §9 (alias : le champ existait mais
   était tronqué).
7. Après crawl, **mesurer** les taux réels (genres, tags, EAN, alias/série) et
   les confronter aux projections de ce rapport.

---

## 7. Artefacts

| Fichier | Contenu |
|---|---|
| `canari/01_echantillon.py` → `reference.json` | échantillon stratifié, graine `20260714` |
| `canari/02_rescrape.py` → `scrape_2026-07.jsonl` | re-scrape via le vrai spider |
| `canari/03_comparer.py` → `comparaison.json` | comparaison champ par champ |
| `canari/04_alias_genres.py` → `analyse_html.json` | mesure actuel vs corrigé |
| `canari/05_ean.py` → `ean.json` | EAN + validation clé de contrôle |
| `canari/06_reviews.py` → `reviews.json` | diagnostic `review_body` |
| `canari/07_verifier.py` | vérification du spider corrigé, hors réseau |
| `canari/html/` | 35 pages HTML figées (série, tome, critique) |

Scripts conformes au standard du module (`ruff check` : *All checks passed!*).
Aucun commit effectué.

**Cloisonnement git** (dépôt public) : `.gitignore` a été complété par
`canari/html/` et `canari/*.json`. Seuls les **scripts** et **ce rapport**
restent versionnables ; toutes les données collectées (HTML du site, synopsis,
alias, corps de critiques) demeurent locales. Contrôlé par `git check-ignore`.

---

## 8. Vérification des correctifs appliqués

`canari/07_verifier.py` rejoue le **spider corrigé** sur le HTML déjà figé —
**aucune requête réseau**, rejouable à volonté — et confronte ses sorties aux
cibles mesurées avant correctif :

| Contrôle | Spider corrigé | Cible | Verdict |
|---|---:|---:|---|
| `series_genres` (25 fiches) | **51** | 51 | ✅ |
| `series_tags` (25 fiches) | **42** | 42 | ✅ |
| `series_other_titles` (25 fiches) | **28** | 28 | ✅ |
| `volume_ean` (5 tomes) | **3/5**, valeurs exactes | 3/5 | ✅ |
| `review_body` (5 critiques) | **5/5** non vides (835-2 451 c) | 5/5 | ✅ |

**Non-régression** : l'ancien et le nouveau spider ont été rejoués sur les
**mêmes 25 pages**, puis diffés champ par champ. Seuls **3 champs** changent —
`series_genres` (22 séries), `series_tags` (7), `series_other_titles` (6) —
c'est-à-dire exactement les champs visés, sur exactement les séries attendues.
**Aucune régression inattendue sur les 15 autres champs.**

Le spider charge toujours (`scrapy list` → `manga_sanctuary_volumes`) et Scrapy
rapporte `USER_AGENT = manga-sanctuary-scraper/0.1`.
