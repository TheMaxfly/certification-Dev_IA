# anilist — reconnaissance — 20260911T221442Z

Statut : VALIDE

> Reconnaissance close le 2026-09-12. **La collecte reste impossible** : l'API
> est coupee. Le point B a recu sa decision (ci-dessous) ; les points C et D
> restent ouverts et devront etre etablis a la reouverture, **avant** que la
> liste des champs et la table des roles ne soient figees.

## 1. Environnement declare

| | |
|---|---|
| point d'acces | https://graphql.anilist.co |
| etat de l'API | **HTTP 403 — desactivee**, toutes requetes confondues |
| message rendu | The AniList API has been temporarily disabled due to severe stability issues. |
| site anilist.co | HTTP 200 — le site repond, seule l'API est coupee |
| cadence prevue | 2 s entre requetes (30/min), conforme a la doc |
| base | apimanga, lecture seule |

## 2. Perimetre

| | |
|---|---|
| pont 1 — kitsu_mappings (anilist/manga) | 33 631 lignes, 33 630 kitsu_id |
| series MS par kitsu_mappings | 4 958 |
| series MS par work_identity.anilist_id | 8 051 |
| PERIMETRE RETENU — union des deux ponts | **8 441** (57,5 % du catalogue) |
| defaut releve | le referentiel n'est PAS un sur-ensemble : 390 series ne sont atteintes que par kitsu_mappings |

## 3. Resultats

| | |
|---|---|
| POINT A — l'hypothese du pont | **CONFIRMEE** |
| A.3 — AniList elargit-il ? | **OUI, massivement** : 6 990 series hors du perimetre Kitsu personnages (1 474), soit 4,7x |
|   AniList INTER Kitsu-personnages | 1 451 — AniList couvre 98,4 % du perimetre Kitsu |
|   couverture des trois sources reunies | 8 468 series — 57,7 % du catalogue, contre 10,0 % avec Kitsu seul |
| POINT B — conditions d'utilisation | **ETABLI, ET BLOQUANT — voir ecarts** |
| POINT C — balisage spoiler | **NON ETABLISSABLE** — introspection impossible, API coupee |
| POINT D — vocabulaire des roles | **NON ETABLISSABLE** — meme motif |
| Reconnaissance sur 30 oeuvres (spec 9) | **NON LANCEE** — aucune requete ne passe |
| Cadence documentee | 30/min confirme : « The API is currently in a degraded state and is limited to 30 requests per minute ». La decision 2 de la spec etait juste. |

## 4. Ecarts aux attendus

- **L'API est coupee.** 403 sur `{ __typename }` comme sur toute requete. Ce n'est ni l'agent, ni la requete, ni une limite de cadence.
- **Les conditions d'utilisation interdisent explicitement ce que la spec propose.** Verbatim : « Hoarding or mass collection of data from the AniList API is strictly prohibited. »
- Tempere par : « For purely educational projects, such as school assignments, we tend to be very lenient on the 3rd point. »
- Clause distincte, non levee par la precedente : « Use of the AniList API within competing, non-complementary services of the same nature is prohibited... The restriction applies to all data provided through the API, including both user data and media data. »
- « Using the AniList API as a backup or data storage service is strictly prohibited » — un raw NDJSON archive tombe dans cette description.
- Commercial : gratuit sous 150 $ de revenu mensuel, licence au-dela. Le but aval inscrit au journal (integrer le chat a un site marchand) n'est pas un projet purement educatif.
- Le pont `malId` des personnages n'a pas pu etre verifie au schema : la correction demandee par la spec reste ouverte.

## 5. Ce que je n'ai pas pu etablir

- Existence et taux de remplissage de `name.alternativeSpoiler` — point C.1.
- Syntaxe et fiabilite du balisage spoiler dans les descriptions — points C.2 et C.3.
- Valeurs de l'enum `CharacterRole` et table de correspondance avec le vocabulaire Kitsu — point D.
- Presence d'un `idMal` sur le type `Character` : le type `Media` en expose un, rien ne prouve que `Character` en fasse autant. A verifier par introspection des la reouverture, avant toute hypothese de fusion.
- Taux de remplissage de `description`, `name.native` et `favourites`, qui decident de l'apport reel de la source.
- Recouvrement personnage a personnage avec le raw Kitsu — mesurable seulement sur donnees collectees.

## 6. Decision sur le point B — 2026-09-12

**Retenu : collecter au titre de l'usage educatif**, en invoquant la clemence que
les conditions accordent au point 3, et en bornant le perimetre.

**Motif.** Le projet est une certification. Les conditions ecrivent : « For purely
educational projects, such as school assignments, we tend to be very lenient on
the 3rd point. »

**Risque assume, ecrit pour ne pas etre redecouvert.** Trois reserves ne sont pas
levees par cette clemence, et la decision les accepte en connaissance :

1. la clemence est **discretionnaire** — « we tend to be » n'est pas un droit ;
2. la clause **« competing, non-complementary services of the same nature »** vise
   toutes les donnees media, et l'objectif aval inscrit au journal (§39.6),
   integrer le chat a un site marchand, sort du cadre educatif ;
3. **« Using the AniList API as a backup or data storage service is strictly
   prohibited »** : un raw NDJSON immuable correspond a cette description.

**Consequence portee au registre C4.** L'usage est educatif et borne *aujourd'hui*.
Tout passage a un usage marchand rouvre le point B et exige une licence
commerciale au-dela de 150 $ de revenu mensuel, ou une autorisation ecrite
demandee a contact@anilist.co.

### Dimensionnement — ce que « borne » veut dire chiffre en main

Base de projection : 12,8 personnages par oeuvre cote Kitsu, pagination AniList
a 25 par page, soit ~1,3 requete par oeuvre. Cadence imposee : 30/min.

| Perimetre | Oeuvres | Requetes | Duree a 30/min |
|---|---:|---:|---:|
| Tout le perimetre | 8 441 | ~11 000 | **~6 h** |
| Top 2 000 par popularite | 2 000 | ~2 600 | ~1 h 25 |
| Top 1 000 par popularite | 1 000 | ~1 300 | ~45 min |

Six heures de sollicitation continue se defendent mal sous une clause interdisant
la collecte de masse. **Le regroupement par alias GraphQL** — plusieurs oeuvres
par requete — divise le nombre d'appels sans changer le volume de donnees, et
menage leur infrastructure : c'est la forme a retenir a la reouverture.
