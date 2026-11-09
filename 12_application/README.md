# 12 — Application du libraire

Une application web minimale, rendue par le serveur, qui consomme l'API du modèle
(`11_api_modele/`) par sa clé : le libraire pose une question, l'application appelle
`POST /v1/reponse` et affiche la réponse ou le refus, les séries citées, leurs métadonnées
et les passages cités.

## Ce qu'elle fait, et ce qu'elle ne fait pas

- **Elle fait** : une page question → réponse ; une conversation de cinq échanges au plus ;
  pour les libraires et l'administrateur, le choix du profil, deux réglages et le mode de
  réponse ; pour l'administrateur seul, un mode démonstration, éteint par défaut, qui
  montre les scores et la trace de la recherche ; le public (visiteur, client) n'a aucun
  réglage, en mode strict (E4, lot 2b) ; un écran nommé par cas dégradé ;
  la mention « Vous parlez à une IA » sur chaque écran ; un audit d'accessibilité outillé.
- **Elle ne fait pas** : aucune écriture en fichier ; aucun mode autre que le strict pour
  le public ; aucune bascule automatique de profil : le repli reste un geste humain.
- **E4, lot 1 (le socle)** : une base à elle (schéma `application`, migration `027`, rôle
  `app_rw` seul) ; mots de passe hachés par Argon2id ; sessions signées, le compte relu en
  base à chaque requête ; jeton CSRF sur chaque formulaire `POST` ; verrou après des
  échecs ; garde par rôle ; garde d'environnement. Sans base configurée, ou base
  injoignable, le conseiller fonctionne comme avant.

C'est le tronc de l'application de l'épreuve E4 : même module, même pile. Le dossier
`schema/` décrit son modèle de données, le schéma `application` de la base (migration
`027`, E4, lot 1) : `schema/README.md`.

## Architecture

```text
navigateur → application (127.0.0.1:8020) → API du modèle (X-API-Key de l'application)
                                             ├→ service d'embedding
                                             ├→ base, sous modele_ro
                                             └→ fournisseur du profil
```

Ce que l'application ne voit jamais : la base, les clés des fournisseurs, la liste des
pseudonymes (l'API contrôle ses sorties ; l'application n'affiche que ce qu'elle rend, et
aucun chemin ne la contourne). Un passage reste, lui, tel qu'il est au corpus : voir « Le mode
démonstration ».

**Pilotée par le contrat** : profils disponibles, bornes des réglages, modes et version
sont lus dans `/v1/info`, relu à chaque page. Le sélecteur de mode n'apparaît que si l'API
en déclare plus d'un. La doublure de ses tests est validée contre
`11_api_modele/contrat/openapi_v1.json`, requêtes comme réponses.

**Pile** : FastAPI et Jinja2, sans JavaScript. Une feuille de style sans classes (Pico CSS,
vendue avec son empreinte) et `theme.css`, des variables seules : l'habillage se change là.

## Ensembles fonctionnels → écran

Les ensembles fonctionnels de l'expression de besoin, et ce qui les sert. Les mesures, par
famille de questions, sont au README de l'API du modèle.

| Ensemble | Le libraire attend | Écran ou comportement |
|---|---|---|
| EF1 | Reconnaître une œuvre désignée | Le champ question, transmis tel quel ; la carte de la série citée, son titre du catalogue, l'ancre depuis le texte |
| EF2 | Tolérer la forme de la demande | Le même champ : aucune correction ni filtre côté application |
| EF3 | Proposer à partir d'une description | Le même champ ; les suites de conversation, reformulation réglable |
| EF4 | Proposer à partir de critères | **En partie** : question libre, et chaque carte montre public, tomes, statut, auteurs. **Le filtre, et « du même auteur » : épreuve E4** (il demanderait un champ saisi par le libraire et une version du contrat) ; la fausse parenté est déjà empêchée, l'auteur étant sur la ligne de titre |
| EF5 | Ne pas inventer | Le refus est une réponse, avec son motif en français et sans carte ; seules les séries contrôlées par l'API sont montrées ; la mention IA |
| EF6 | Rédiger le conseil | La zone de réponse, les passages cités et leur type, la ligne discrète, les détails repliés |
| EF7 | Dire la disponibilité | « Disponibilité » sur chaque carte, et ce qu'elle veut dire : au moins un volume paru, pas un stock |

**Hors de ces ensembles, épreuve E4** : comptes, rôles, page de série, critique de libraire,
administrateur.

## Les écrans

Un seul écran, `/`, dans l'ordre de lecture :

1. le lien d'évitement ; le nom de la librairie, fictive ; la mention courte ; le bouton
   **Nouvelle conversation**, sur chaque écran : un formulaire `GET /` sans champ, donc sans
   script, et l'historique ne le suit pas ;
2. l'état du service : le profil actif et son modèle, ou « ne répond pas » ;
3. la conversation : les tours précédents, sans leurs marques `[S:…]` ;
4. le formulaire : la question ; les **Réglages**, repliés, pour un libraire ou
   l'administrateur seulement (« Le conseiller selon le rôle ») ; le champ caché de
   l'historique ;
5. la réponse ou le refus ; la ligne discrète (profil, fournisseur, modèle, date de l'appel,
   reformulation, coût estimé, latence) ; une carte par série citée, ses métadonnées et ses
   seuls passages cités ; les détails techniques, repliés ;
6. le pied de page : la mention complète, « À propos et données », les versions.

`/a-propos` : ce qui est transmis, tous les profils proposés et leur hébergement, ce qui
n'est pas conservé. `/sante` : du JSON, toujours 200 : l'application sert sans l'API ; il
porte `api`, `base` et `catalogue` (`ok`, `indisponible`, `non_configure` : un `GET /live`
du catalogue, sans clé, délai court), et `cles_api` (`une`, `deux` : la clé des comptes
posée ou non, E4, lot 2b).
`/metrics` : Prometheus, sur le bouclage, ou le réseau de collecte déclaré
(`APPLICATION_METRIQUES_RESEAUX`). `application_requetes_total{route, statut}` : `route` est
le gabarit de la route, `statique` pour les feuilles de style, `inconnue` pour le reste.
`application_api_erreurs_total{code}` : une liste fermée, `corps_inattendu` hors d'elle.
**Deux jauges pour l'état de l'API** : `application_api_info_etat{etat}` (`ok`, `ko`, `cle` : la
clé est refusée), lue au démarrage et à chaque page ; `application_api_sante_ok`, lue au
démarrage et à chaque `/sante`. Aucune ne se relit seule : une panne de l'API se lit sur la
cible de Prometheus. **Le catalogue** (E4, lot 2a) :
`application_catalogue_appels_total{route_catalogue, statut}` (routes `series`, `serie` ;
issues `ok`, `introuvable`, `indisponible`, `cle`, `corps`) et la jauge
`application_catalogue_etat{etat}` (`ok`, `indisponible`, `non_configure`, `cle`), posée à
chaque appel.

### Le conseiller selon le rôle (E4, lot 2b)

| Rôle | Réglages (profil, température, reformulation) | Case « mode démonstration » | Liste des modes | Mode envoyé |
|---|---|---|---|---|
| visiteur, client | **absents du HTML** | absente | absente | `strict`, le défaut du contrat |
| libraire | présents, repliés | absente | si `/v1/info` déclare plus d'un mode ; `strict` choisi | celui de la liste ; hors liste, le défaut |
| administrateur | présents | **présente** | même règle | idem |

