"""Le banc de mesure de la réponse rédigée (E3, étape 3, spec 06).

    uv run --env-file .env python -m api_modele.banc_reponse <commande>

Commandes : `essai <profil> "<question>"` (un appel, coût affiché avant et après),
`estimer <profil> <jeu>` (aucun appel), `mesurer <profil> <jeu>` (le run),
`scenarios <profil> <fichier.csv>` (les scénarios de conversation). Un jeu se dit par
famille et par issue, combinables : `famille:F7,issue:hors_catalogue` ; jamais une
question désignée une par une.

Le banc appelle la route EN INTERNE (l'application dans le processus, comme les
tests et l'étape d'équivalence de `verify-poste`), sur la base réelle sous
`modele_api` et le service d'embedding réel. Il n'écrit en base que la ligne
`bench.eval_runs` de chaque run, sous le rôle qui l'écrit déjà pour le module 10
(`DATABASE_URL`).

Gardes : l'empreinte du jeu gelé avant tout run ; l'arbre propre et `HEAD` poussé
avant `mesurer` et `scenarios` (on commite d'abord, on mesure ensuite) ; le coût
estimé avant le premier appel, puis le coût réel cumulé après chaque appel, face au
plafond `COUT_PLAFOND_RUN`.

Aucun test n'appelle un fournisseur : tout appel réel passe par ce banc, à la main.
"""
