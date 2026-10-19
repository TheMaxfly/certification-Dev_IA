# Vérification unique du dépôt : le même verdict sur le poste et sur GitHub.
# « Tests verts » = `make verify` sans échec, les tests sautés affichés.
#
#   make verify              les modules concernés par les changements depuis
#                            origin/develop, et ceux qui en dépendent
#                            (verification/dependances.tsv) : Ruff, format, tests
#   make verify TOUT=1       tous les modules
#   make verify MODULE=05    un seul module : 01 … 10, database, demo, workflows
#   make verify-indicatif    Bandit et pip-audit sur les modules existants, jamais exigés
#   make verify-poste        la mémoire d'abord ; make verify complet, sauf si l'arbre,
#                            propre, est déjà vert (FORCER=1 pour le rejouer) ; puis les
#                            harnais du poste seul : hooks, fidélité du schéma,
#                            équivalence du service d'embedding, état d'apimanga en
#                            lecture seule, intégration Compose du 02
#
# Le moteur est verification/verifier.sh ; les tests sautés admis, avec leur raison,
# sont dans verification/sauts_attendus.tsv. Sorties (JUnit, couverture, résumé,
# verdict du dernier passage complet) : .verify/, ignoré par git.

MODULE ?=
TOUT ?=
FORCER ?=

.PHONY: verify verify-indicatif verify-poste

verify:
	@TOUT=$(TOUT) bash verification/verifier.sh verify $(MODULE)

verify-indicatif:
	@bash verification/verifier.sh indicatif $(MODULE)

verify-poste:
	@FORCER=$(FORCER) bash verification/verifier.sh poste