- **Un champ forgé est ignoré, pas refusé** : pour le public, `profil`, `temperature`,
  `reformuler`, `mode` et `mode_demonstration` n'existent pas ; un `POST` qui les porte part
  avec les défauts (profil par défaut, température par défaut, reformulation par défaut de
  l'API, `strict`) — 200, jamais 422. Un libraire n'a pas le mode démonstration.
- **Le complément du mode enrichi** n'est rendu que pour un rôle qui choisit le mode ; son
  étiquette est `complement_etiquette` de l'API (contrat 1.4.0), le texte du 12 si elle
  manque (`[textes] complement_etiquette`).
- **Le public** lit, sous le titre, « Le conseiller répond à partir des critiques des
  libraires du réseau » ; la mention IA reste sur chaque écran.
- **Le pont, dans les deux sens** : la fiche d'une série porte « Demander au conseiller »
  (`/?serie=<id>`), qui pré-remplit la question avec le **titre exact lu dans le 02**
  (« Que pensent les libraires de *titre* ? », `[textes] question_pre_remplie`) — jamais un
  texte de l'adresse ; un identifiant illisible, une série inconnue, un catalogue absent ou
  en panne : la page sans pré-remplissage, sans erreur. Une réponse qui **refuse** ou ne
  **cite aucune série** porte « Chercher ce titre dans le catalogue »
  (`/catalogue?q=<question coupée à 100 caractères>`, absent sous 2 caractères).

Tests : `tests/test_conseiller_roles.py` (un test par critère de V1, A7, L6, fin de V3) ;
mutations `E4Q-01` (réglages servis au public), `E4Q-02` (case servie à un libraire),
`E4Q-03` (`mode=enrichi` forgé par un visiteur accepté).

### Les bornes du conseiller (E4, lot 2b)

Le conseiller public appelle un modèle payant : quatre bornes le tiennent, toutes en
configuration (`[conseiller.limites]`), vérifiées **avant** l'appel, dans cet ordre, et un
refus n'appelle jamais l'API.

Les chiffres des deux quotas sont ceux de la décision de Max du 2026-11-09 : **5 questions par
jour pour un visiteur, 20 pour un client**. Ils remplacent 10 par heure et 10 par jour, qui
donnaient à un visiteur plus de questions sur une journée qu'à un compte. Les deux se
comptent par jour civil UTC et se remettent à zéro à minuit UTC.

| Borne | Qui | Valeur, et son repère | Mécanisme | Dépassement |
|---|---|---|---|---|
| Quota par session | visiteurs | **5 questions / jour civil UTC** — de quoi essayer le conseiller | les horodatages des questions du jour dans le cookie signé (`quota`), purgés et **bornés** à 5 à chaque lecture (un cookie forgé ne fait rien enfler) ; compté après une réponse 200 seulement | 429, `Retry-After` jusqu'à minuit UTC, « Vous avez posé vos 5 questions du jour ; le conseiller vous revient demain à HH:MM (fuseau du serveur). Un compte gratuit en donne 20 par jour. » ; la question reste au formulaire |
| Quota par compte client | clients connectés (ni libraires, ni administrateur) | **20 questions / jour civil UTC** — un compte donne plus de questions qu'aucun compte | `application.questions_jour` (migration `029`), relu avant, incrémenté après une réponse 200 ; il **suit le compte**, pas le cookie | 429, `Retry-After` jusqu'à minuit UTC, « … vos 20 questions du jour ; le conseiller vous revient demain à HH:MM » |
| Plafond de débit | les appels partis avec la **clé publique** | **10 / minute**, fenêtre glissante — la marge de 5 / min laissée aux comptes sous le plafond Mistral de 15 / min | en mémoire du processus : **valable avec un processus** ; plusieurs demanderaient un compteur partagé | 429, `Retry-After`, « Le conseiller est très sollicité ; réessayez dans une minute » |
| Plafond de coût du jour | tout le monde, strict et enrichi | **0,50 $ / jour** — environ 250 questions au coût mesuré | `application.couts_jour` (027) : la somme du jour, relue avant chaque appel ; après chaque réponse, le `cout_estime_usd` reçu (0 s'il manque, journalisé `cout_absent`) sous le profil et le mode **rendus par l'API** ; base indisponible : le plafond n'est **pas vérifiable**, le conseiller répond quand même (`plafond_non_verifiable`, état `inconnu`) | 503, `Retry-After` jusqu'à minuit UTC, « Le conseiller a atteint son budget du jour ; il reprend demain » |

- **Les deux clés** (décision 5) : `APPLICATION_CLE_API` (publique, visiteurs) et
  `APPLICATION_CLE_API_COMPTES` (facultative : client, libraire, administrateur), prises
  **par appel** par le même client HTTP. Sans clé des comptes, la publique sert aux deux
  (`cle_comptes_absente` au démarrage, `/sante` : `"cles_api": "une"`, sinon `"deux"`) :
  les comptes passent alors sous le plafond de débit des visiteurs — la protection de D7
  (une rafale de visiteurs ne bloque pas un libraire) suppose la seconde clé, posée au
  trousseau de l'API au lot de livraison continue.
- **L'inscription bornée** : 3 comptes par heure et par session (`inscriptions` dans le
  cookie, même mécanisme, gardé à l'ouverture et à la fermeture d'une session) ; au-delà,
  429, `Retry-After`, un encart au formulaire, aucune ligne en base.
- **Limites assumées** : un cookie effacé remet à zéro le quota de session et le compteur
  d'inscriptions (le quota d'un compte, lui, suit le compte) ; deux questions simultanées
  peuvent passer ensemble le plafond de coût (relu, pas réservé) ; une réponse en erreur de
  l'API (503) ne porte pas son coût : il n'est pas compté.
- **Métriques** : `application_questions_total{mode, issue}` (`reponse`, `refus`, `quota`,
  `debit`, `plafond_cout`, `api_erreur`, `api_indisponible`),
  `application_quota_refus_total{motif}` (`session`, `client`, `debit`, `cout`,
  `inscription`), `application_cout_jour_usd` (lu avant chaque question),
  `application_plafond_cout_etat{etat}` (`ok`, `atteint`, `inconnu`) ; la latence du
  conseiller est `application_latence_secondes{route="/question"}` (histogramme existant,
  borné). Toutes déclarées à zéro dès le démarrage.

Tests : `tests/test_bornes.py` (horloge injectée : la fenêtre glisse, minuit passe, sans
attendre) ; mutations `E4Q-04` (quota de session jamais incrémenté), `E4Q-05` (plafond de
débit retiré), `E4Q-06` (coût non relu avant l'appel), `E4Q-07` (clé des comptes retirée),
`E4Q-08` (inscription sans borne), `E4Q-12` (quota du compte client jamais relu).

### Les réglages

Pour un libraire ou l'administrateur (« Le conseiller selon le rôle »).

| Réglage | Valeurs | Défaut |
|---|---|---|
| Profil | les profils disponibles de `/v1/info`, sauf `mistral-large-4` : en aperçu, à remesurer. Chaque option dit son fournisseur et son hébergement | le **profil actif de l'application**, choisi par l'administration (E4, lot 5) ; sans réglage, ou réglé sur un profil qui n'est plus proposé : le profil actif de l'API ; exclu ou indisponible : le premier proposé |
| Température | 0 « stable », seule valeur mesurée ; 0,3 « modérée », candidate pour la production, **à mesurer avant de devenir défaut** ; 0,7 « variée », pour la démonstration | 0 |
| Reformulation | cochée ou non ; sans effet à la première question | cochée |
| Mode démonstration | administrateur seul ; coché ou non ; ne change que ce que la page affiche, voir plus bas | décoché |
| Mode | affiché seulement si l'API en déclare plus d'un | `strict` |

Le reste (`longueur_max`, `nombre_series`, `passages_par_serie`, `catalogue_seul`) reste aux
défauts de l'API.

### Le mode démonstration

Une case des réglages, **à l'administrateur seul** (E4, lot 2b, A7), **décochée par
défaut** (`[reglages] mode_demonstration_defaut = false`). Portée par le formulaire comme les autres réglages, elle suit la conversation ;
« Nouvelle conversation » la remet au défaut. Ni variable d'environnement, ni cookie à
elle (le seul cookie est celui de la session, « Sécurité et données »).

**Ce qu'il montre**, coché, tout venant de la même réponse de `/v1/reponse` :

- sur chaque carte, le rang et le score de la série (« rang 3 — score 0,52 ») ; sous chaque
  passage cité, son score, à côté de son identifiant ; une infobulle dit ce qu'est le score ;
- une section **Trace de la recherche**, après les cartes : le classement entier (rang,
  titre, score, *citée* ou *non citée*, nombre de passages), les passages non cités, repliés
  par série, la question d'origine, sa reformulation et celle qui a cherché, les réglages de
  la recherche, le modèle (profil, fournisseur, température, longueur, jetons par appel, coût,
  latences, identifiant de requête). Le bloc « Détails techniques » y est absorbé.

Un score nul ou absent s'écrit « — », jamais une erreur.

**Pourquoi il est éteint par défaut** : c'est la production. Le libraire lit une réponse et
ses sources ; le classement et ce que le modèle n'a pas retenu servent à montrer comment la
recherche travaille, pas à conseiller un lecteur. Décoché, rien de cela n'atteint le gabarit :
ni score, ni rang, ni passage non cité, ni section (tests sur chaque écran, et mutations).

**Ce que le score est, et n'est pas** : le cosinus entre la question et le meilleur fragment
d'une série, tel que l'API le rend. Il mesure une ressemblance de textes. Ce n'est ni une note
de la série, ni une preuve que la réponse est vraie : la recherche propose, le modèle choisit
parmi ces séries. La page le dit en tête de la section. L'API refuse qu'il cite une série hors
de la recherche ; un titre seulement nommé dans son texte, sans carte, n'est pas contrôlé
(« Limites déclarées »).

**Les passages non cités** : hors de ce mode, aucun texte n'apparaît au-delà des passages
cités, et cette règle ne change pas. Ce mode en est la seule exception, décidée :
**les passages ne portent aucun pseudonyme autre que les mentions connues du corpus v2
(décision D2)**. Le texte d'une critique y reste tel qu'il a été écrit, et 20 mentions d'un
membre, sur 19 critiques, sont connues et listées. `make verify-poste` le relit sur les
passages rendus pour le jeu d'évaluation (904 passages distincts, 0 hors des listes, 2026-11-03)
; l'API ne relit pas les extraits à chaque réponse : ses contrôles portent sur le texte du
modèle et sur la question reformulée. Une page en mode démonstration se relit donc avant toute
capture. Le masquage de ces mentions à l'affichage est prévu pour l'épreuve E4.

**Aucun effet en amont** : la requête envoyée à l'API est la même, coché ou non, et le contrat
refuserait un champ inconnu. Aucune donnée nouvelle ne sort : le tableau OWASP ne change pas.

**Limite** : l'infobulle n'est pas atteignable au clavier ; la phrase en tête de la section
dit la même chose, en texte.

### La conversation

L'historique vit **dans la page**, dans un champ caché : le serveur n'a aucune mémoire, il
n'y a rien à purger, et fermer l'onglet efface tout. Il est revalidé à chaque question,
selon les règles de l'API. La conversation est close à **cinq échanges**, ou si elle ne
tiendrait plus les bornes de l'API (4 000 caractères, 2 000 par tour), la réponse restant
affichée : jamais de coupe silencieuse des tours anciens.

### Les cas dégradés

Chaque cas a son encart, annoncé au lecteur d'écran ; la question et l'historique sont
conservés, sauf mention.

| Déclencheur | Ce que voit le libraire |
|---|---|
| API injoignable | Le service de recommandation ne répond pas. |
| Délai dépassé | Le même, « délai dépassé » ; l'appel peut continuer côté API |
| 429 `debit_depasse` | Trop de demandes. Réessayez dans N secondes (`Retry-After`, sinon 60) |
| 503 `fournisseur_refus`, statut 429 | Le modèle de langage est saturé. Réessayez dans N secondes (`Retry-After`, sinon 2) |
| 503 `fournisseur_injoignable`, `fournisseur_delai`, `fournisseur_refus` autre statut | Le modèle de langage ne répond pas (profil X). L'exploitant peut changer de profil |
| 503 `fournisseur_refus`, motif `longueur_atteinte` | Le modèle de langage n'a pas terminé sa réponse (profil X) |
| 503 `sortie_modele_invalide` | La réponse du modèle a été refusée par nos contrôles (motif, en français) ; jamais la sortie |
| 503 `service_embedding_*`, `base_indisponible`, `encodage_en_service_change` | La recherche est indisponible ; vous pouvez réessayer |
| 422 `profil_indisponible`, `profil_inconnu` | Le profil X n'est pas disponible ; la liste est rechargée, choisissez-en un autre |
| 422 `reglage_non_expose` | Le profil X n'accepte pas le réglage Y ; changez de profil ou de réglage |
| 422 autre, 401, 413, 5xx imprévu, corps hors contrat | Erreur du service ; réessayer, ou donner l'identifiant à l'exploitant |
| Historique invalide | Cette conversation n'est plus valide ; formulaire vide, rien n'est envoyé |

Un refus (`200`, `refus` vrai) n'est pas un cas dégradé : c'est une réponse.

**Après un 429, le bouton reste actif** : sans JavaScript, un bouton désactivé ne se
réactive jamais. Le nombre de secondes est affiché parce qu'un clic trop tôt n'est pas
toujours gratuit : le 429 de l'API l'est, mais avec un historique la reformulation passe
avant l'appel de réponse et reste facturée si celui-ci est refusé (0,0004 $ au plus, au
prix du profil actif, mesuré le 2026-11-02), et chaque clic consomme une requête sur les 15
par minute du compte (2026-11-03).

