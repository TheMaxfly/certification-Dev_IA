# Le schéma de données de l'application

Le schéma `application` de la base `apimanga` : les données que l'application du libraire
écrit — ses comptes, les critiques signées des libraires, les listes des clients, le journal
des actions, les coûts du modèle de langage, les réglages qu'un administrateur change.
Il est créé, avec toutes ses tables, par **une seule migration**,
`database/migrations/027_schema_application.sql` (E4, lot 1) : les lots suivants s'en
servent sans repasser par la production. Une exception, décidée : le compteur des
questions des comptes clients, `questions_jour`, ajouté par la `029` (E4, lot 2b), quand
le quota est passé du cookie au compte.

L'application n'y entre que par le rôle de connexion `app_conseiller`, membre du rôle de
groupe `app_rw`. Le catalogue (schéma `manga`), elle ne le lit pas en base : elle le lit par
l'API data du module 02, et parle à l'IA par l'API du modèle (module 11).

## Les tables

```text
comptes ──< critiques            (compte_id, ON DELETE RESTRICT)
comptes ──< listes               (compte_id, ON DELETE CASCADE)
comptes ──< journal_actions      (par_compte_id, ON DELETE SET NULL)
comptes ──< reglages             (modifie_par, ON DELETE SET NULL)
comptes ──< questions_jour       (compte_id, ON DELETE CASCADE ; migration 029)
couts_jour                       (aucun lien : un agrégat)

series_id (critiques, listes) → le catalogue, par valeur : AUCUNE clé étrangère
```

### `comptes`

