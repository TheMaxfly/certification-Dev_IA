# Démonstration de soutenance — API Manga (exécution locale)

Six gestes obligatoires, un facultatif. **3 à 4 minutes** dans un oral de 15.
Ce dossier ne contient pas un discours : il contient les **commandes exactes**,
éprouvées et chronométrées, et le repli si la machine lâche.

| Fichier | Rôle |
|---|---|
| `commandes.sh` | La séquence. `source` puis `g1`…`g7`. Une frappe par geste. |
| `titre.sh` | Le geste 6 : trouver une série **à partir de son titre**. |
| `repli_2026-09-08.txt` | Les sorties enregistrées. Si l'API ne démarre pas, la séquence reste montrable. |
| `.env` | Clé de démonstration + mot de passe de lecture. **Non versionné.** |

---

## Mise en route — 3 minutes avant d'entrer

```bash
cd ~/certification-Dev_IA/02_api_manga

# 1. Lancer l'API. La redirection `> /tmp/api-soutenance.log 2>&1` est
#    ESSENTIELLE : sans elle, uvicorn écrit une ligne de journal dans le
#    terminal à CHAQUE appel, en plein milieu de vos réponses.
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 \
  --env-file demo_soutenance/.env > /tmp/api-soutenance.log 2>&1 &

# 2. Charger la séquence (à refaire dans chaque nouveau terminal).
source demo_soutenance/commandes.sh

# 3. Contrôler.
verif        # doit afficher : db ok · clé de démo · manga_api · jq
```

**4. Ouvrir `http://127.0.0.1:8000/docs` dans un onglet du navigateur — et l'y
laisser.** C'est une étape de la mise en route, pas un geste de séance : rien de
la démonstration ne lance de navigateur. `g1` affiche ses trois comptages, ce
qui vous laisse le temps de basculer sur l'onglet déjà prêt (`Alt+Tab`).

Pour repartir d'une API propre : `pkill -f "uvicorn app.main"` puis relancer.
Si le port est déjà pris, c'est qu'une instance tourne : `verif` vous le dira.

`verif` est le seul contrôle à passer. S'il est vert, les sept gestes le sont.

**La clé affichée est jetable.** Elle s'appelle `demo-soutenance-…`, elle vit
dans ce `.env` seul, et elle peut rester à l'écran sans conséquence : la clé de
production n'a jamais été utilisée ici. Si le jury la voit, il n'a rien vu.

---

## Chaque geste montre sa commande

Depuis la version du 9 septembre, un geste **affiche la commande qu'il exécute**
avant de l'exécuter, sous un titre qui annonce l'objectif :

```
 GESTE 2 · LE REFUS, PUIS L'ACCÈS — même route, une seule différence
 Critère 4 de C5 : l'API restreint l'accès aux données

  ── SANS clé ──
  $ curl -si http://127.0.0.1:8000/rag/export/composition | head -1
HTTP/1.1 401 Unauthorized
```

**Ce qui est affiché est ce qui tourne** : la chaîne montrée est exactement celle
passée à `eval`, jamais une reformulation. Un écart entre les deux se verrait, et
ruinerait la preuve.

Deux conséquences sur la façon de parler :

- **Laissez le jury lire.** Après avoir tapé le geste, marquez une seconde avant
  de commenter. La commande est à l'écran, elle travaille pour vous.
- **`$CLE` reste affiché comme variable**, pas déplié en 48 caractères
  aléatoires : le nom dit mieux ce qui se passe, et la ligne reste lisible. La
  clé elle-même est visible via `verif` si on la demande.

## La séquence

### `g1` — Le contrat · 20 s
Affiche trois comptages — 13 routes, 11 sous cadenas, les 2 ouvertes nommées —
puis vous invite à basculer sur l'onglet `/docs`, **ouvert avant la séance**.

> Le geste ne lance aucun navigateur, volontairement. L'ouvrir en séance ferait
> un second onglet sur la même page si celui-ci est déjà prêt — un jury y lit un
> raté — et dépendrait d'un utilitaire qui peut ne trouver aucun navigateur
> selon l'environnement graphique. **Rien de la séquence ne dépend plus du
> poste graphique.**

> « La documentation n'est pas écrite à côté du code : elle est **servie par
> l'API elle-même**. Treize points de terminaison, chacun avec son résumé. Le
> cadenas est sur onze d'entre eux — les deux qui ne l'ont pas sont les sondes
> de supervision, `/health` et `/live`. »

*Critères 1, 2, 3 de C5.*

### `g2` — Le refus, puis l'accès · 20 s
Même route, deux fois, une seule différence : l'en-tête.

> « Sans clé : **401**. La même route, la même seconde, avec la clé : **200**.
> La seule différence entre les deux appels tient dans un en-tête. »

*Critère 4 : l'API restreint l'accès aux données.*

Si on demande pourquoi 401 et pas 403 : le refus est **identique** que la clé
soit absente ou fausse. Distinguer les deux dirait à un attaquant que sa clé a
été lue et rejetée — donc qu'elle a la bonne forme. Le journal, lui, distingue.