### Le catalogue (E4, lot 2a)

Deux pages publiques, servies par l'API data du module 02 (contrat 0.6.0 depuis le lot 2b ; un 02 en 0.5.0 reste lu, sans suggestions ni source du résumé), sous la clé
de l'application (`APPLICATION_CLE_CATALOGUE`, consommateur `conseiller`) ; le menu porte
« Catalogue » sur chaque page ; elles gardent le menu du compte connecté.

| Page | Ce qu'elle fait |
|---|---|
| `/catalogue` | la recherche (`<form role="search">`, un champ étiqueté) : titre, autres titres et auteurs, sans casse ni accents, au classement du catalogue (titre exact, début, contenu, auteur) ; chaque résultat mène à sa fiche et dit type, année, auteurs, note des lecteurs /10, disponibilité, et « trouvé par » quand c'est un autre titre ou un auteur ; 20 par page, page courante dite (`aria-current`) ; une saisie hors de 2 à 100 caractères est dite au champ, sans appel ; aucun résultat : un écran nommé ; quand le 02 rend des `suggestions` (contrat 0.6.0, la ressemblance de trigrammes au-dessus d'un seuil mesuré), **« Vouliez-vous dire… ? »** et un lien par fiche suggérée (non préchargée), puis le lien vers le conseiller ; sans suggestion, le renvoi au conseiller seul (E4, lot 2b) |
| `/series/{series_id}` | la fiche : la ligne de titre par `metadonnees_de`, **les mêmes valeurs que les cartes du conseiller** (auteurs, tomes, statut, public, disponibilité), plus type et année ; la note des lecteurs /10 et ses votes, précédée de celle des libraires du réseau ; « Les libraires du réseau » (leurs critiques, lot 3, ci-dessous ; état vide nommé) ; « Vos listes » (lot 4, « Les listes du client ») ; le résumé servi par le 02 (contrat 0.6.0 : coupé avant les critiques de lecteurs que la source y avait recopiées) et sa source — un résumé Kitsu dit « Résumé fourni par Kitsu (en anglais) », `lang="en"` ; le français principal, le résumé Kitsu replié dessous dans un `<details>` (`lang="en"`) ; sans résumé, « Résumé indisponible » — ; « Demander au conseiller » (`zone-pont-conseiller`) ; les genres, le lien vers le site du catalogue (dit externe, `noopener`) ; des zones nommées (`zone-titre`, `zone-note`, `zone-critiques-reseau`, `zone-listes`, `zone-synopsis`) pour l'habillage |

**Cas dégradés** : catalogue injoignable, en délai, qui refuse la clé, hors contrat, ou non
configuré (pas de clé, en développement) → l'encart « Le catalogue ne répond pas ; le
conseiller reste accessible », **503**, sur les deux pages ; une série inconnue ou un
identifiant hors forme → la page introuvable (404). L'application **n'appelle jamais**
`/series/{id}/reviews` : les critiques collectées portent des pseudonymes de lecteurs (la
doublure compte ses appels, zéro sur cette route).

**Ce que le lot 2a ne fait pas** : la tolérance aux fautes de frappe et « Vouliez-vous
dire ? », le pont de la fiche vers le conseiller (lot 2b) ; les listes (lot 4).

### Les critiques signées (E4, lot 3)

C'est l'argument du projet : un libraire publie une critique signée, notée sur 10, sur une
série ou sur un tome ; elle paraît **à l'instant** sur la fiche de la série, face à la note
des lecteurs. Pages rendues côté serveur, sans JavaScript ; chaque formulaire en `POST`,
avec son jeton ; `Cache-Control: no-store`.