| Colonne | Type | Règle |
|---|---|---|
| `id` | `bigint`, identité | clé primaire |
| `pseudo` | `text` | unique ; `^[a-z0-9_-]{3,30}$` (ramené en minuscules par l'application) |
| `nom_affiche` | `text` | 1 à 60 caractères |
| `role` | `text` | `client`, `libraire` ou `admin` |
| `hachage` | `text` | l'empreinte **Argon2id** du mot de passe, jamais le mot de passe |
| `actif` | `boolean` | vrai par défaut ; un compte désactivé ne se connecte plus |
| `generation` | `integer` | 0 par défaut, ≥ 0 ; + 1 quand le mot de passe change ou que le compte est désactivé : toute session d'une autre génération est refusée |
| `doit_changer_mdp` | `boolean` | faux par défaut ; vrai pour un compte créé par un administrateur |
| `echecs_connexion` | `integer` | 0 par défaut, ≥ 0 : les échecs consécutifs |
| `verrou_jusqu_a` | `timestamptz` | nul, ou la fin du verrou posé après trop d'échecs |
| `cree_le`, `modifie_le` | `timestamptz` | `now()` par défaut |

Aucun courriel, aucune autre donnée personnelle.

### `critiques`

| Colonne | Type | Règle |
|---|---|---|
| `id` | `bigint`, identité | clé primaire |
| `compte_id` | `bigint` | → `comptes`, `ON DELETE RESTRICT` : un libraire qui a des critiques ne se supprime pas |
| `series_id` | `integer` | l'identifiant du catalogue, sans clé étrangère |
| `volume_url`, `volume_libelle` | `text` | nuls ensemble (critique de la série) ou renseignés ensemble (critique d'un tome) |
| `texte` | `text` | 1 à 10 000 caractères (les bornes métier, plus serrées, sont dans la configuration de l'application) |
| `note` | `smallint` | de 0 à 10 |
| `label` | `text` | nul, ou un code `^[a-z_]{1,40}$` d'une liste fixe validée par l'application |
| `statut` | `text` | `publiee` (défaut) ou `retiree` : un retrait est un statut, jamais une suppression |
| `publie_le`, `modifie_le` | `timestamptz` | `now()` par défaut |

- Index unique partiel `(compte_id, series_id, coalesce(volume_url, ''))` sur les critiques
  **publiées** : une seule critique publiée par libraire, série et tome ; la même redevient
  possible après le retrait de la première.
- Index `(series_id)` sur les critiques publiées : la page d'une série.
- C'est la **table distincte** que lira, chaque mois, la construction du corpus : une
  critique publiée entre dans la recherche à la promotion suivante du corpus.
- **Usage effectif (E4, lot 3)** : `application/critiques.py` y écrit — un libraire publie,
  modifie (texte, note, label seulement) et retire les siennes, un administrateur retire
  n'importe laquelle ; un retrait pose `statut = 'retiree'` et `modifie_le`, jamais une
  suppression ; `volume_libelle` est le libellé du volume copié du 02 à l'écriture (« Tome N
  — titre »), `label` un code de la liste de `[critiques]`. Chaque action écrit dans la même
  transaction sa ligne de `journal_actions` (`critique_publiee`, `critique_modifiee`,
  `critique_retiree`, objet `critique`, `details` = `series_id`, `tome`, `par` : `auteur` ou
  `admin`) — jamais le texte. Le modèle ne change pas.

### `journal_actions`

| Colonne | Type | Règle |
|---|---|---|
| `id` | `bigint`, identité | clé primaire |
| `le` | `timestamptz` | `now()` par défaut |
| `par_compte_id` | `bigint` | → `comptes`, `ON DELETE SET NULL` ; nul pour une action sans auteur (le premier administrateur, créé par une commande) |
| `action` | `text` | `compte_cree`, `compte_desactive`, `compte_reactive`, `compte_supprime`, `mot_de_passe_change`, `critique_publiee`, `critique_modifiee`, `critique_retiree`, `profil_actif_change` |
| `objet_type`, `objet_id` | `text`, `bigint` | l'objet de l'action, nuls si sans objet |
| `details` | `jsonb` | un objet, `{}` par défaut |

**En ajout seul**, et c'est la base qui le garantit : `app_rw` n'y a que `SELECT` et
`INSERT`. **Jamais de texte libre ni de secret dans `details`** — ni mot de passe, ni
empreinte, ni pseudo, ni texte saisi : des identifiants et des codes seulement.

### `listes`

| Colonne | Type | Règle |
|---|---|---|
| `compte_id` | `bigint` | → `comptes`, `ON DELETE CASCADE` : les listes partent avec le compte |
| `series_id` | `integer` | l'identifiant du catalogue, sans clé étrangère |
| `etat` | `text` | `favori`, `a_lire` ou `a_commander` |
| `ajoute_le` | `timestamptz` | `now()` par défaut |

Clé primaire `(compte_id, series_id)` : un état par série et par compte.

### `couts_jour`

| Colonne | Type | Règle |
|---|---|---|
| `jour` | `date` | |
| `profil` | `text` | le profil du modèle de langage |
| `mode` | `text` | `strict` ou `enrichi` |
| `appels` | `integer` | 0 par défaut, ≥ 0 |
| `cout_usd` | `numeric(12,6)` | 0 par défaut, ≥ 0 : la somme des `contexte.cout_estime_usd` |

Clé primaire `(jour, profil, mode)`. Depuis le lot 2b d'E4 : le plafond de coût du jour
du conseiller, la somme du jour (UTC) relue avant chaque appel ; le profil et le mode
sont ceux que l'API rend.

### `questions_jour` (migration `029`, E4, lot 2b)

| Colonne | Type | Règle |
|---|---|---|
| `compte_id` | `bigint` | → `comptes`, `ON DELETE CASCADE` : les compteurs partent avec le compte |
| `jour` | `date` | le jour civil UTC, celui de `couts_jour` |
| `questions` | `integer` | 0 par défaut, ≥ 0 : les questions posées au conseiller ce jour-là |

Clé primaire `(compte_id, jour)`. Le quota des comptes clients (10 par jour) ; aucun texte,
un compteur. `app_rw` : `SELECT`, `INSERT`, `UPDATE`, pas de `DELETE`.

### `reglages`

| Colonne | Type | Règle |
|---|---|---|
| `cle` | `text` | clé primaire, `^[a-z_]{1,60}$` |
| `valeur` | `text` | |
| `modifie_par` | `bigint` | → `comptes`, `ON DELETE SET NULL` |
| `modifie_le` | `timestamptz` | `now()` par défaut |

## Les droits de `app_rw`

| Objet | Droits |
|---|---|
| schéma `application` | `USAGE` ; `CONNECT` sur la base |
| `comptes`, `listes` | `SELECT`, `INSERT`, `UPDATE`, `DELETE` |
| `critiques` | `SELECT`, `INSERT`, `UPDATE` — pas de `DELETE` |
| `journal_actions` | `SELECT`, `INSERT` — ni `UPDATE` ni `DELETE` |
| `couts_jour`, `reglages` | `SELECT`, `INSERT`, `UPDATE` |
| séquences des colonnes d'identité | rien : l'insertion n'en demande aucun droit |
| `manga`, `bench`, `staging`, `public` | **rien**, pas même en lecture : aucune table, aucune colonne, aucune séquence (restent ce que `PUBLIC` a par défaut, comme pour tout rôle : l'usage du schéma `public`, `TEMP` sur la base) |

`PUBLIC` n'a aucun droit sur le schéma. Chaque refus est un `42501` éprouvé par un test
(`database/tests/test_schema_application.py`).

## Pourquoi

- **Aucune clé étrangère vers le catalogue** : `series_id` est validé par l'API data du
  module 02 au moment de l'écriture. Le catalogue se recharge par instantanés (Manga
  Sanctuary, Kitsu) ; une clé étrangère lierait les données de l'application à un
  instantané, et empêcherait de recharger le catalogue tant qu'une critique ou une liste
  désigne une série (E4, lot 1, décision 3).
- **Le journal en ajout seul, par les droits** : ce qu'un compte a fait (création,
  désactivation, changement de mot de passe, critique publiée ou retirée) ne se réécrit pas,
  même par l'application. La règle ne dépend pas du code de l'application.
- **Une critique ne se supprime pas** : son retrait est un statut et une ligne du journal ;
  elle reste attribuable à son libraire.
- **Toutes les tables en une migration** : la production reçoit le schéma une fois, au lot
  de livraison continue, après la sauvegarde et l'accord de Max ; les lots suivants s'en
  servent sans nouvelle migration.