### `g3` — Le témoin de lecture seule · 30 s
Le rôle de l'API lit 14 670 séries, puis échoue à en modifier une.

> « L'accès SQL direct est le second moyen de mise à disposition. Voici le rôle
> dont l'API se sert : il lit. Il tente une écriture — **refusée**. Et ce n'est
> pas mon code qui refuse : c'est PostgreSQL, `SQLSTATE 42501`, dans
> `aclcheck_error`. Une garantie applicative se contourne ; un privilège
> retiré, non. »

> **Pourquoi l'écriture vise `series_id = -1`.** PostgreSQL vérifie le privilège
> **avant** d'évaluer la clause `WHERE` : le refus est identique, mot pour mot.
> Mais si l'écriture passait un jour — un `PGPASSFILE` qui résout autrement, une
> variable héritée —, aucune ligne existante ne serait touchée : le plus petit
> `series_id` du catalogue est **12**. Viser 11455 exposait la série des gestes
> 4 et 5 à s'appeler « piraté » deux gestes plus tard, devant le jury.

### `g4` — Le geste central · 40 s
`/series/11455` — JoJo's Bizarre Adventure : Jojolion.

À l'écran : `kitsu_id: null` · `work_uid: 19599` ·
`genres_source: fantastique, aventure` · `genres_enriched: surnaturel, aventure`.

> « Cette série n'est **pas appariée à Kitsu** : `kitsu_id` est nul. Or Kitsu
> est ma source d'enrichissement des genres — elle ne peut donc rien en
> recevoir. Et pourtant elle **porte ses genres** : ils viennent de la source
> française, normalisés contre un référentiel commun. En juin, la colonne
> dérivée ne voyait que les séries appariées à Kitsu : les autres sortaient
> **vides**. Elles sont **7 487**. Zéro avant, 7 487 après. »

> ⚠️ **Ne dites JAMAIS « appariée à aucun référentiel international ».**
> C'est faux et vérifiable en direct : `/identity/19599` montre pour Jojolion un
> `wikidata_qid` Q22343522, un `mal_id` et un `anilist_id`. Ce qui manque est
> l'appariement **Kitsu**, et c'est précisément ce qui compte, puisque Kitsu est
> la source d'enrichissement. Sur les 7 487 séries concernées, 1 094 ont un
> identifiant Wikidata.

C'est l'écran qui relie en une fois le défaut de juin, la péremption de la
colonne dérivée, le référentiel de genres et l'exposition.

### `g5` — Les deux normalisations · 30 s
Deux séries, deux opérations qu'on confond d'habitude.

| Série | Transformation | Opération |
|---|---|---|
| **746** Monster | `thriller`, `Suspense`, `policier` → `thriller` | **consolidation** |
| **11455** Jojolion | `fantastique` → `surnaturel` | **traduction non littérale** |

> « Monster portait trois libellés de la **même** source pour le même lectorat.
> Un seul code en sort : c'est de la **consolidation**.
>
> Jojolion, c'est autre chose. `fantastique` devient `surnaturel`.
> `fantastique` et `fantasy` se ressemblent et désignent deux genres
> **distincts** : le fantastique français, c'est l'irruption du surnaturel dans
> le réel — le `supernatural` anglais. Une correspondance par similarité de
> chaîne aurait rapproché `fantastique` de `Fantasy` et produit **l'inverse**.
> C'est pourquoi la table est arbitrée à la main, libellé par libellé. »

**`g5bis`** montre cette table si on le demande — et la note d'arbitrage entière.

> ⚠️ **Si le jury lit la réponse complète**, il verra
> `{"code": "surnaturel", "label_fr": "Fantastique"}` et pourra objecter que
> « rien n'a changé ». La réponse : *le libellé d'affichage français reste
> « Fantastique », et c'est normal — c'est le mot juste en français. Ce qui
> change, c'est le **code** : `surnaturel`, celui-là même que reçoit le
> `Supernatural` de Kitsu. Le code est l'identifiant partagé, le libellé est la
> langue. `fantasy` est un autre code, réservé à `Fantasy` et `Heroïc-Fantasy`.*

### `g6` — FACULTATIF · 40 s
**Offert, jamais imposé.** Si personne ne demande, passer à `g7`.

> « Si vous voulez choisir un titre vous-même, je peux le chercher. »

`g6 "One Piece"` — cherche l'identifiant par le titre (SQL en lecture seule),
puis sert la fiche par l'API.

**C'est le seul geste dont la sortie n'est pas connue d'avance.** Les quatre
réponses possibles sont préparées ; aucune ne met le système en défaut.

