# Sorties réelles des commandes appelées par la chaîne

Des fichiers de test : la chaîne doit lire ces sorties comme les vraies commandes les
rendent. Seulement des nombres, des identifiants et des empreintes.

| Fichier | Commande | Origine |
|---|---|---|
| `encodage_bge-m3.json` | `service_embedding.encodage bge-m3 --sortie …` (09) | bilan réel de l'encodage 1 (E2, 7 octobre) |
| `rejeu_bge-m3.json` | même commande, rejouée | bilan réel d'un rejeu : rien créé (`encodage_cree: false`) |
| `comparer_encodage_2.json` | `service_embedding.equivalence comparer --encodage 2` (09) | sortie réelle du 6 novembre (`teste_le` en plus) |
| `promotion_mesure_8.json` | `mesures_recherche.promotion --run … --sortie …` (10) | fichier de sortie réel du 10 octobre |
| `construire_v2_dry_run.txt` | `corpus.construire_v2 --dry-run` (05) | la forme de la sortie standard, aux valeurs du rejeu du 10 octobre |
| `promotion_verdict_refuse.txt` | `mesures_recherche.promotion --run …` (10) | la forme de la sortie d'erreur d'un « pas de verdict » (exception `VerdictRefuse`), chemins raccourcis |