| Page | Qui | Ce qu'elle fait |
|---|---|---|
| `/series/{series_id}` | tous | « Les libraires du réseau » : les critiques **publiées**, les plus récentes d'abord — le nom affiché du libraire (jamais son pseudo), la date, « Tome N » s'il y a lieu, la note « 8/10 » en texte, le badge du label, « modifiée le … », le texte en entier, en paragraphes, **échappé** ; dans la zone de la note, « Vos libraires : 8,5/10 (3 critiques) » **avant** « Note des lecteurs : … » ; un libraire connecté y voit « Écrire une critique » et « Modifier » sur les siennes, un administrateur « Retirer (modération) » sur chacune, les autres aucun de ces liens ; la base perdue : la fiche s'affiche avec l'encart « Les critiques du réseau sont momentanément indisponibles » (200) |
| `/libraire/critiques/nouvelle?series_id=…` | libraire | le formulaire : la série rappelée ; le tome, facultatif (« La série entière », puis les volumes lus dans le 02, libellés « Tome N — titre ») ; le texte, ses bornes affichées (50 à 2 000 caractères) ; la note de 0 à 10, obligatoire, aucune présélectionnée ; le label, facultatif, dans la liste fixe ; « vous en avez déjà une » avec le lien « Modifier » ; succès : **303 vers la fiche**, où la critique est déjà là, avec l'avis du délai d'intégration ; une saisie refusée : le formulaire, les valeurs saisies (texte compris), l'erreur au champ et le focus sur le premier champ fautif (422) ; série inconnue du catalogue : page nommée (404) ; catalogue injoignable : 503 nommé |
| `/libraire/critiques/{id}/modifier` | l'auteur | texte, note, label — la série et le tome affichés, non modifiables ; une retirée : page nommée (404) ; celle d'un autre : **403**, base inchangée |
| `/libraire/critiques/{id}/retirer` | l'auteur | une page de confirmation (rappel, « le retrait est définitif ») puis le `POST` ; celle d'un autre : **403** |
| `/libraire/critiques` | libraire | « Mes critiques » : la phrase du délai en tête ; toutes ses critiques, publiées et retirées (filtre sans JavaScript), 25 par page — titre de la série lu dans le 02 (« titre indisponible » et l'encart si le catalogue manque, la page reste à 200), tome ou « série entière », note, label, statut, dates, liens « Modifier » et « Retirer » sur les publiées ; état vide nommé, qui mène au catalogue |
| `/admin/critiques` | administrateur | toutes les critiques publiées, 25 par page, filtre par libraire (actifs et inactifs) : libraire, série, tome, note, label, date, début du texte (120 caractères, échappé), « Retirer » |
| `/admin/critiques/{id}/retirer` | administrateur | la critique **en entier** (l'administrateur lit avant de retirer), « le retrait est tracé au journal à votre nom », puis le `POST` |

**Les règles** (`config/reglages.toml`, `[critiques]` : modifiables sans code, validées au
démarrage, comparées à la migration 027 par un test) : texte de 50 à 2 000 caractères
(espaces des bords retirés, sauts de ligne normalisés, aucun caractère de contrôle hors du
saut de ligne ; gardé tel que saisi, jamais reformulé) ; note entière de 0 à 10,
obligatoire ; un label au plus, facultatif : Coup de cœur, Découverte du mois, À offrir,
Pour débuter, Valeur sûre. **Une critique publiée par libraire, série et tome** (l'index de
la base le garantit ; deux envois croisés rendent le même refus nommé, jamais une erreur
500) ; plusieurs tomes d'une même série sont possibles. La série est validée par le 02 ; le
tome doit être l'un de ses volumes, et son libellé est **copié** à l'écriture (l'éditeur ne
l'est pas : défaut de source connu du 02).

**Les droits** : un libraire écrit, modifie et retire **les siennes** ; un administrateur
retire **n'importe laquelle** et n'en écrit ni n'en modifie aucune — un administrateur qui
veut écrire se crée un compte libraire ; un client ou un visiteur n'a aucun bouton, et 403
ou la page de connexion sur toute route de critique. Le jeton d'abord, puis le rôle, puis
l'identifiant du chemin (hors forme : 404).

**Ce que fait un retrait** : le statut `retiree` et la date de modification, **jamais une
suppression** (le rôle `app_rw` n'a pas le droit de supprimer une critique) ; la critique
disparaît de tous les écrans publics et de la moyenne ; elle reste dans « Mes critiques »
avec son statut ; une nouvelle critique sur le même couple redevient possible. Retirer une
critique déjà retirée ne change rien et ne laisse aucune trace.

**Le journal** : chaque publication, modification, retrait écrit, **dans la même
transaction**, sa ligne au journal des actions (`critique_publiee`, `critique_modifiee`,
`critique_retiree`, au nom de qui agit, `details` : la série, s'il s'agit d'un tome, `par`
auteur ou administrateur) ; le journal applicatif dit l'événement et les identifiants.
**Jamais le texte d'une critique**, ni à l'un ni à l'autre (tests sur le journal complet et
sur la table). Métrique : `application_critiques_total{action}`.

**Le délai d'intégration** : « Votre critique est visible sur la fiche ; elle sera intégrée
aux recommandations du conseiller à la prochaine mise à jour du corpus » — après la
publication et en tête de « Mes critiques ». Les critiques collectées (celles des lecteurs)
ne sont toujours pas affichées : aucun appel à `/series/{id}/reviews`.

### Les listes du client (E4, lot 4)

Un compte **client** tient trois listes — **favori**, **à lire**, **à commander** —, un
état par série, le dernier geste gagne. Sans JavaScript : un formulaire `POST`, des
boutons ; `Cache-Control: no-store`.

| Page | Qui | Ce qu'elle fait |
|---|---|---|
| `/series/{series_id}`, zone « Vos listes » (`zone-listes`) | visiteur | « Connectez-vous pour tenir vos listes » (`/connexion?retour=/series/{id}`) et « Créer un compte » ; aucun bouton |
| | client | « Dans votre liste : à lire » ou « Cette série n'est dans aucune de vos listes » ; **un** formulaire, trois boutons (`etat`), l'état courant `aria-pressed="true"` et dit « (actuelle) », et « Retirer de mes listes » quand un état existe ; après le geste, l'avis en tête de la fiche |
| | libraire, administrateur | la zone **n'est pas rendue** (les listes sont celles des clients ; un libraire a ses critiques, l'administrateur ses outils) |
| `POST /listes/{series_id}` | client | `jeton`, `etat` (`favori`, `a_lire`, `a_commander`, `retirer`), `retour` (chemin local seul ; défaut : la fiche) ; **303 vers `retour`**, `?etat=` : `ajoutee`, `changee`, `inchangee` (« Déjà dans vos favoris »), `retiree`, `absente`, `pleine`, `indisponible` ; libraire, administrateur : **403** ; visiteur : 303 vers la connexion, retour compris ; un état hors liste : 422 nommé ; un identifiant hors de 1 … 2 147 483 647 : 404 |
| `/compte/listes` | client | « Mes listes » : trois sections (`zone-liste-favori`, `zone-liste-a_lire`, `zone-liste-a_commander`), « À lire (3) », la plus récente d'abord ; chaque entrée : le titre **lu dans le 02** (lien vers la fiche), la date d'ajout (`<time>`), « Retirer » ; état vide nommé par section ; base absente : 503 « comptes indisponibles » |
| carte du conseiller (`zone-carte-listes`) | client | sous chaque carte citée, « Dans votre liste : favori » s'il y a lieu et les trois boutons ; `retour` = la fiche de la série ; pas de retrait (la fiche et la page le font) ; **aucun formulaire dans le HTML d'un visiteur, d'un libraire ou d'un administrateur** |

- **La revalidation** : la série d'un **ajout** (ou d'un changement) est relue dans le 02
  avant l'écriture — inconnue : 404, rien d'écrit ; catalogue injoignable ou non
  configuré : rien d'écrit, l'avis « le catalogue ne répond pas ». Un **retrait** ne la
  demande pas : une série que le 02 ne connaît plus doit pouvoir sortir d'une liste.
- **La borne** : `[comptes] listes_max_par_etat` (**50**) ; une 51e série, ou un changement
  vers une liste pleine, est refusé (`?etat=pleine`, avis nommé), vérifié dans la même
  transaction que l'écriture, la ligne du compte tenue : deux gestes simultanés ne la
  dépassent pas. Pas de pagination : la borne suffit.
- **Les titres en parallèle** : la page demande au 02 les fiches de ses entrées ensemble
  (`asyncio.gather`, 150 au plus) ; une série inconnue : « Titre indisponible », l'entrée
  et son lien restent ; le catalogue muet : toutes en « Titre indisponible » et l'encart
  « Le catalogue ne répond pas ; vos listes sont intactes ». Le titre n'est jamais gardé
  en base (`application.listes` : compte, série, état, date).
- **Rien n'est tracé au journal des actions** : une liste est la donnée du client, pas un
  contenu publié ; retirer supprime la ligne. Métrique : `application_listes_total{action}`
  (`ajout`, `changement`, `retrait` ; un geste sans effet n'est pas compté).
- **Limite (CL4)** : le geste depuis une carte mène à la **fiche** de la série, où l'état et
  l'avis se lisent : la réponse du conseiller n'est pas conservée par un `POST` sans
  JavaScript (« rien n'est gardé des conversations »).

### L'effacement du compte (E4, lot 4)

**Le droit à l'effacement** : un client supprime son compte lui-même, à tout moment,
physiquement et immédiatement.

| Page | Qui | Ce qu'elle fait |
|---|---|---|
| `/compte/mot-de-passe`, en bas (`zone-effacement`) | client | le lien « Supprimer mon compte » — pas dans le menu : un geste irréversible n'est pas à un clic du menu |
| `/compte/supprimer` | client | la page de confirmation : ce qui sera supprimé (le compte, le pseudo, l'empreinte du mot de passe, les listes, les compteurs de questions), ce qui reste (une ligne de journal sans identité), « irréversible » ; le mot de passe (`autocomplete="current-password"`), « Supprimer définitivement mon compte », « Annuler » |
| `POST /compte/supprimer` | client | le mot de passe vérifié par **`comptes.authentifier`**, le chemin même de la connexion — même coût, même compteur d'échecs, même verrou : un mauvais mot de passe compte comme un échec de connexion ; refus : la page, 422, « Mot de passe incorrect. », rien d'écrit ; succès : **303 vers l'accueil**, l'avis « Votre compte a été supprimé », la session fermée |
| `/compte/supprimer` | libraire, administrateur | **403** et la phrase : « Les comptes du réseau sont désactivés par l'administrateur ; ils ne se suppriment pas, leurs critiques signées doivent rester attribuables » (l'asymétrie est voulue) |

- **Une transaction** : la ligne du compte tenue (`FOR UPDATE`), la ligne `compte_supprime`
  au journal des actions (par le compte, sur le compte), puis `DELETE FROM
  application.comptes` — la cascade emporte `listes` et `questions_jour` ; la contrainte
  met `par_compte_id` à `NULL` : il reste « un compte n° n a été supprimé, par lui-même », et
  ce numéro ne nomme plus personne. Supprimé entre-temps par une autre session : même
  sortie, rien au journal. Un compte qui a des critiques signées (un libraire, jamais un
  client) : la base refuse (`ON DELETE RESTRICT`), la page « Ce compte ne peut pas être
  supprimé » (409).
- **Après** : un cookie copié avant ne vaut plus rien (le compte n'existe plus) ; **le
  pseudo redevient libre** ; le compteur des inscriptions de la session, lui, survit (un
  compte supprimé n'est pas une inscription de plus). Métrique :
  `application_comptes_supprimes_total`.

### L'administration (E4, lot 5)

Toutes les pages `/admin/*` exigent le rôle **administrateur** : un autre rôle reçoit 403,
un visiteur la page de connexion (`GET`) ou 401 (`POST`) ; chaque `POST` porte son jeton.
Elles partagent une **barre d'onglets** (`nav.onglets`, `aria-label` « Administration »),
dans cet ordre : Comptes · Modération · Réglages · Clés · Coûts · Services. La barre est
construite à partir des **routes enregistrées** (`pages_admin.ONGLETS`, filtrée) : un onglet
n'apparaît que si sa page existe ; la page courante porte `aria-current="page"`. Le menu
principal n'a qu'une entrée, « Administration », vers `/admin/comptes`.

**Le profil IA actif** (`/admin/reglages`, story A2, décision D12) :

- **où il est gardé** : la clé `profil_actif` de la table `application.reglages` (027), une
  ligne, sa valeur, son auteur (`modifie_par`) et sa date. Un réglage se **remplace**, il ne
  s'efface pas (`app_rw` n'a pas `DELETE`) ; les clés écrites forment une liste fermée dans
  le code (`reglages_app.CLES`), jamais une clé venue d'un formulaire ;
- **ce que la page propose** : les profils de `/v1/info` **disponibles et non exclus**
  (fournisseur, hébergement, modèle, statut `stable` / `aperçu`), un bouton radio chacun ;
  l'actif coché — sans réglage, le défaut, dit « défaut de l'application » ; qui l'a choisi
  (nom affiché) et quand ; les indisponibles **à part, sans bouton** ; les exclus **comptés,
  jamais nommés** (l'exclusion est une décision du dépôt, `[reglages] profils_exclus`).
  Le choix est **validé contre cette liste** avant toute écriture (hors liste : 422, rien
  d'écrit) ; l'API injoignable : un encart et la valeur enregistrée, sans formulaire ;
- **quand il est lu** : **à chaque question** (une lecture par requête, aucun cache au-delà),
  et à chaque page du conseiller ; il part avec **toute** question — celle du public, qui n'a
  pas de réglages, et, présélectionné, celle d'un libraire ou de l'administration, qui
  **gardent leur liste** : le choix par question reste un geste de démonstration (lot 2b) et
  ne modifie jamais le réglage. L'état du service, la page « à propos » et `/sante`
  (`profil_actif: <nom> | defaut`) nomment le profil actif de l'application ;
- **s'il devient indisponible** (l'API le dit à `/v1/info`) : la question part avec le
  **défaut de l'API** ; un encart nommé paraît sur `/admin/reglages` et sur la page du
  conseiller **d'une session administrateur seulement** ; l'événement
  `profil_actif_indisponible` entre au journal applicatif ; le réglage **n'est pas effacé** :
  il s'applique de nouveau quand le profil revient. Base injoignable pendant une question :
  le défaut, l'événement `profil_actif_non_lu`, et le conseiller répond (décision 11) ;
- **le journal** : chaque changement écrit, dans la même transaction, une ligne
  `profil_actif_change` (`objet_type = 'reglage'`, `details = {"profil": "<nom>"}` — un nom
  de profil, ni texte libre ni secret) ; la même valeur qu'avant n'écrit rien ;
- **la métrique** : `application_profil_actif_info{profil}`, 1 sur le profil actif, 0 sur les
  autres profils de `/v1/info`, rafraîchie à chaque lecture.

### Les comptes (E4, lot 1)

Pages rendues côté serveur, sans JavaScript ; chaque formulaire en `POST`, avec son jeton.

| Page | Qui | Ce qu'elle fait |
|---|---|---|
| `/inscription` | visiteur | pseudo, mot de passe, confirmation ; les règles affichées ; « ce que nous gardons » et le renvoi vers « à propos » ; succès : compte client, session ouverte, retour à la page d'origine (chemin local seul) |
| `/connexion` | visiteur | un seul refus, « Pseudo ou mot de passe incorrect », quel que soit le cas (inconnu, mauvais mot de passe, verrouillé, désactivé), même statut (401), suivi d'une phrase fixe, la même dans tous les cas : le seuil et la durée de la suspension (lus aux réglages), et qu'un compte désactivé s'adresse à l'administrateur — les règles, rien sur le compte ; succès : retour à la page demandée, ou au changement d'un mot de passe initial |
| `/deconnexion` (`POST`) | connecté | la génération + 1 (toute copie du cookie tombe, et les autres sessions du compte avec), la session vidée ; retour à l'accueil |
| `/compte/mot-de-passe` | connecté | ancien, nouveau, confirmation ; succès : génération + 1 (les autres sessions tombent), la session courante rouverte, le mot de passe initial levé, une ligne au journal des actions ; la page ne montre plus le formulaire mais un encart « Mot de passe changé et enregistré » et un lien « Continuer » ; un mauvais ancien compte un échec, sous le même compteur et le même verrou que la connexion |
| `/admin/comptes` | administrateur | la liste (pseudo, nom affiché, rôle, état, date), filtrée par rôle, par pages de 25 (une page hors bornes rend la plus proche) ; créer un compte libraire ou administrateur (un rôle hors liste est refusé, nommé), au mot de passe initial saisi deux fois, à changer ; désactiver (ses sessions tombent) ou réactiver un compte — jamais le sien ; un changement sans effet ne laisse aucune trace ; aucune suppression |
| `/libraire/critiques` | libraire | « Mes critiques » (E4, lot 3, ci-dessous) |
| `/admin/critiques` | administrateur | la modération des critiques (E4, lot 3, ci-dessous) |

**Le menu**, dans chaque page (`<nav>`), pages d'erreur comprises : visiteur — Conseiller,
Connexion, Créer un compte (ces deux liens ramènent à la page affichée) ;
client — Conseiller, Mes listes (E4, lot 4), Mon compte, son nom, Déconnexion (un bouton de formulaire) ; libraire —
en plus, Mes critiques ; administrateur — en plus, **une seule entrée « Administration »**
(`/admin/comptes`, E4, lot 5 : elle remplace « Comptes » et « Critiques » ; les pages de
l'administration ont leur barre d'onglets ; pas « Mes critiques » : il n'écrit pas, E4, lot 3). Sans base, chaque page de compte
dit « Les comptes sont momentanément indisponibles ; le conseiller reste accessible » (503).

**Le premier administrateur**, par une commande : `make app-admin-creer`, avec
`APPLICATION_ENVIRONNEMENT` et `APPLICATION_BASE_DSN` (sans mot de passe) dans
l'environnement. Elle applique la garde d'environnement, demande le pseudo, le nom affiché,
puis le mot de passe deux fois, sans écho (`getpass`) — sans terminal, ou si le terminal
ne coupe pas l'écho, elle refuse (code 2) ; elle crée le compte `admin`, sans mot de passe
à changer, et sa ligne de journal sans auteur ; un refus de la base est nommé par sa
classe, sans son message. Jamais de mot de passe en argument, en variable ni dans une
sortie.

```bash
APPLICATION_ENVIRONNEMENT=preproduction \
APPLICATION_BASE_DSN='postgresql://app_conseiller@localhost:5432/apimanga_preprod' \
  make app-admin-creer
```

## Accessibilité

- **Niveau visé** : WCAG 2.1 AA (le RGAA 4 s'appuie dessus).
- **Outil** : axe-core, vendu avec son empreinte et sa licence, lancé par Playwright dans
  Chromium sur chaque écran et chaque cas, dans `make verify-poste` — **100 pages** au
  2026-11-08, dont les seize du lot 1 d'E4 (écrans des comptes, leurs refus, leurs
  avis, les pages 401, 403, « formulaire expiré », 404 sous un compte et 503), les huit
  du lot 2a (la recherche, ses résultats paginés, son état vide, sa saisie fautive, le
  catalogue indisponible, la fiche, la fiche sans note, la série introuvable) et les
  treize du lot 3 (« Mes critiques » pleine, vide, sans catalogue ; écrire, son refus,
  modifier, retirer, l'accès refusé ; la fiche avec critiques, et sans base ; la
  modération, sa liste vide, sa confirmation), sur une base jetable, les connexions faites dans le navigateur et vérifiées, chaque page
  reconnue à son titre. Politique de sécurité active, `<details>` ouverts, formulaire soumis. **Règle** : aucune violation `serious` ou
  `critical`, et aucune violation de la politique de sécurité en console.
- **Le rapport** : [`accessibilite/rapport_axe.md`](accessibilite/rapport_axe.md), et sa
  sortie, `rapport_axe.json` : versionnés, réécrits par le test si le résultat change.
  **Défauts inscrits : aucun.** Une règle reste à relire à l'œil, dite au rapport.
- **Sans navigateur, sur le poste et sur GitHub** : des tests de balisage par critère
  (`-k wcag`) : langue, titres, étiquettes, lien d'évitement, focus, zone annoncée, contraste
  des couleurs du thème calculé.
- **Ce que l'outil ne couvre pas** : un outil automatique couvre une partie seulement des
  critères, de l'ordre d'un tiers. **Relecture manuelle du 2026-11-05**, dans Firefox, contre
  la doublure : parcours complet au clavier, agrandissement à 200 %, lecture sans couleur
  (simulation de Firefox) : sans écart. **Lecteur d'écran** (Orca), sur une réponse et un
  encart : il lit, mais sa voix est restée inaudible à la relecture : la compréhension à
  l'oreille **n'est pas jugée**.
- **Sans JavaScript**, `aria-live` et `role="alert"` n'annoncent rien au chargement d'une
  page : c'est le focus qui annonce, porté sur le titre de la réponse, sur celui de l'encart,
  décrit par son texte, ou sur le champ fautif. L'audit vérifie dans Chromium que le focus y
  est, et l'arbre d'accessibilité porte ces titres et ce texte ; ce qu'en dit un lecteur
  d'écran n'a pas été jugé à l'oreille.

## Sécurité et données

- **La clé de l'application** : un consommateur de l'API, `application`, révocable à part.
  Générée sur le poste, 32 caractères au moins, en ASCII :
  `uv run --no-project python -c "import secrets; print(secrets.token_urlsafe(32))"`. Posée
  dans le `.env` du module de l'API (`API_MODELE_CLES`, `nom:secret`) et dans celui de ce
  module (`APPLICATION_CLE_API`, le secret seul). Jamais dans un fichier suivi, une page, un
  journal : un test le vérifie, et les hooks la refusent par valeur et par aspect. Pour la
  révoquer : la retirer du trousseau, redémarrer l'API.
- **En-têtes** : politique de sécurité du contenu stricte (aucun script, aucune ressource
  externe ; `data:` pour les images seules : Pico dessine ses coches ainsi, 14 occurrences,
  toutes des images SVG, et une image n'exécute rien), `X-Content-Type-Options`,
  `Referrer-Policy`, `Cache-Control: no-store` sur les pages de conversation.
- **Formulaire** : en `POST`, corps borné, gabarits échappés, aucun marquage « sûr » d'un
  texte venu de l'API ; **un jeton CSRF** dans chaque formulaire `POST` (celui du conseiller
  compris), comparé en temps constant au jeton de la session : absent ou faux, un 403 et la
  page « Ce formulaire a expiré, rechargez la page », avant tout appel à l'API.
- **La base de l'application** (E4, lot 1) : le schéma `application` d'`apimanga`
  (`schema/README.md`), par le rôle de connexion `app_conseiller`, membre de `app_rw` seul
  — rien hors du schéma, pas même en lecture ; requêtes paramétrées seules ; une
  connexion par opération, refermée aussitôt, pas de pool (quelques requêtes par minute,
  10 connexions au plus pour le rôle) ; délai borné (`[comptes] delai_base_s`) pour se
  connecter, pour une instruction et pour attendre un verrou. DSN **sans mot de passe**
  (`~/.pgpass` sur le poste, un fichier monté dans un conteneur) : un DSN qui en porte un
  empêche de démarrer. Base absente ou injoignable : les pages de compte disent 503,
  le conseiller fonctionne ; `/sante` et la métrique `application_base_etat` disent son
  état, sans détail.
- **Les mots de passe** : **Argon2id**, par `argon2-cffi` directement, aux paramètres par
  défaut de la bibliothèque, relevés le 2026-11-08 (argon2-cffi 25.1.0) : **temps 3, mémoire
  65 536 Kio (64 Mio), parallélisme 4**, empreinte de 32 octets, sel de 16 octets ; un
  nouveau hachage à la connexion si les paramètres changent. Le hachage tourne hors de la
  boucle d'événements, **quatre calculs au plus à la fois** (la mémoire reste bornée). Un
  pseudo inconnu passe par une **vérification factice** (empreinte de référence calculée
  au démarrage).
- **Les sessions** : `SessionMiddleware` de Starlette, cookie signé (`itsdangerous`,
  expiration vérifiée côté serveur), `HttpOnly`, `SameSite=Lax`, `Secure` hors
  développement, durée `[comptes] duree_session_s` **après la dernière page** (le jeton
  réécrit à chaque page renvoie le cookie). **Un cookie par environnement** :
  `conseiller_session` en développement, `conseiller_session_preproduction`,
  `conseiller_session_production`, et une signature liée à l'environnement — avec la
  même clé, une session de préproduction ne vaudrait pas en production ; **une clé par
  environnement** reste la règle. Il ne porte que l'identifiant du
  compte, un **numéro de génération** et le jeton CSRF — ni rôle, ni pseudo. Le rôle, l'état
  et la génération sont **relus en base à chaque requête** ; une génération différente ou
  un compte inactif vident la session. Changer son mot de passe ou être désactivé
  incrémente la génération : toutes les sessions du compte tombent ; la déconnexion aussi
  (choix du lot : une copie du cookie ne survit pas à la déconnexion, au prix de fermer
  les autres sessions du compte). Une connexion réussie
  rouvre une session neuve, au jeton neuf (pas de fixation). Les visiteurs anonymes
  reçoivent aussi le cookie : il porte leur jeton (cookie strictement nécessaire).
- **Le verrou** : 5 échecs consécutifs (`[comptes] echecs_avant_verrou`) posent un verrou de
  15 minutes (`duree_verrou_s`) ; un compte verrouillé, inactif, un mauvais mot de passe
  ou un pseudo inconnu rendent le même refus, par **le même travail** : une lecture sans
  verrou de ligne, une vérification hors transaction (rien n'est tenu pendant le calcul,
  des essais simultanés ne s'attendent pas), une écriture — le compteur, en une
  instruction atomique, ou une écriture vide ; un succès remet le compteur à zéro. Un
  pseudo hors forme (octet nul, trop long) n'atteint pas la base : il suit le chemin d'un
  pseudo inconnu ; un nom affiché ne garde que des caractères imprimables.
- **La garde par rôle** : une page réservée renvoie un anonyme vers la connexion (le chemin
  de retour n'accepte qu'un chemin local : ni « // », ni barre inverse, ni caractère de
  contrôle) — un formulaire réservé envoyé sans session reçoit la page 401 —, et répond
  403 à un compte sans le rôle. `Cache-Control: no-store` sur les
  pages de compte et d'administration.
- **Le 401 sans `WWW-Authenticate`** : écart déclaré à la RFC 9110 (§ 11.6.1), qui veut cet
  en-tête avec tout 401. L'authentification se fait par formulaire, aucun schéma n'est
  normalisé pour elle, et l'en-tête n'aurait aucun sens pour un navigateur ; même règle
  pour le 401 de la connexion refusée.
- **La garde d'environnement** : `APPLICATION_ENVIRONNEMENT` ; la base du DSN doit être celle
  de l'environnement (`preproduction` ↔ `apimanga_preprod`, `production` ↔ `apimanga`) ; en
  développement, `apimanga` est refusée. Un écart empêche de démarrer, sans montrer le DSN.
- **Ce qui est transmis, et par qui** : l'API envoie au fournisseur du profil utilisé la
  question, l'historique et les passages. L'application ne parle jamais à un fournisseur.
- **Ce qui est conservé par l'application** : rien des conversations. Les comptes, en
  base (pseudo, empreinte du mot de passe, rôle, dates, compteur d'échecs ; aucun courriel),
  et les listes d'un client — jusqu'à ce qu'il supprime son compte (« Registre des
  traitements » ci-dessous).
  Son journal, une ligne JSON par requête et par événement, porte route, statut, durée,
  identifiants (de la requête, du compte), code d'erreur, nombre de tours et taille de
  l'historique ; **jamais** la question, l'historique, la réponse, la clé, un mot de passe,
  une empreinte, un pseudo, le cookie ni le jeton. Tout événement pendant une requête porte
  son identifiant. Événements des mécanismes : `session_refusee`, `csrf_refuse`,
  `acces_refuse` (gabarit de la route), `base_indisponible`, `cle_session_ephemere` ; des
  écrans de compte (E4, lot 1, spec 03) : `inscription`, `connexion_reussie`,
  `connexion_refusee` (motif borné), `verrou_pose`, `deconnexion`, `mot_de_passe_change`,
  `mot_de_passe_refuse` (motif borné) ; de l'effacement (E4, lot 4) : `compte_supprime`,
  `suppression_refusee` (motif borné).

### Registre des traitements : les données du client

| Données | Finalité | Durée | Suppression | Ce qui subsiste |
|---|---|---|---|---|
| pseudo, nom affiché (le pseudo), empreinte Argon2id du mot de passe, rôle, dates, compteur d'échecs et verrou | se connecter, tenir ses listes | jusqu'à la suppression par l'intéressé | **immédiate**, par lui-même (`/compte/supprimer`), sans délai de rétention | une ligne `compte_supprime` du journal des actions, sans identité (`par_compte_id` nul, le numéro de l'ancien compte, la date) |
| listes (identifiant de série, état, date d'ajout) | favori, à lire, à commander | jusqu'au retrait de la série ou à la suppression du compte | le retrait supprime la ligne ; la suppression du compte, toutes (cascade) | rien |
| compteurs de questions du jour (`questions_jour`, un nombre, aucun texte) | le quota d'un client | jusqu'à la suppression du compte | cascade | rien |
| coûts du jour (`couts_jour`) | le budget du conseiller | — | — | des agrégats par jour, profil et mode, **sans compte** |

Les comptes du réseau (libraires, administrateurs) ne se suppriment pas : ils se
désactivent (leurs critiques signées restent attribuables).

### OWASP Top 10, mis en regard

| Risque | Ici |
|---|---|
| Injection | requêtes paramétrées seules, sur le schéma `application` ; le rôle n'a rien ailleurs (refus `42501` éprouvés) ; l'API par son contrat |
| XSS | échappement des gabarits, politique de sécurité du contenu sans script |
| Mauvaise configuration | en-têtes, hors ligne, image non root épinglée par empreinte ; configuration validée au démarrage |
| Composants vulnérables | verrou, `pip-audit --strict` bloquant ; ressources vendues sous empreinte |
| Secrets | hors dépôt, hooks, `repr` sans valeur |
| Clé du catalogue | dans l'environnement, jamais en page ni au journal (prouvé par test, sur le journal complet) ; gardée dans le seul client HTTP ; un refus (401) journalisé sans elle |
| Authentification | Argon2id aux paramètres de la bibliothèque ; vérification factice d'un pseudo inconnu ; verrou après 5 échecs ; même refus en tout cas (E4, lot 1) |
| Sessions | cookie signé à expiration, `HttpOnly`, `SameSite=Lax`, `Secure` hors développement ; génération relue en base à chaque requête ; session neuve à la connexion |
| Sessions : la déconnexion | déconnexion = révocation côté serveur de toutes les sessions du compte (génération + 1) : une copie du cookie ne survit pas, sur un poste partagé comme ailleurs |
| Contrôle d'accès | garde par rôle (403), renvoi vers la connexion par un chemin local seul ; mutations versionnées sur la garde, la génération, le verrou, le retour |
| Contrôle d'accès (A01) : l'administration | **toute page `/admin/*` exige le rôle admin** (`sessions.exiger_role("admin")`, en `GET` comme en `POST`) : un client, un libraire reçoivent 403, un visiteur la connexion ; le profil choisi est validé contre la liste de `/v1/info` avant d'être écrit ; mutations E4A-02 (profil hors liste), **E4A-03** (garde de rôle retirée) |
| Contrôle d'accès (A01) : les critiques | un libraire n'agit que sur les siennes (garde d'auteur dans la requête même, `WHERE compte_id`), l'administrateur retire sans jamais modifier ; agir sur celle d'un autre : 403, base inchangée (tests en `GET` et en `POST`) ; aucune suppression possible (droits de `app_rw`) ; mutations E4R-01, 02, 05, 06, 08 |
| Contrôle d'accès (A01) : l'effacement | la suppression exige **la session et le mot de passe** (vérifié par le chemin de la connexion : même compteur, même verrou) ; elle ne vise que le compte de la session ; un libraire, un administrateur : 403 ; mutations E4L-05 (sans le mot de passe), E4L-06 (sans la ligne de journal) |
| Contrôle d'accès (A01) : les listes | les listes ne sont lisibles et modifiables que par **leur compte** : chaque requête porte l'identifiant de la session, jamais un identifiant venu du formulaire ; un client ne voit ni ne change celles d'un autre ; un libraire, un administrateur : 403 au geste et à la page ; tests par rôle et à deux comptes ; mutations E4L-01 à 04, 07 |
| Injection (A03) : le texte des critiques | requêtes paramétrées ; le texte rendu **échappé** par les gabarits, jamais marqué « sûr » (test avec `<script>`, `<b>`, `<img onerror>` : rendus littéralement, aucun élément créé ; mutation E4R-07) ; politique de sécurité du contenu sans script |
| Journalisation des accès | événements de compte en JSON, identifiants seuls ; aucun secret, aucun pseudo |
| Désérialisation | sans objet : aucun format binaire ; l'historique est du JSON revalidé |
| Requêtes intersites | jeton CSRF par session dans chaque formulaire `POST`, comparé en temps constant, et `SameSite=Lax` (E4, lot 1) : un site tiers ne peut plus déclencher un appel facturé |
| Consommation de ressources non bornée (A04) : le conseiller | **couvert** (E4, lot 2b) : quatre bornes vérifiées avant l'appel — quota par session, quota par compte client, débit de la clé publique, budget du jour persisté (« Les bornes du conseiller ») ; un refus n'appelle jamais l'API ; mutations E4Q-04 à 07, E4Q-12 |
| Authentification (A07) : l'inscription | **couvert, par session** (E4, lot 2b) : 3 comptes par heure et par navigateur, aucune ligne au-delà ; limite assumée : un cookie effacé remet le compteur à zéro ; mutation E4Q-08 |

## Configuration

`config/reglages.toml` : chaque réglage et son motif, tous les textes des écrans. Lu et
validé au démarrage : un écart empêche de démarrer, et le message nomme le réglage.

| Réglage | Défaut | Motif |
|---|---|---|
| `[api] adresse` | `http://127.0.0.1:8010` | l'API du modèle sur le poste |
| `[api] identite` | `libraire-demo` | fixe tant qu'il n'y a pas de comptes |
| `[api.delais]` | connexion 5 s, réponse 75 s, `/v1/info` et `/v1/sante` 5 s | 75 s couvre largement le mesuré (p95 de 2,1 à 9,5 s, 2026-10-30 à 11-03), pas le pire cas théorique de l'API (90 à 240 s) |
| `[api.attentes]` | 60 s, 2 s | l'attente annoncée sans `Retry-After` lisible |
| `[reglages]` | température 0, reformulation cochée, mode démonstration éteint, `mistral-large-4` exclu ; trois positions | voir « Les réglages » |
| `[conversation]` | 500 caractères par question, 5 échanges, 10 tours, 4 000 caractères, 2 000 par tour | les bornes de l'API ; celles que `/v1/info` publie l'emportent, un écart est journalisé ; la longueur d'un tour est comparée au contrat par un test |
| `[corps]` | 16 Kio | un historique plein en français tient en 6,5 Kio environ (estimation) |
| `[ecoute]` | `127.0.0.1:8020` | 8000 est au module 02 |
| `[metriques]` | le bouclage | `/metrics` ; 404 ailleurs |
| `[fournisseurs]` | Mistral : Union européenne ; OpenAI : hors Union européenne par défaut | `/v1/info` ne dit pas l'hébergement |
| `[catalogue]` | `http://127.0.0.1:8000` ; 20 par page (50 au plus) ; saisie de 2 à 100 ; connexion 3 s, réponse 5 s | l'API data du 02 sur le poste ; ses bornes, recopiées et comparées au contrat par un test ; la recherche répond en 116 ms de médiane (2026-11-08) |
| `[comptes]` | session 8 h après la dernière page ; verrou après 5 échecs, 15 min ; pseudo 3 à 30, nom affiché 60 au plus (les bornes de la migration `027`, comparées par un test) ; mot de passe 12 à 128 (décision 7) ; base 3 s ; 25 comptes par page | voir « Sécurité et données » |
| `[textes]` | tous les textes des écrans | une seule source ; un texte absent empêche de démarrer |

| Variable | Rôle |
|---|---|
| `APPLICATION_CLE_API` | la clé de l'application, obligatoire |
| `APPLICATION_ENVIRONNEMENT` | `developpement` (défaut), `preproduction`, `production` : la garde d'environnement |
| `APPLICATION_BASE_DSN` | le DSN de la base des comptes, **sans mot de passe** ; facultatif : sans lui, le conseiller seul |
| `APPLICATION_CLE_SESSION` | la clé de signature des sessions, 32 octets au moins, **une par environnement** ; obligatoire en préproduction et en production ; absente en développement, une clé éphémère est tirée au démarrage (avertissement au journal, sessions perdues au redémarrage) |
| `API_MODELE_ADRESSE` | l'adresse de l'API |
| `APPLICATION_CLE_CATALOGUE` | la clé de l'application auprès du catalogue (l'API data du 02, consommateur `conseiller`) ; obligatoire si `CATALOGUE_ADRESSE` est posée et en préproduction et production ; absente en développement : catalogue non configuré, l'encart le dit, le reste fonctionne |
| `CATALOGUE_ADRESSE` | l'adresse du catalogue ; défaut `[catalogue] adresse`, `http://catalogue:8000` dans la pile |
| `APPLICATION_ECOUTE` | `hôte:port` |
| `APPLICATION_METRIQUES_RESEAUX` | les réseaux admis à lire `/metrics`, séparés par des virgules |
| `APPLICATION_PORT_PUBLIE` | le port publié par le Compose ; défaut 8020 |

## Démarrer

### Seule, l'API tournant déjà

Depuis ce dossier :

```bash
cp .env.example .env     # APPLICATION_CLE_API : le secret seul, sans son nom
uv sync --locked
uv run --env-file .env python -m application
```

Sans clé, l'application refuse de démarrer. Sans API, elle démarre et le dit à l'écran.

### Par Compose : le service d'embedding, l'API, l'application, le catalogue

```bash
make app-compose         # depuis la racine du dépôt
make app-compose-arret
```

Il faut **trois** `.env` : celui de ce module, celui de l'API et celui du module 02 (le
catalogue) ; sans l'un d'eux, Compose refuse et nomme le fichier absent. Dans celui de
l'API, ajouter à la ligne `API_MODELE_CLES`, séparé par une virgule,
`application:<secret>` : une seconde ligne remplacerait la première.

**Le catalogue dans la pile** (E4, lot 2a) — le service `catalogue`, l'image
`api-catalogue:0.6.0` du module 02 :

- dans `02_api_manga/.env` : `API_KEYS=conseiller:<clé>` (le consommateur de
  l'application) et `DB_NAME` (`apimanga`, lu seulement) ; dans le `.env` de ce module,
  `APPLICATION_CLE_CATALOGUE=<la même clé>`, posée **avant** tout lancement de la pile — la
  pile pose `CATALOGUE_ADRESSE` : sans `APPLICATION_CLE_CATALOGUE`, l'application ne démarre
  pas : c'est voulu (une clé absente est une erreur de déploiement, pas un mode ; « non
  configuré » reste le cas du poste en développement, sans adresse posée) ;
- le mot de passe de `manga_api` : dans `02_api_manga/.pgpass.conteneur`, hors dépôt, mode
  600, une ligne `host.docker.internal:5432:<base>:manga_api:<mot de passe>`, monté comme
  secret (`PGPASSFILE`) — jamais dans le DSN, jamais dans un `.env` ; le service tourne sous
  l'utilisateur de l'hôte (`HOTE_UID`, `HOTE_GID`), seul lecteur du fichier ;
- le serveur PostgreSQL admet `manga_api` depuis le seul sous-réseau du catalogue, une
  ligne de `pg_hba.conf` par base : `host  <base>  manga_api  172.30.2.0/24  scram-sha-256` ;
- le catalogue est publié sur `127.0.0.1:8000` (`CATALOGUE_PORT_PUBLIE`), joint par
  l'application sur `applicatif` (`http://catalogue:8000`), et sort vers la base par
  `catalogue_sortie` (`172.30.2.0/24`, fixe) ; il n'entre pas dans le monitorage (pas de
  métriques).

Le Compose du module de l'API ne change pas : `compose.yaml` s'y ajoute par fusion. **L'API
n'est pas publiée** : on l'atteint par l'application ; l'application et le catalogue le
sont, sur l'adresse locale seule. Il ajoute trois réseaux : `applicatif`, l'application,
l'API et le catalogue, rien d'autre ; `publication`, pour le port : Docker ne publie pas sur
un réseau interne, celui-ci a donc une route vers l'extérieur, que l'application n'emploie
pas ; `catalogue_sortie`, du catalogue vers la base de l'hôte.

Pour montrer aussi le Swagger de l'API et MLflow : `make demo-direct`, qui ajoute
`compose.demo.yaml` et publie alors le port de l'API sur l'adresse locale. Voir
[../demo/README.md](../demo/README.md).

## Vérifier

```bash
make verify MODULE=12    # depuis la racine : Ruff, tests, essai Compose, Pyright, Bandit, pip-audit
make verify-poste        # l'audit d'accessibilité, entre autres
```

L'essai d'intégration lève l'image de l'application contre la doublure de l'API,
conteneurisée, sur un réseau sans sortie. Aucun test n'atteint l'API réelle ni un
fournisseur : une garde coupe toute requête du client de l'application hors du bouclage.

Une seule installation, hors de `make verify`, pour l'audit ; depuis ce dossier :

```bash
uv run --locked --extra dev --extra audit playwright install chromium
AUDIT_AXE=1 uv run --locked --extra dev --extra audit pytest tests/test_audit_axe.py
```

## Limites déclarées

- **Tolérance aux fautes du conseiller** : mesurée le 2026-11-08 (E4, lot 2b, D14 a) — douze
  questions du jeu gelé, une faute mécanique chacune (auteur inversé, transposition, lettre
  manquante, lettre doublée), posées en strict à l'API réelle depuis la préproduction :
  **11 / 12** citent une série attendue ; résultat et méthode au rapport du lot 2b ; la
  résolution des titres dans l'API (D14 c) reste une décision de Max.

- **Une instance**, aucune mémoire partagée.
- **Pas de file d'attente** : le plafond du compte Mistral, 15 requêtes par minute
  (2026-11-03), est rendu à l'écran, pas absorbé ; soit environ 7 tours de conversation par minute. File d'attente et
  quota par poste : épreuve E4.
- **Aucune personnalisation du conseiller** : il ne lit ni les listes ni l'historique du
  compte.
- **Un seul écran.**
- **Un clic trop tôt après un refus** peut coûter une reformulation (« Les cas dégradés »).
- **Au-delà de 75 s**, l'écran dit « délai dépassé » pendant que l'API peut continuer, et
  l'appel peut être facturé.
- **Un texte presque tout en japonais** peut dépasser les 16 Kio du corps : page d'erreur.
  De même une critique de 2 000 caractères presque toute en japonais (environ 18 Kio
  encodée) : la page 413, dont le texte parle de la conversation.
- **Le texte du modèle** : `*x*` et `**x**` sont rendus en emphase, et un titre repéré avant
  ou après sa marque ; une emphase qui ouvre avant le titre et le ferme après lui
  (`*Un : Titre* [S:1]`) laisse ses astérisques ; un saut de ligne simple ne coupe pas le
  paragraphe. Ces formes n'ont pas été vues dans les réponses mesurées.
- **La température 0 ne rejoue ni le texte, ni toujours les séries** : aucune graine n'est
  envoyée. Le 2026-11-04, sur une même question posée cinq fois à 0, le texte a changé chaque
  fois ; deux séries sont revenues en carte les cinq fois, une troisième s'y est ajoutée la
  dernière ; séries nommées : 2, 2, 5, 2, 3. Une seule question : c'est un constat, pas une
  mesure. L'essai d'une graine par profil reste à mesurer.
- **Une série nommée sans carte** : le modèle peut marquer une série que ses
  `series_citees` ne portent pas, et l'API ne contrôle pas ce cas. La marque est masquée :
  le titre écrit par le modèle reste, sans lien ni carte, non contrôlé ; l'identifiant est
  dans les détails techniques et dans le champ caché.
- **Un passage est un extrait** : le fragment que la recherche a retrouvé, 1 200 caractères
  au plus, rendu en entier tel que l'API le donne ; c'est aussi ce que le modèle a lu. Une
  critique se lit donc souvent en partie (corpus v2, 2026-11-04 : 9 644 critiques sur 11 041
  tiennent en plusieurs fragments). D'autres « … » viennent de la collecte : le champ d'un
  résumé de série peut porter, après le résumé, des aperçus de critiques coupés vers 400
  caractères (constaté sur une série, 2026-11-04) ; 5 039 résumés sur 8 685 finissent par des
  points de suspension.
- **Quitter la page perd la conversation** : « À propos et données » s'ouvre dans le même
  onglet, et revenir par « Précédent » peut demander de renvoyer le formulaire, donc un
  nouvel appel. C'est la conséquence de « rien n'est gardé » ; l'épreuve E4 la règle avec
  les sessions.

## Ce que l'épreuve E4 ajoute

**Lot 1 (le socle)** : la base, Argon2id, les sessions signées, le jeton CSRF, le verrou,
la garde par rôle et d'environnement, les écrans des comptes et leurs trois rôles (client,
libraire, administrateur), la commande du premier administrateur (ci-dessus). **Lot 2a
(le catalogue)** : la recherche et la page de série, par l'API data du 02. **Lot 3 (les
critiques signées)** : publier, modifier, retirer, modérer, afficher face à la note des
lecteurs (ci-dessus). **Lot 4 (les listes du client)** : favori, à lire, à commander, depuis
la fiche, les cartes du conseiller et « Mes listes » ; la suppression du compte par son
titulaire, le droit à l'effacement (ci-dessus). À venir : l'entrée des critiques de libraires dans le corpus ; le masquage à
l'affichage des mentions de membres connues du corpus ; l'administration du profil actif
et de l'état des services ; le serveur avec préproduction et production ; l'identité
visuelle, sur `theme.css`.
