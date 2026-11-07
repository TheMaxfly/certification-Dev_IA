# 13_monitorage

Le monitorage du modèle : **Prometheus collecte, Grafana restitue et alerte, MLflow garde la
qualité**. Une sonde d'équivalence et un exporteur de qualité publient un indicateur du
modèle comme métrique, pour qu'une alerte puisse porter sur le modèle et pas seulement sur
la machine. Tout tourne sur le poste, par Compose ; aucun serveur distant.

## Lancer

```bash
make monitorage        # l'application, l'API, le service d'embedding, et le monitorage
make monitorage-etat   # les conteneurs, les cibles de Prometheus
make monitorage-arret  # tout s'arrête ; les données de Prometheus restent sur leur volume
```

- **Avant `make verify-poste` ou une sauvegarde : `make monitorage-arret`.** Elles refusent une
  instance `tei-*` en marche.
- Grafana : `http://127.0.0.1:3000`, utilisateur `admin` ; Prometheus :
  `http://127.0.0.1:9090`. Sur l'adresse locale seule.
- **Trois fichiers de secrets hors dépôt**, mode 600, requis au lancement, dans
  `13_monitorage/.secrets/` : `grafana_mot_de_passe`, `smtp_mot_de_passe`, `pgpass_lecture`. Leurs
  chemins se changent par `GRAFANA_MOT_DE_PASSE_FICHIER`, `GRAFANA_SMTP_MOT_DE_PASSE_FICHIER`,
  `MONITORAGE_PGPASS_FICHIER`. Les données de Grafana ne durent pas (`tmpfs`) : tout ce qu'il
  montre est provisionné, et un nouveau mot de passe vaut au redémarrage suivant.
- Besoin : environ 5,3 Go de mémoire vive avec l'application, 1,5 Go de mémoire vidéo.

## Les cibles

| `job` | Cible | Ce qu'elle publie |
|---|---|---|
| `embedding` | `embedding:8082` | le service d'embedding : requêtes, durées, file, refus |
| `api` | `api:8010` | l'API du modèle : requêtes, erreurs, modèle de langage |
| `application` | `application:8020` | l'application : requêtes, erreurs de l'API, état de l'API |
| `machine` | `node-exporter:9100` | la machine ; aucun port publié |
| `qualite` | `sonde:9108` | la sonde d'équivalence, la mesure de la dernière promotion |

Scrutation 15 s, rétention 15 jours. **Seul le réseau `collecte` (`172.30.13.0/24`) lit
`/metrics` de l'API et de l'application** : deux variables du fichier de fusion, aucune ligne de
code. Le service d'embedding n'est joignable que de l'API et de la collecte ; l'application reste
hors de son réseau.

## La sonde d'équivalence

Toutes les 15 min, un échantillon fixe de l'encodage en service (10 tranches de longueur × 20
fragments) est réencodé par le service, puis confronté aux vecteurs inscrits. Le calcul est celui
de `make verify-poste` : le paquet du module 09 est chargé par son chemin, aucune copie. **Aucun
accès à la base, aucun appel à un fournisseur : gratuit.** 13 requêtes au service à chaque passage,
visibles sur le tableau « Service ».

| Métrique | Ce qu'elle dit |
|---|---|
| `modele_sonde_equivalence_cosinus_min`, `_mediane` | le cosinus du dernier passage réussi |
| `modele_sonde_tranches_sous_seuil` | les tranches sous le seuil, lu dans la configuration du 09 |
| `modele_sonde_derniere_execution_timestamp`, `modele_sonde_duree_secondes` | quand, combien de temps |
| `modele_sonde_erreurs_total{cause}` | `service_injoignable`, `echantillon_absent`, `autre` |
| `modele_sonde_encodage{encodage}` | l'encodage sondé |

**Ces jauges n'existent qu'après la première mesure** : une jauge sans valeur vaudrait 0, et
l'alerte d'équivalence partirait au démarrage. **La série est gardée** :
`09_service_embedding/mesures/equivalence/journal_sonde.jsonl`, hors dépôt : date, cosinus,
identifiants des fragments sous le seuil, durée ; aucun texte. `make verify-poste` écrase son
propre fichier de résultat, pas celui-ci.

```bash
docker exec api-modele-sonde-1 python -m monitorage mesurer   # un passage à la main, journalisé
```

## L'exporteur de qualité

`modele_derniere_mesure_hit_rate_10` et `modele_derniere_mesure_timestamp` : la mesure qui a fait
promouvoir l'encodage en service — celle du run cité par sa dernière promotion (`bench.promotions`,
`decision = 'promu'`), lue dans `bench.eval_mesures` (taux de réussite à 10, global, jeu v2, toutes
les questions), toutes les 5 minutes. Un run plus récent du même encodage (un rejeu, une autre
configuration) ne la change pas. Une promotion sans run (l'encodage 2, promu par la migration 021
le 2026-10-09) lit la référence connue de son encodage (`28aa0e60`, la mesure 8 sur le corpus v1).
Sans mesure à publier, les deux jauges disparaissent ; une base injoignable laisse la dernière
valeur. **Un chiffre qui ne bouge qu'à une promotion ou un retour arrière, pas un signal vivant.**

