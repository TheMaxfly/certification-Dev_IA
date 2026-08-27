# Grille d'arbitrage C3 — protocole du tirage

Cette grille fige le contrôle humain réalisé le **21 juillet 2026**, avant les
980 promotions `llm_review` du 24 juillet. Son empreinte SHA-256 est :

```text
10707da97d30f21fb18fd2312a0fa096f1816da577f7a174a105eb3fb1802216
```

## Population et tirage

La population était constituée des **7 434 décisions automatiques courantes**
de `manga.v_match_current`. Les 1 932 décisions alors en `needs_review` étaient
hors du tirage. L'échantillon a été tiré sans remise avec la graine
`20260719`, selon quatre strates volontairement surpondérées en risques :

| Strate | Population | Tirés | Définition |
|---|---:|---:|---|
| `historique` | 2 677 | 25 | cas `auto_k_historique_confirme` sous surveillance |
| `score_bas` | 729 | 20 | décisions de score `0.90` ou `0.93` |
| `pont` | 1 689 | 15 | décisions `kitsu_bridge` fondées sur les identifiants |
| `standard` | 2 339 | 40 | autres décisions automatiques |
| **Total** | **7 434** | **100** | |

Les requêtes de constitution, la graine et l'absence de doublon entre strates
sont définies dans `src/identity/etage_r_dossiers.py`. Le geste d'arbitrage est
décrit dans `GRILLE_ARBITRAGE_C3.md`.

## Arbitrage et résultat

Pour chaque dossier, la question était : **« la série Manga Sanctuary et le
candidat désignent-ils la même œuvre ? »**, et non simplement la même
franchise. L'arbitre disposait des titres, formes, auteurs, années, synopsis et
URL publiques des deux fiches.

Les colonnes `AVIS_LLM`, `CONFIANCE` et `JUSTIFICATION` ont été masquées avant
le jugement. Aucun outil d'IA n'a participé aux verdicts humains, qui ont été
figés avant de réafficher l'avis du juge.

- **100/100 rapprochements confirmés** : aucun faux positif détecté sur ces
  100 cas stratifiés ;
- **98/100 accords humain–LLM** : le LLM a répondu `same_work` dans 98 cas et
  `undecidable` dans 2 cas que l'humain a confirmés.

Ce résultat décrit l'échantillon de contrôle du socle automatique de 7 434
décisions. Il ne constitue ni un échantillon proportionnel ni une validation
directe des 980 promotions LLM postérieures.
