# 12 — Application du libraire

Une application web minimale, rendue par le serveur, qui consomme l'API du modèle
(`11_api_modele/`) par sa clé : le libraire pose une question, l'application appelle
`POST /v1/reponse` et affiche la réponse ou le refus, les séries citées, leurs métadonnées
et les passages cités.

## Ce qu'elle fait, et ce qu'elle ne fait pas

- **Elle fait** : une page question → réponse ; une conversation de cinq échanges au plus ;
  le choix du profil et deux réglages de démonstration ; un écran nommé par cas dégradé ;
  la mention « Vous parlez à une IA » sur chaque écran ; un audit d'accessibilité outillé.
- **Elle ne fait pas** : pas de compte, pas de mémoire côté serveur, aucune écriture, en
  base ou en fichier ; mode strict seul tant que l'API n'en expose pas d'autre ; aucune
  bascule automatique de profil : le repli reste un geste humain.

C'est le tronc de l'application de l'épreuve E4 : même module, même pile. Le dossier
`schema/` garde la place de son schéma de données, vide à ce stade.

## Architecture

```text
navigateur → application (127.0.0.1:8020) → API du modèle (X-API-Key de l'application)
                                             ├→ service d'embedding
                                             ├→ base, sous modele_ro
                                             └→ fournisseur du profil
```

Ce que l'application ne voit jamais : la base, les clés des fournisseurs, les pseudonymes
(l'API contrôle ses passages et ses sorties ; l'application n'affiche que ce qu'elle rend,
et aucun chemin ne la contourne).

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
4. le formulaire : la question ; les **Réglages**, repliés ; le champ caché de l'historique ;
5. la réponse ou le refus ; la ligne discrète (profil, fournisseur, modèle, date de l'appel,
   reformulation, coût estimé, latence) ; une carte par série citée, ses métadonnées et ses
   seuls passages cités ; les détails techniques, repliés ;
6. le pied de page : la mention complète, « À propos et données », les versions.

`/a-propos` : ce qui est transmis, tous les profils proposés et leur hébergement, ce qui
n'est pas conservé. `/sante` : du JSON, toujours 200 : l'application sert sans l'API.
`/metrics` : Prometheus, sur le bouclage.

### Les réglages

| Réglage | Valeurs | Défaut |
|---|---|---|
| Profil | les profils disponibles de `/v1/info`, sauf `mistral-large-4` : en aperçu, à remesurer. Chaque option dit son fournisseur et son hébergement | le profil actif ; exclu ou indisponible : le premier proposé |
| Température | 0 « stable », seule valeur mesurée ; 0,3 « modérée », candidate pour la production, **à mesurer avant de devenir défaut** ; 0,7 « variée », pour la démonstration | 0 |
| Reformulation | cochée ou non ; sans effet à la première question | cochée |
| Mode | affiché seulement si l'API en déclare plus d'un | `strict` |

Le reste (`longueur_max`, `nombre_series`, `passages_par_serie`, `catalogue_seul`) reste aux
défauts de l'API.

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

## Accessibilité

