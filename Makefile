# Vérification unique du dépôt : le même verdict sur le poste et sur GitHub.
# « Tests verts » = `make verify` sans échec, les tests sautés affichés.
#
#   make verify              les modules concernés par les changements depuis
#                            origin/develop, et ceux qui en dépendent
#                            (verification/dependances.tsv) : Ruff, format, tests ;
#                            et toujours la tâche statique (Ruff du 06 et de
#                            docs/modeles, actionlint, shellcheck, essais des hooks
#                            et du périmètre)
#   make verify TOUT=1       tous les modules
#   make verify MODULE=05    un seul module : 01 … 05, 07 … 14, database, demo, statique
#   make verify-indicatif    Bandit et pip-audit sur les modules existants, jamais exigés
#   make app-compose         le service d'embedding, l'API, l'application et le catalogue
#                            (l'API data du 02), conteneurisés ; trois fichiers .env
#   make app-compose-arret   les arrête
#   make app-admin-creer     le premier administrateur de l'application (mot de passe au
#                            clavier, sans écho) ; APPLICATION_ENVIRONNEMENT et
#                            APPLICATION_BASE_DSN (sans mot de passe) viennent de l'environnement
#   make demo-direct         la démonstration en direct : l'application, le Swagger de
#                            l'API et MLflow (CONSTRUIRE=1 reconstruit les images d'abord) ;
#                            demo-direct-etat dit ce qui répond, demo-direct-arret arrête
#   make monitorage          l'application et le monitorage : Prometheus, Grafana, la
#                            sonde ; monitorage-etat dit ce qui répond, monitorage-arret
#                            arrête tout (avant verify-poste ou une sauvegarde)
#   make verify-poste       la mémoire d'abord ; make verify complet, sauf si l'arbre,
#                            propre, est déjà vert (FORCER=1 pour le rejouer) ; puis les
#                            harnais du poste seul : fidélité du schéma, équivalence du
#                            service d'embedding et de l'API du modèle, état d'apimanga
#                            en lecture seule (pseudonymes des passages rendus compris),
#                            ensemble de règles de GitHub
#   make preprod-creer       la préproduction : copie de la production dans apimanga_preprod
#                            (supprimée et recréée si elle existe), contrôlée
#   make chaine CANDIDAT=…   la chaîne de livraison du modèle, en préproduction : étapes 0
#                            à 8, jamais la promotion (A_BLANC=1 : la séquence, sans rien
#                            lancer) ; chaine-promouvoir LIVRAISON=… MOTIF="…" et
#                            chaine-revenir MOTIF="…" sont les gestes de Max ; chaine-etat
#                            lit le journal de la préproduction
#   make verify-mutations    les mutations versionnées (verification/mutations/),
#                            rejouées dans un arbre jetable : chacune doit faire
#                            rougir son test (poste, avant une demande vers main ;
#                            MUTATIONS="ID …" pour quelques-unes)
#
# Le moteur est verification/verifier.sh ; les tests sautés admis, avec leur raison,
# sont dans verification/sauts_attendus.tsv. Sorties (JUnit, couverture, résumé,
# verdict du dernier passage complet) : .verify/, ignoré par git.

MODULE ?=
TOUT ?=
FORCER ?=
MUTATIONS ?=
CONSTRUIRE ?=
CANDIDAT ?=
A_BLANC ?=
LIVRAISON ?=
MOTIF ?=
# Les fichiers suivent la base : la préproduction écrit ses échantillons dans le sien.
EQUIVALENCE_DOSSIER ?= $(CURDIR)/09_service_embedding/mesures/equivalence_preprod

.PHONY: verify verify-indicatif verify-poste verify-mutations app-compose app-compose-arret app-admin-creer \
	demo-direct demo-direct-etat demo-direct-arret monitorage monitorage-etat monitorage-arret \
	preprod-creer chaine chaine-promouvoir chaine-revenir chaine-etat

