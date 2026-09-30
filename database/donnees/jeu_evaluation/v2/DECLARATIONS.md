# Jeu d'évaluation v2 — déclarations

**v2 = v1 au titre près** — seul le titre de la section 3 change ; v1 (empreinte `07a3f4f8e43350c7389a2b18f223b3e60652e475e63ae459f7478b8149f424a3`), gelée le 30/09/2026, reste en base locale, retirée : un mot dans un titre de déclaration, aucune question modifiée. — Max, 30/09/2026

## 1. Non-contamination — Max, 30/09/2026

> Les 58 questions et leurs réponses attendues ont été écrites entre le 29 et
> le 30 septembre 2026 sans qu'aucune récupération n'ait été exécutée sur le
> corpus reconstruit. Les réponses attendues ont été écrites de mémoire, puis
> confirmées par clé exacte — titre, auteur, identifiant —, jamais par
> recherche sur les mots des questions. Quatre issues « hors catalogue »
> déclarées à la rédaction se sont révélées fausses à la confirmation et ont été
> corrigées ; cette correction porte sur l'étiquette, pas sur le texte. Aucune
> question n'a été ajoutée, retirée ou reformulée en fonction d'un résultat du
> système, qui n'a produit aucun résultat depuis décembre 2025.
> — Max, 30/09/2026

## 2. Provenance des reprises de décembre — Max, 30/09/2026

> Onze questions reprennent des requêtes du banc de décembre 2025 (origine
> `decembre`, identifiant de décembre conservé) : six en F9 et F10 (D1, D3, D6,
> D7, D8, D11 → Q059, Q060, Q062, Q063, Q064, Q066) et cinq en F11 (D13, D21,
> D22, D23, D24 → Q067 à Q071). Leur texte est celui de décembre, inchangé.
> Elles ont été écrites pour l'ancien corpus, où 6 % des critiques étaient
> présentes ; D13–D24 ont été créées le 28 décembre 2025 à 04 h 02, après la
> construction des index FAISS — qu'elles aient été ajustées au système ne se
> prouve pas, le contraire non plus. Aucune n'a été exécutée sur le corpus
> reconstruit. Les qrels de décembre, dont la production n'est pas documentée,
> n'ont pas été utilisés : les attendus de F9 et F10 sont dérivés de règles SQL
> versionnées (`regles/`) ; ceux de F11 ont été **proposés par un assistant,
> mis en forme avec un assistant, tranchés par Max le 30/09/2026**.
> — Max, 30/09/2026

## 3. Lectures de documents du corpus — l'assistant de développement, 30/09/2026

Aucune récupération n'a été lancée. Des documents du corpus `bench` ont été lus
ou comptés, sans requête ni classement du système, pour des contrôles nommés :

- présence au corpus des quatre œuvres hors catalogue (Q055–Q058) ;
- le fait affirmé par la note de Q040 (« shogi » dans les critiques des deux
  séries) ;
- le contrôle §8.2 (critiques des séries attendues en F3, fréquence des mots
  dans les critiques) ;
- la vérification des sept séries F3 sans critique, et le tableau de diversité
  des références F11 (nombre de critiques et de synopsis par série) ;
- le marqueur « synopsis anglais seulement » (présence de documents, calculée).

Les trois premières lectures ont été consignées par Max telles quelles
(ETAT §65.7) ; les deux suivantes sont de même nature — des comptes de
documents, jamais un résultat du système.
