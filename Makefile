# Vérification unique du dépôt : le même verdict sur le poste et sur GitHub.
# « Tests verts » = `make verify` sans échec, les tests sautés affichés.
#
#   make verify              tous les modules : Ruff, format, tests ; puis actionlint
#   make verify MODULE=05    un seul module : 01 … 10, database, demo, workflows
#   make verify-indicatif    Bandit et pip-audit sur les modules existants, jamais exigés
#   make verify-poste        make verify, puis les harnais du poste seul : hooks,
#                            fidélité du schéma, intégration Compose du 02
#
# Le moteur est verification/verifier.sh ; les tests sautés admis, avec leur raison,
# sont dans verification/sauts_attendus.tsv. Sorties (JUnit, couverture, résumé) :
# .verify/, ignoré par git.

MODULE ?=

.PHONY: verify verify-indicatif verify-poste

verify:
	@bash verification/verifier.sh verify $(MODULE)

verify-indicatif:
	@bash verification/verifier.sh indicatif $(MODULE)

verify-poste:
	@bash verification/verifier.sh poste
