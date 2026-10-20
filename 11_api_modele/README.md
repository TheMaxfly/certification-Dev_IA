# 11 — API du modèle

> Ébauche (E3, étape 2, bloc 02). Le README complet vient avec la documentation de
> l'étape.

Une API REST qui expose le modèle d'embedding en service : une question entre, une
liste de séries classées sort, avec les passages qui les justifient. Elle reprend
exactement le classement mesuré par le banc (module 10, mesure 8 sur le corpus en
service) : ce qui est servi est ce qui a été mesuré.

Le module 02 (API de données) ne change pas : deux API distinctes, données et
inférence.

## Ce que l'API réutilise

| Module | Quoi | Comment |
|---|---|---|
| 09 | le client du service d'embedding (préfixes, refus de troncature, confrontation `/info` ↔ registre) | dépendance locale `uv`, modifiable |
| 10 | le classement : rattachement des fragments, score d'une série = son meilleur fragment, départage, limitation au catalogue | paquet chargé par son chemin (`api_modele.banc`) |
| 05 | le rattachement (`atteignabilite.py`) | chargé par le module 10, par son chemin |

Les versions dont le calcul dépend (numpy, pgvector, psycopg, typer) sont celles du
verrou du module 10 ; `tests/test_verrous.py` compare les deux verrous.

## Vérifier

```bash
make verify MODULE=11   # depuis la racine du dépôt
```

Exigences du module neuf, bloquantes dans `make verify` : Pyright strict sur `src/`,
Bandit sur `src/`, pip-audit sur le verrou, couverture mesurée.