**Éteint sans sa configuration** (`modele_exporteur_base_activee` à 0, et les deux jauges absentes).
Pour le lire, trois choses, toutes hors dépôt ou sur le serveur :

1. une ligne `host apimanga verif_lecture 172.30.14.0/24 scram-sha-256` à `pg_hba.conf` :
   `172.30.14.0/24` est `publication_monitorage`, le seul réseau de la sonde à sortir ;
2. `MONITORAGE_LECTURE_BASE=1` et
   `MONITORAGE_DSN_LECTURE=postgresql://verif_lecture@host.docker.internal:5432/apimanga` au fichier
   d'environnement du module : **un DSN qui porte un mot de passe empêche de démarrer** ;
3. le pgpass, `13_monitorage/.secrets/pgpass_lecture`, mode 600, requis au lancement, sa ligne
   commençant par `host.docker.internal:5432:`.

La session est en lecture seule et membre de `verif_ro`, ou la lecture est refusée.

## Les tableaux de bord

`grafana/tableaux/modele.json` et `service.json`, chargés par provisionnement : **ce qu'on montre
est ce qui est versionné**. L'écran ne les modifie pas (`allowUiUpdates: false`). Pour changer un
tableau : modifier le JSON et attendre 30 s ; ou, pour le faire à l'écran, ouvrir « Modèle JSON »
dans les réglages du tableau, travailler sur une copie, et recopier le JSON dans le dépôt.

Chaque panneau dit dans sa description ce qu'il montre, sa valeur normale, ce qu'on fait si elle
bouge. Les seuils sont des séries tracées et nommées ; les états sont en toutes lettres.

## Les alertes

Grafana évalue quatre règles toutes les 30 s (`grafana/provisioning/alerting/regles.yml`) ; aucun
Alertmanager externe. Les seuils et les durées sont écrits une fois, dans ce fichier.

| Règle | Requête | Condition | Durée | Sans donnée | Pourquoi ce seuil |
|---|---|---|---|---|---|
| **A — service ou API indisponible** | `up{job=~"embedding\|api"}` | `< 1` | 1 min | Alerting | plus long qu'un redémarrage volontaire ; une cible disparue est une panne |
| **B — sorties invalides** | `sum(increase(api_modele_sortie_invalide_total[15m]))` | `> 3` | 0 | OK | quelques refus isolés sont le contrôle qui travaille ; au-delà, le modèle dérive |
| **C — coût** | `sum(increase(api_modele_llm_cout_usd_total[1h]))` | `> 0,50 $` | 0 | OK | le plafond d'un run de mesure ; **elle prévient, rien ne bloque côté API** |
| **D — équivalence** | `modele_sonde_equivalence_cosinus_min` | `< 0,999` | 20 min | OK | deux sondes de suite : un écart isolé vers 0,9988 revient sans panne |

- **A ne filtre pas sur `== 0`** : en marche normale, le résultat serait vide, et la règle
  passerait en « No Data ». Le seuil est porté par Grafana.
- **Les essais, sans coût** : A, arrêter puis relancer le service `api` ; C, seuil abaissé une fois
  qu'un coût existe ; D, seuil porté au-dessus de 1, durée réduite, le temps de l'essai, puis remis.
  **B n'a pas de geste de démonstration** : la doublure du fournisseur ne rend que des sorties
  conformes. Sa règle est lue par un test ; son déclenchement réel n'est pas éprouvé.
- **Le canal : un courriel**, par Gmail en SMTP (`smtp.gmail.com:587`, STARTTLS obligatoire). Deux
  choses hors dépôt : l'adresse, `MONITORAGE_COURRIEL`, dans le fichier d'environnement du module ;
  le mot de passe d'application, dans `13_monitorage/.secrets/smtp_mot_de_passe`, mode 600, lu par
  `GF_SMTP_PASSWORD__FILE`. **Les hooks refusent sa valeur dans un fichier suivi.** Un second
  point de contact, `webhook-essai`, servi par la sonde, reste pour les essais sans sortie : un
  compteur et une ligne de journal, le titre seul ; une ligne de `politiques.yml` le désigne.
- **L'état connu de D** : **intermittent, 16 sur 111 (≈ 14 %) au 2026-11-06, deux fragments,
  valeur identique, cause non établie** (`487295` et `491847`, 0,998797) ; la série complète est
  dans le journal de la sonde. `make verify-poste` reste vert
  dans cet état, le test passe entre 0,99 et 0,999. Une alerte D sur ces deux fragments est donc
  cet état, pas une panne nouvelle ; sur d'autres fragments, c'en est une.
- **Si A tombe** : lire `make monitorage-etat` et `/v1/sante` ; relancer le service ; si c'est le
  fournisseur, changer de profil. Une panne se voit sur le tableau « Service », panneau « Cibles ».

## Licences

Prometheus et l'exporteur de machine : Apache 2.0. Grafana : AGPL v3, usage interne, non
redistribué.
