# Grille d'arbitrage — Échantillon C3 (100 décisions AUTO)

> Objectif : établir la précision de la cascade (critère ≥ 95 %) et le taux
> d'accord humain↔LLM. **Tu es l'étalon** : aucun outil IA ne participe aux
> verdicts. Durée cible : ~50 min (30 s par cas simple, 2-3 min par cas dur).

---

## 0. Préparation (5 min, une fois)

1. **Masque les colonnes du juge AVANT de commencer** (verdict LLM, confiance,
   justification) : ton verdict doit être formé *avant* de lire le sien, sinon
   le « taux d'accord » mesure ton influençabilité, pas la vérité. Dans
   LibreOffice/Excel : clic droit sur les colonnes → Masquer. Tu les
   réafficheras après le 100e cas.
2. Fige la première ligne (Affichage → Figer les volets) et élargis les
   colonnes titre/auteurs/synopsis.
3. Ouvre un navigateur à côté : la vérification se fait sur les fiches
   publiques (URL MS + URL du candidat — Wikidata `wikidata.org/wiki/<QID>`
   ou Kitsu `kitsu.app/manga/<id>`).
4. Environnement : le CSV s'ouvre où tu veux — **LibreOffice Calc ou VS Code**
   (extension Rainbow CSV ou Edit CSV). Aucune requête SQL n'est nécessaire :
   le dossier de chaque cas est déjà assemblé dans la ligne.

## 1. Le geste, cas par cas

Pour chaque ligne : **la série MS et le candidat désignent-ils la même
œuvre ?** (Pas « la même franchise » — la même œuvre.)

Ordre de vérification (arrête-toi dès que c'est net) :
1. **Titres/formes** — une des formes MS correspond-elle à une forme du
   candidat ? (Romanisations différentes d'un même titre = correspondance.)
2. **Auteurs** — même personne, même si graphiée autrement (Naoki Urasawa =
   浦沢直樹 ; « discordant » du socle est souvent une romanisation).
3. **Année** — cohérente à quelques années près (conventions différentes :
   prépublication vs tome 1). Un gros écart (>5 ans) = drapeau, pas verdict.
4. **Synopsis / nature** — même histoire ? même format (série vs one-shot,
   manga vs anime/novel) ?
5. En cas de doute persistant : ouvre les deux fiches web (30 s de plus).

## 2. Les trois verdicts (colonne VERDICT_HUMAIN)

| Verdict | Quand |
|---|---|
| `same_work` | même œuvre, quelle que soit la graphie/langue des titres |
| `different_work` | œuvres distinctes — y compris **spin-off, suite, adaptation, anthologie de la même franchise** (Beast Complex ≠ Beastars ; l'anime ≠ le manga ; le light novel ≠ son adaptation manga) |
| `undecidable` | après consultation des fiches, l'information ne permet pas de trancher — c'est un verdict légitime, pas un échec ; ne force jamais |

## 3. Pièges connus (issus de nos mesures)

- **Romanisation** : deux graphies d'un même auteur/titre ≠ discordance.
- **Franchise** : même univers, même auteur, œuvre différente → `different_work`.
  C'est LE piège des homonymes partiels (« Sister » vs « Chocotto Sister »).
- **Rééditions/intégrales** : même œuvre, édition différente → `same_work`.
- **Type** : si le candidat est un light novel/anime et MS un manga →
  `different_work` (sauf fiche MS explicitement du même type).
- Ne te fie pas au score de la cascade ni à la case d'origine : tu juges
  l'œuvre, pas le système.

## 4. Colonne NOTES (libre, mais précieuse)

Note en 5-10 mots : les cas durs (« tranché sur synopsis, titres ambigus »),
les erreurs franches (« deux œuvres, auteurs différents — faux positif »),
les bizarreries de données (« fiche MS erronée elle-même »). Ces notes
nourrissent directement le rapport E1.

## 5. Après le 100e cas (10 min)

1. Réaffiche les colonnes du juge. **Ne modifie aucun de tes verdicts** —
   les désaccords sont la mesure, pas une erreur à corriger.
2. Compte : (a) tes `same_work` / total hors tes `undecidable` → **précision
   cascade** ; (b) accords verdict humain = verdict LLM → **taux d'accord** ;
   (c) liste des désaccords avec tes notes.
3. Sauvegarde le CSV rempli SOUS LE MÊME NOM + suffixe `_arbitre`
   (ex. `echantillon_c3_arbitrage_arbitre.csv`), dans le même dossier
   (gitignoré). Claude Code fera le dépouillement chiffré — mais les
   verdicts sont figés avant.

## Règles d'or

- **Aucune IA dans la boucle des verdicts** (ni Claude Code, ni claude.ai,
  ni le juge lui-même) — sinon la mesure s'effondre épistémologiquement.
- `undecidable` est permis ; le forçage est interdit.
- Un verdict posé ne se retouche pas après lecture de l'avis LLM.
- En cas de fatigue : pause. 100 cas en deux sessions valent mieux que 100
  cas bâclés — le tirage est seedé, rien ne périme.