- **Niveau visé** : WCAG 2.1 AA (le RGAA 4 s'appuie dessus).
- **Outil** : axe-core, vendu avec son empreinte et sa licence, lancé par Playwright dans
  Chromium sur chaque écran et chaque cas, dans `make verify-poste`. Politique de sécurité
  active, `<details>` ouverts, formulaire soumis. **Règle** : aucune violation `serious` ou
  `critical`, et aucune violation de la politique de sécurité en console.
- **Le rapport** : [`accessibilite/rapport_axe.md`](accessibilite/rapport_axe.md), et sa
  sortie, `rapport_axe.json` : versionnés, réécrits par le test si le résultat change.
  **Défauts inscrits : aucun.** Une règle reste à relire à l'œil, dite au rapport.
- **Sans navigateur, sur le poste et sur GitHub** : des tests de balisage par critère
  (`-k wcag`) : langue, titres, étiquettes, lien d'évitement, focus, zone annoncée, contraste
  des couleurs du thème calculé.
- **Ce que l'outil ne couvre pas** : un outil automatique couvre une partie seulement des
  critères, de l'ordre d'un tiers. Reste une relecture manuelle : parcours complet au
  clavier, lecture d'une réponse et d'un encart au lecteur d'écran, agrandissement à 200 %,
  lecture en niveaux de gris.
- **Sans JavaScript**, `aria-live` et `role="alert"` n'annoncent rien au chargement d'une
  page : c'est le focus qui annonce, porté sur le titre de la réponse, sur celui de l'encart,
  décrit par son texte, ou sur le champ fautif. L'audit vérifie dans Chromium que le focus y
  est ; ce qu'en dit un lecteur d'écran reste à la relecture manuelle.

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
  texte venu de l'API.
- **Ce qui est transmis, et par qui** : l'API envoie au fournisseur du profil utilisé la
  question, l'historique et les passages. L'application ne parle jamais à un fournisseur.
- **Ce qui est conservé par l'application : rien.** Son journal porte route, statut, durée,
  identifiants, code d'erreur, nombre de tours et taille de l'historique ; jamais la
  question, l'historique, la réponse ni la clé.

### OWASP Top 10, mis en regard

| Risque | Ici |
|---|---|
| Injection | aucun accès à la base ; l'API seule, par son contrat |
| XSS | échappement des gabarits, politique de sécurité du contenu sans script |
| Mauvaise configuration | en-têtes, hors ligne, image non root épinglée par empreinte ; configuration validée au démarrage |
| Composants vulnérables | verrou, `pip-audit --strict` bloquant ; ressources vendues sous empreinte |
| Secrets | hors dépôt, hooks, `repr` sans valeur |
| Authentification, sessions, contrôle d'accès, journalisation des accès | **relèvent de l'épreuve E4** : pas de comptes ici |
| Désérialisation | sans objet : aucun format binaire ; l'historique est du JSON revalidé |
| Requêtes intersites | **non contrôlées à ce stade** : `POST /question` ne lit ni `Origin` ni `Sec-Fetch-Site`. Sans compte ni cookie, il n'y a rien à voler, mais un site tiers ouvert sur le poste du libraire pourrait déclencher un appel facturé. Limite déclarée, à traiter avec les sessions de l'épreuve E4 |

## Configuration

`config/reglages.toml` : chaque réglage et son motif, tous les textes des écrans. Lu et
validé au démarrage : un écart empêche de démarrer, et le message nomme le réglage.

| Réglage | Défaut | Motif |
|---|---|---|
| `[api] adresse` | `http://127.0.0.1:8010` | l'API du modèle sur le poste |
| `[api] identite` | `libraire-demo` | fixe tant qu'il n'y a pas de comptes |
| `[api.delais]` | connexion 5 s, réponse 75 s, `/v1/info` et `/v1/sante` 5 s | 75 s couvre largement le mesuré (p95 de 2,1 à 9,5 s, 2026-10-30 à 11-03), pas le pire cas théorique de l'API (90 à 240 s) |
| `[api.attentes]` | 60 s, 2 s | l'attente annoncée sans `Retry-After` lisible |
| `[reglages]` | température 0, reformulation cochée, `mistral-large-4` exclu ; trois positions | voir « Les réglages » |
| `[conversation]` | 500 caractères par question, 5 échanges, 10 tours, 4 000 caractères, 2 000 par tour | les bornes de l'API ; celles que `/v1/info` publie l'emportent, un écart est journalisé ; la longueur d'un tour est comparée au contrat par un test |
| `[corps]` | 16 Kio | un historique plein en français tient en 6,5 Kio environ (estimation) |
| `[ecoute]` | `127.0.0.1:8020` | 8000 est au module 02 |
| `[metriques]` | le bouclage | `/metrics` ; 404 ailleurs |
| `[fournisseurs]` | Mistral : Union européenne ; OpenAI : hors Union européenne par défaut | `/v1/info` ne dit pas l'hébergement |
| `[textes]` | tous les textes des écrans | une seule source ; un texte absent empêche de démarrer |

| Variable (`.env.example`) | Rôle |
|---|---|
| `APPLICATION_CLE_API` | la clé de l'application, obligatoire |
| `API_MODELE_ADRESSE` | l'adresse de l'API |
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

### Par Compose : le service d'embedding, l'API, l'application

```bash
make app-compose         # depuis la racine du dépôt
make app-compose-arret
```

Il faut les deux `.env`, celui de ce module et celui de l'API. Dans le second, ajouter à la
ligne `API_MODELE_CLES`, séparé par une virgule, `application:<secret>` : une seconde ligne
remplacerait la première.

Le Compose du module de l'API ne change pas : `compose.yaml` s'y ajoute par fusion. **Seule
l'application est publiée**, sur l'adresse locale ; on atteint l'API par elle. Il ajoute deux
réseaux : `applicatif`, l'application et l'API, rien d'autre ; `publication`, pour le port :
Docker ne publie pas sur un réseau interne, celui-ci a donc une route vers l'extérieur, que
l'application n'emploie pas.

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

- **Une instance**, aucune mémoire partagée.
- **Pas de file d'attente** : le plafond du compte Mistral, 15 requêtes par minute
  (2026-11-03), est rendu à l'écran, pas absorbé ; soit environ 7 tours de conversation par minute. File d'attente et
  quota par poste : épreuve E4.
- **Aucune personnalisation** : pas de compte.
- **Un seul écran.**
- **Un clic trop tôt après un refus** peut coûter une reformulation (« Les cas dégradés »).
- **Au-delà de 75 s**, l'écran dit « délai dépassé » pendant que l'API peut continuer, et
  l'appel peut être facturé.
- **Un texte presque tout en japonais** peut dépasser les 16 Kio du corps : page d'erreur.
- **Le texte du modèle** : `*x*` et `**x**` sont rendus en emphase, et un titre repéré avant
  ou après sa marque ; une emphase qui ouvre avant le titre et le ferme après lui
  (`*Un : Titre* [S:1]`) laisse ses astérisques ; un saut de ligne simple ne coupe pas le
  paragraphe. Ces formes n'ont pas été vues dans les réponses mesurées.
- **La température 0 ne rejoue pas le texte** : aucune graine n'est envoyée. Le 2026-11-04,
  sur une même question posée quatre fois à 0, les mêmes séries sont revenues en carte, le
  texte a changé, et le nombre de séries nommées aussi (2, 2, 5, 2). Une seule question :
  c'est un constat, pas une mesure. L'essai d'une graine par profil reste à mesurer.
- **Une série nommée sans carte** : le modèle peut marquer une série que ses
  `series_citees` ne portent pas, et l'API ne contrôle pas ce cas. La marque est masquée :
  le titre écrit par le modèle reste, sans lien ni carte, non contrôlé ; l'identifiant est
  dans les détails techniques et dans le champ caché.
- **Quitter la page perd la conversation** : « À propos et données » s'ouvre dans le même
  onglet, et revenir par « Précédent » peut demander de renvoyer le formulaire, donc un
  nouvel appel. C'est la conséquence de « rien n'est gardé » ; l'épreuve E4 la règle avec
  les sessions.

## Ce que l'épreuve E4 ajoute

Comptes avec mot de passe haché et sessions signées à expiration ; trois rôles (client,
libraire, administrateur) ; page de série ; critique de libraire signée, entrant dans le
corpus ; administrateur (profil actif, comptes, état des services) ; serveur avec préproduction
et production ; identité visuelle, sur `theme.css`.