verify:
	@TOUT=$(TOUT) bash verification/verifier.sh verify $(MODULE)

verify-indicatif:
	@bash verification/verifier.sh indicatif $(MODULE)

verify-poste:
	@FORCER=$(FORCER) bash verification/verifier.sh poste

# Le Compose du 11, inchangé, et le fichier de fusion du 12 (l'application et le
# catalogue, l'API data du 02) ; les trois `.env`, nommés — sans celui du 02, Compose
# refuse : il nomme le fichier absent.
APP_COMPOSE = docker compose --env-file 11_api_modele/.env --env-file 12_application/.env \
	--env-file 02_api_manga/.env \
	-f 11_api_modele/docker-compose.yml -f 12_application/compose.yaml

app-compose:
	@$(APP_COMPOSE) up -d --build

app-compose-arret:
	@$(APP_COMPOSE) down

# Le premier administrateur : la commande du module 12, dans son environnement. Le mot de
# passe est demandé au clavier, deux fois, sans écho ; jamais en argument ni en variable.
app-admin-creer:
	@cd 12_application && uv run --quiet --locked python -m application.admin creer-admin

# Le même Compose, plus le fichier du mode démonstration, et MLflow à côté.
DEMO_COMPOSE = $(APP_COMPOSE) -f 12_application/compose.demo.yaml

demo-direct:
	@CONSTRUIRE=$(CONSTRUIRE) bash demo/en_direct.sh lancer $(DEMO_COMPOSE)

demo-direct-etat:
	@bash demo/en_direct.sh etat

demo-direct-arret:
	@bash demo/en_direct.sh arreter $(DEMO_COMPOSE)

# Le monitorage, un fichier de fusion de plus. APP_COMPOSE et DEMO_COMPOSE ne le lisent pas.
# Le fichier d'environnement du 13 est facultatif : sans lui, le canal d'essai.
MONITORAGE_COMPOSE = docker compose --env-file 11_api_modele/.env --env-file 12_application/.env \
	--env-file 02_api_manga/.env \
	$(if $(wildcard 13_monitorage/.env),--env-file 13_monitorage/.env) \
	-f 11_api_modele/docker-compose.yml -f 12_application/compose.yaml -f 13_monitorage/compose.yaml

# Le journal de la sonde : hors dépôt ; il doit exister pour être monté comme un fichier.
monitorage:
	@touch 09_service_embedding/mesures/equivalence/journal_sonde.jsonl
	@$(MONITORAGE_COMPOSE) up -d --build

monitorage-etat:
	@docker ps --filter label=com.docker.compose.project=api-modele --format 'table {{.Names}}\t{{.Status}}'
	@curl -sf -m 5 http://127.0.0.1:9090/api/v1/targets | \
		jq -r '.data.activeTargets[] | [.labels.job, .health] | @tsv' || echo "Prometheus ne répond pas"

monitorage-arret:
	@$(MONITORAGE_COMPOSE) down

verify-mutations:
	@uv run --no-project python -I verification/rejouer_mutations.py $(MUTATIONS)

# La chaîne de livraison du modèle (module 14) : la préproduction seule, jamais la
# production ; le motif des gestes de Max n'a pas de valeur par défaut.
CHAINE = cd 14_chaine && EQUIVALENCE_DOSSIER="$(EQUIVALENCE_DOSSIER)" \
	uv run --quiet --locked --no-env-file python -m chaine

preprod-creer:
	@$(CHAINE) preprod

chaine:
	@$(CHAINE) lancer "$(CANDIDAT)" $(if $(A_BLANC),--a-blanc)

chaine-promouvoir:
	@$(CHAINE) promouvoir "$(LIVRAISON)" --motif "$(MOTIF)"

chaine-revenir:
	@$(CHAINE) revenir --motif "$(MOTIF)"

chaine-etat:
	@$(CHAINE) etat