| Ce qui s'affiche | Ce qu'on dit |
|---|---|
| `kitsu_id` **renseigné**, genres présents | « Le cas nominal : les deux identifiants sont là, et vous voyez que les genres enrichis sont **plus nombreux** que ceux de la source — l'écart, c'est l'apport Kitsu. » |
| `kitsu_id: null`, genres présents | « Le cas de Jojolion, sur un autre titre. Non appariée **à Kitsu**, et elle porte quand même ses genres. C'est exactement ce que le correctif a débloqué. » |
| genres **vides** des deux côtés | « Celle-ci n'a de genre dans **aucune** source. Elle fait partie des **1 694** — et ce chiffre, `/coverage` le calcule à chaque appel. Une lacune mesurée n'est pas une lacune cachée. » |
| **aucun candidat** | « Le catalogue est celui du **marché français**, pas un catalogue mondial. Ce titre n'y est pas paru sous ce nom. Une absence est une frontière connue, pas une panne. » |

Titres éprouvés, un par cas : `One Piece` (736, Kitsu 38, 4 genres source →
8 enrichis) · `Akame ga Kill` (15101, non appariée, 4 genres) · `Moriarty`
(49246, aucun genre) · `Calvin and Hobbes` (absent).

Le titre saisi ne peut rien casser : il passe en **paramètre** psql, jamais
concaténé dans le SQL. `x'; DROP TABLE …` ressort comme un titre introuvable.

### `g7` — La couverture · 30 s
> « Et pour finir, ce que l'outil **ne sait pas**. 14 670 séries, 104 107
> volumes, 11 074 critiques. Mais aussi : **1 694 séries sans aucun genre**, et
> **5 304 sans identité rapprochée**. Ces deux chiffres sont **calculés à
> l'appel**, jamais écrits en dur — un chiffre figé dans le code cesse d'être
> vrai au premier recalcul, sans que personne s'en aperçoive. C'était le défaut
> de juin. »

---

## Chronométrage

Deux passages, le premier **API arrêtée au départ** (le démarrage compte), plus
une mesure de contrôle. Ce qui est mesuré ici est le **temps machine** : le
temps que le poste met à répondre, hors parole.

| | Passage 1 (à froid) | Passage 2 (à chaud) | Contrôle |
|---|---:|---:|---:|
| Démarrage de l'API | 0,66 s | — | — |
| `g1` contrat (3 appels) | 0,10 s | 0,03 s | 0,03 s |
| `g2` 401/200 (4 appels) | 0,80 s | 0,83 s | 0,83 s |
| `g3` lecture seule | 0,13 s | 0,13 s | 0,13 s |
| `g4` /series/11455 | 0,01 s | 0,01 s | 0,01 s |
| `g5` normalisations | 0,02 s | 0,02 s | 0,02 s |
| `g6` par titre | 0,09 s | 0,08 s | 0,08 s |
| `g7` couverture (2 appels) | 0,09 s | 0,09 s | 0,08 s |
| **Total machine** | **1,92 s** | **1,21 s** | **1,19 s** |

**La machine n'est pas le facteur limitant** — elle consomme moins de deux
secondes sur toute la séquence, démarrage compris. La durée réelle de la
démonstration est donc **celle de la parole**, et le seul temps qui ne se
recouvre pas avec elle est le démarrage à froid : **0,7 s**. Négligeable, mais
l'API est à lancer avant d'entrer, pas devant le jury.

Budget de parole, geste par geste :

| Périmètre | Budget |
|---|---:|
| Noyau obligatoire (`g1`+`g2`+`g3`+`g4`+`g5`+`g7`) | **2 min 50 s** |
| Avec le geste 6 facultatif | **3 min 30 s** |
| Avec `g5bis` en plus, si le jury creuse | **≈ 4 min** |

**Le noyau obligatoire tient sous les trois minutes : aucune coupe n'est
nécessaire.** La marge est dans le geste 6, qu'on n'ouvre que si on est dans
les temps. Si `g5bis` est demandé *et* que le geste 6 a déjà été joué, on
touche les quatre minutes : dans ce cas, écourter `g7` au seul chiffre de
1 694 suffit à revenir dans la fenêtre.

⚠️ Ces budgets sont ceux qui étaient **visés** par geste, pas une mesure de
diction. À valider en répétant à voix haute, chronomètre en main — c'est la
seule mesure qui manque, et je ne peux pas la produire à votre place.

---

## Le jour J, si ça tourne mal

| Symptôme | Geste |
|---|---|
| `verif` dit `INJOIGNABLE` | Relancer l'uvicorn ; 0,7 s. |
| `verif` dit `db` autre que `ok` | PostgreSQL est tombé : `pg_isready`. Les gestes 1 et 2 (401) fonctionnent quand même. |
| Rien ne démarre | Ouvrir `repli_2026-09-08.txt` : la séquence entière y est, sorties comprises. |
| Le jury creuse la sécurité | `logs` affiche le journal : le refus y figure, **la clé jamais**. |

L'onglet `/docs` **ouvert avant d'entrer** fait partie de la mise en route
(étape 4). Depuis que `g1` ne lance plus de navigateur, la séquence ne dépend
plus du tout de l'environnement graphique : elle tient entièrement dans le
terminal, l'onglet ne servant qu'à montrer la page.
