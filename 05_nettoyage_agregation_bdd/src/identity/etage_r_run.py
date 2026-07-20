"""Étage R, jalon R-b — le pilote de run : étalonnage, puis file.

    uv run --env-file .env python -m identity.etage_r_run preparer
    uv run --env-file .env python -m identity.etage_r_run valider
    uv run --env-file .env python -m identity.etage_r_run etalonnage

CE QUE FAIT CE MODULE. Il prend les cas d'étalonnage seedés (`etage_r_dossiers`),
les rend en dossiers dans EXACTEMENT le même format que la vraie file (le juge
doit lire la même matière, sinon la mesure ne dit rien de la file), les soumet à
un ou plusieurs modèles, note les verdicts contre la vérité connue, et décide de
la poursuite. **Le seul écrit est `manga.llm_avis`** — régime avis-seulement.

DOUBLE ÉTALONNAGE. On compare plusieurs modèles sur les MÊMES 60 cas seedés. Un
score global de 95 % ne suffit pas : sur 60 cas, trois erreurs le donnent encore.
La règle de poursuite est double — exactitude hors `undecidable` ≥ 95 % ET
**aucun faux `same_work` en confiance haute** (un seul disqualifie un juge
autonome). Chaque modèle est un « run » distinct (`run_ts` propre) : la
comparaison se fait après coup, par `custom_id`.

ENVELOPPE. Batch OpenAI `/v1/chat/completions`, sorties structurées `strict`,
`reasoning_effort` optionnel. Avant tout lot, `valider` fait UN appel synchrone
par modèle pour confirmer que l'enveloppe passe — mieux vaut un rejet sur 1
appel que sur 120.
"""

from __future__ import annotations

import csv
import json
import sys
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import typer

from identity.etage_r_contrat import PROMPT_VERSION, identifiant
from identity.etage_r_dossiers import (
    FILE_ATTENDUE,
    RAPPORTS,
    STRATES,
    TAILLE_ETALONNAGE,
    ErreurEtageR,
    construire_dossiers,
    dsn,
    echantillonner_c3,
    fabriquer_etalonnage,
    texte_dossier,
    verifier_prerequis,
)
from identity.etage_r_juge_openai import (
    client_openai,
    compter_tokens,
    construire_requete,
)
from identity.wikidata_dump import normaliser

# Noms courts → identifiants /v1/models. Un id complet passé tel quel est accepté.
MODELES = {
    "luna": "gpt-5.6-luna",
    "sol": "gpt-5.6-sol",
    "terra": "gpt-5.6-terra",
}
MODELES_DEFAUT = "luna,terra"
EFFORT_DEFAUT = "medium"
PHASE = "etalonnage"

# Tarifs /M tokens (juillet 2026), RÉDUITS DE MOITIÉ par la Batch API. Source :
# page pricing OpenAI (confirmés hors code, jamais devinés). Pour chiffrer, pas
# pour décider — le juge du choix reste l'exactitude sur les 60 cas (§23.4).
TARIFS_BATCH = {
    "gpt-5.6-luna": {"in": 0.50 / 1e6, "out": 3.00 / 1e6},
    "gpt-5.6-terra": {"in": 1.25 / 1e6, "out": 7.50 / 1e6},
    "gpt-5.6-sol": {"in": 2.50 / 1e6, "out": 15.00 / 1e6},
}

SEUIL_EXACTITUDE = 0.95

app = typer.Typer(add_completion=False, help=__doc__)


def resoudre_modele(nom: str) -> str:
    return MODELES.get(nom, nom)


# --------------------------------------------------------------------------- #
# Signaux — purs, testables sans base
# --------------------------------------------------------------------------- #
def calc_signal_auteur(ms_norm: set[str], cand_norm: set[str] | None) -> str:
    """Le signal le plus fiable du prompt. `incomparable` si un côté manque —
    ni concordance ni discordance ne peuvent être affirmées."""
    if not ms_norm or not cand_norm:
        return "incomparable"
    return "concordant" if ms_norm & cand_norm else "discordant"


def calc_ecart_annee(ms_year: int | None, cand_year: int | None) -> int | None:
    if ms_year is None or cand_year is None:
        return None
    return ms_year - cand_year


def assembler_dossier(cas: dict, serie: dict, candidat: dict) -> dict:
    """Construit un dossier dans le format EXACT de la file (`texte_dossier`).

    La vérité (`attendu`) n'entre JAMAIS dans le dossier : le juge ne doit pas
    la voir. Elle voyage à côté, pour la notation.
    """
    ms_norm = {n for n in (serie.get("auteurs_norm") or set()) if n}
    cand_norm = candidat.get("auteurs_norm")
    return {
        "series_id": cas["series_id"],
        "origine": PHASE,
        "method": cas["fabrication"],
        "score": None,
        "cas": cas["fabrication"],
        "ms": {
            "titre": serie["titre"],
            "formes": serie.get("formes") or "",
            "auteurs": serie.get("auteurs") or "",
            "annee": serie.get("annee"),
            "synopsis": serie.get("synopsis") or "",
        },
        "candidats": [
            {
                "type": candidat["type"],
                "id": candidat["id"],
                "label": candidat.get("label") or "",
                "annee": candidat.get("annee"),
                "contexte": candidat.get("contexte") or "",
                "formes": candidat.get("formes") or "",
                "auteurs": candidat.get("auteurs") or "",
                "signal_auteur": calc_signal_auteur(ms_norm, cand_norm),
                "ecart_annee": calc_ecart_annee(
                    serie.get("annee"), candidat.get("annee")
                ),
                "confirme_par_kitsu": False,
            }
        ],
        "dossier_partiel": False,
        "pre_validation_bandes": False,
    }


# --------------------------------------------------------------------------- #
# Enrichissement du candidat — lecture base
# --------------------------------------------------------------------------- #
def enrichir_qid(cur, qid: str) -> dict:
    cur.execute(
        "SELECT p.label_principal, p.annee, p.wiki_en, "
        " (SELECT string_agg(DISTINCT wf.forme, ' | ' ORDER BY wf.forme) "
        "    FROM manga.wd_formes wf WHERE wf.qid = p.qid), "
        " (SELECT string_agg(DISTINCT waf.forme, ' | ' ORDER BY waf.forme) "
        "    FROM manga.wd_auteurs wa "
        "    JOIN manga.wd_auteurs_formes waf ON waf.auteur_qid = wa.auteur_qid "
        "    WHERE wa.qid = p.qid) "
        "FROM manga.wd_pivot p WHERE p.qid = %s",
        (qid,),
    )
    r = cur.fetchone()
    if r is None:
        return {"type": "qid", "id": qid, "auteurs_norm": set()}
    cur.execute(
        "SELECT DISTINCT waf.forme_norm FROM manga.wd_auteurs wa "
        "JOIN manga.wd_auteurs_formes waf ON waf.auteur_qid = wa.auteur_qid "
        "WHERE wa.qid = %s",
        (qid,),
    )
    return {
        "type": "qid",
        "id": qid,
        "label": r[0],
        "annee": r[1],
        "contexte": r[2],
        "formes": r[3],
        "auteurs": r[4],
        "auteurs_norm": {n for (n,) in cur.fetchall() if n},
    }


def enrichir_kitsu(cur, kitsu_id: str) -> dict:
    kid = int(kitsu_id)
    cur.execute(
        "SELECT "
        " (SELECT kf.forme FROM manga.kitsu_formes kf "
        "    WHERE kf.kitsu_id = %s AND kf.forme_type = 'canonical' LIMIT 1), "
        " (SELECT km.annee FROM manga.kitsu_meta km WHERE km.kitsu_id = %s), "
        " (SELECT km.subtype FROM manga.kitsu_meta km WHERE km.kitsu_id = %s), "
        " (SELECT string_agg(DISTINCT kf.forme, ' | ' ORDER BY kf.forme) "
        "    FROM manga.kitsu_formes kf WHERE kf.kitsu_id = %s), "
        " (SELECT string_agg(DISTINCT ks.personne, ' | ' ORDER BY ks.personne) "
        "    FROM manga.kitsu_staff ks WHERE ks.kitsu_id = %s)",
        (kid, kid, kid, kid, kid),
    )
    r = cur.fetchone()
    cur.execute(
        "SELECT DISTINCT ks.personne_norm FROM manga.kitsu_staff ks "
        "WHERE ks.kitsu_id = %s",
        (kid,),
    )
    return {
        "type": "kitsu_id",
        "id": kitsu_id,
        "label": r[0],
        "annee": r[1],
        "contexte": r[2],
        "formes": r[3],
        "auteurs": r[4],
        "auteurs_norm": {n for (n,) in cur.fetchall() if n},
    }


def enrichir_candidat(cur, cas: dict) -> dict:
    """Choisit le candidat à juger : QID d'abord (Wikidata), sinon Kitsu."""
    if cas.get("qid"):
        return enrichir_qid(cur, cas["qid"])
    return enrichir_kitsu(cur, str(cas["kitsu_id"]))


def charger_serie(cur, cas: dict) -> dict:
    """Complète le côté MS : formes, synopsis, auteurs normalisés."""
    cur.execute(
        "SELECT s.series_scenariste, s.series_dessinateur, "
        "       left(coalesce(s.series_synopsis, ''), 600), "
        "       (SELECT string_agg(DISTINCT mf.forme, ' | ' ORDER BY mf.forme) "
        "          FROM manga.ms_formes mf WHERE mf.series_id = s.series_id) "
        "FROM manga.ms_series_enriched s WHERE s.series_id = %s",
        (cas["series_id"],),
    )
    r = cur.fetchone()
    auteurs_norm = {
        norme for brut in (r[0], r[1]) if brut and (norme := normaliser(brut))
    }
    return {
        "titre": cas["titre"],
        "auteurs": cas["auteurs"],
        "annee": cas["annee"],
        "synopsis": r[2],
        "formes": r[3],
        "auteurs_norm": auteurs_norm,
    }


def preparer_dossiers(cur, taille: int, graine: int) -> list[dict]:
    """Les cas seedés → dossiers jugeables, vérité gardée à côté."""
    cas_list = fabriquer_etalonnage(cur, taille, graine)
    prepares = []
    for cas in cas_list:
        serie = charger_serie(cur, cas)
        candidat = enrichir_candidat(cur, cas)
        dossier = assembler_dossier(cas, serie, candidat)
        prepares.append(
            {
                "custom_id": identifiant(
                    PHASE, cas["series_id"], candidat["type"], candidat["id"]
                ),
                "attendu": cas["attendu"],
                "fabrication": cas["fabrication"],
                "texte": texte_dossier(dossier),
                "series_id": cas["series_id"],
                "candidat_type": candidat["type"],
                "candidat_id": candidat["id"],
            }
        )
    return prepares


# --------------------------------------------------------------------------- #
# Notation — pure
# --------------------------------------------------------------------------- #
def noter(resultats: list[dict]) -> dict:
    """resultats : [{attendu, verdict, confiance}]. Métriques + poursuite."""
    n = len(resultats)
    juges = [r for r in resultats if r["verdict"] in ("same_work", "different_work")]
    corrects = [r for r in juges if r["verdict"] == r["attendu"]]
    faux_same = [
        r
        for r in resultats
        if r["attendu"] == "different_work" and r["verdict"] == "same_work"
    ]
    faux_same_haute = [r for r in faux_same if r["confiance"] == "haute"]
    faux_diff = [
        r
        for r in resultats
        if r["attendu"] == "same_work" and r["verdict"] == "different_work"
    ]
    indecis = [r for r in resultats if r["verdict"] == "undecidable"]
    exactitude = len(corrects) / len(juges) if juges else 0.0
    return {
        "total": n,
        "juges": len(juges),
        "undecidable": len(indecis),
        "corrects": len(corrects),
        "exactitude_hors_undecidable": exactitude,
        "faux_same_work": len(faux_same),
        "faux_same_work_haute": len(faux_same_haute),
        "faux_different_work": len(faux_diff),
        "poursuite": exactitude >= SEUIL_EXACTITUDE and len(faux_same_haute) == 0,
    }


def comparer(par_modele: dict[str, dict[str, dict]]) -> dict:
    """Écart inter-modèles : sur combien de cas les verdicts diffèrent."""
    modeles = list(par_modele)
    if len(modeles) != 2:
        return {}
    a, b = modeles
    communs = set(par_modele[a]) & set(par_modele[b])
    desaccords = [
        cid
        for cid in communs
        if par_modele[a][cid]["verdict"]["verdict"]
        != par_modele[b][cid]["verdict"]["verdict"]
    ]
    return {"modeles": (a, b), "communs": len(communs), "desaccords": desaccords}


# --------------------------------------------------------------------------- #
# Réseau — appels
# --------------------------------------------------------------------------- #
def valider_enveloppe(client, modele: str, effort: str, texte: str) -> dict:
    """UN appel synchrone : confirme que sorties structurées + reasoning_effort
    passent sur ce modèle, avant d'engager un lot de 60."""
    corps = construire_requete("validation", texte, modele=modele)["body"]
    if effort:
        corps["reasoning_effort"] = effort
    reponse = client.chat.completions.create(**corps)
    verdict = json.loads(reponse.choices[0].message.content)
    usage = reponse.usage
    return {
        "verdict": verdict,
        "tokens_in": getattr(usage, "prompt_tokens", None),
        "tokens_out": getattr(usage, "completion_tokens", None),
    }


def soumettre_batch(client, modele: str, effort: str, prepares: list[dict], dest: Path):
    """Écrit le JSONL, l'envoie, crée le lot. Rend l'id du lot."""
    lignes = []
    for p in prepares:
        requete = construire_requete(p["custom_id"], p["texte"], modele=modele)
        if effort:
            requete["body"]["reasoning_effort"] = effort
        lignes.append(requete)
    chemin = dest / f"lot_{modele}.jsonl"
    chemin.parent.mkdir(parents=True, exist_ok=True)
    with chemin.open("w", encoding="utf-8") as fh:
        for ligne in lignes:
            fh.write(json.dumps(ligne, ensure_ascii=False) + "\n")
    with chemin.open("rb") as fh:
        fichier = client.files.create(file=fh, purpose="batch")
    lot = client.batches.create(
        input_file_id=fichier.id,
        endpoint="/v1/chat/completions",
        completion_window="24h",
        metadata={"phase": PHASE, "modele": modele},
    )
    return lot.id


def attendre_et_collecter(
    client, lots: dict[str, str], attente_max: int, intervalle: int, echo
) -> dict[str, dict] | None:
    """Sonde TOUS les lots contre une seule échéance partagée, jamais l'un après
    l'autre (sinon l'attente totale serait n × attente_max). Rend les résultats
    par modèle si tous sont terminés, sinon None (run à reprendre)."""
    resultats: dict[str, dict] = {}
    restants = dict(lots)
    debut = time.monotonic()
    while restants:
        for modele, batch_id in list(restants.items()):
            lot = client.batches.retrieve(batch_id)
            if lot.status == "completed":
                resultats[modele] = collecter_batch(client, lot)
                echo(f"lot collecté : {modele} ({len(resultats[modele])})")
                del restants[modele]
            elif lot.status in ("failed", "expired", "cancelled"):
                raise ErreurEtageR(
                    f"Lot {modele} en statut {lot.status} (id {batch_id}) — STOP."
                )
        if restants:
            if time.monotonic() - debut > attente_max:
                echo(f"⏳ encore en cours : {', '.join(restants)}.")
                return None
            time.sleep(intervalle)
    return resultats


def collecter_batch(client, lot) -> dict[str, dict]:
    """Rattache CHAQUE réponse par `custom_id` — jamais par position."""
    if lot.status != "completed" or not lot.output_file_id:
        raise ErreurEtageR(
            f"Lot {lot.id} non collectable — statut {lot.status}. "
            + (f"Erreurs : {lot.error_file_id}" if lot.error_file_id else "")
        )
    contenu = client.files.content(lot.output_file_id).text
    resultats: dict[str, dict] = {}
    for ligne in contenu.splitlines():
        if not ligne.strip():
            continue
        obj = json.loads(ligne)
        corps = obj["response"]["body"]
        message = corps["choices"][0]["message"]
        verdict = json.loads(message["content"])
        usage = corps.get("usage", {})
        resultats[obj["custom_id"]] = {
            "verdict": verdict,
            "tokens_in": usage.get("prompt_tokens"),
            "tokens_out": usage.get("completion_tokens"),
        }
    return resultats


# --------------------------------------------------------------------------- #
# Écriture — le SEUL écrit : llm_avis
# --------------------------------------------------------------------------- #
def ecrire_avis(
    cur, modele: str, run_ts, phase: str, prepares: list[dict], resultats: dict
):
    index = {p["custom_id"]: p for p in prepares}
    for cid, res in resultats.items():
        p = index[cid]
        v = res["verdict"]
        cur.execute(
            "INSERT INTO manga.llm_avis "
            "(series_id, run_ts, phase, candidat_type, candidat_id, verdict, "
            " confiance, justification, modele, prompt_version, tokens_in, "
            " tokens_out, pre_validation_bandes, dossier_partiel) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                p["series_id"],
                run_ts,
                phase,
                p["candidat_type"],
                p["candidat_id"],
                v["verdict"],
                v["confiance"],
                v["justification"],
                modele,
                PROMPT_VERSION,
                res.get("tokens_in"),
                res.get("tokens_out"),
                p.get("pre_validation_bandes", False),
                p.get("dossier_partiel", False),
            ),
        )


# --------------------------------------------------------------------------- #
# Sorties
# --------------------------------------------------------------------------- #
def _dest(rapport_dir: str | None, prefixe: str) -> Path:
    if rapport_dir:
        return Path(rapport_dir)
    return RAPPORTS / (prefixe + "_" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))


def _lignes_rapport(mesures: dict, comparaison: dict, cout: dict) -> list[str]:
    lignes = [
        "# Double étalonnage — étage R (jalon R-b)",
        "",
        f"prompt_version : `{PROMPT_VERSION}` · phase : `{PHASE}` · seuil "
        f"exactitude : {SEUIL_EXACTITUDE:.0%}",
        "",
        "| Modèle | Jugés | Undecid. | Exactitude hors undecid. | "
        "Faux same_work | dont conf. haute | Poursuite |",
        "|---|---:|---:|---:|---:|---:|:--:|",
    ]
    for modele, m in mesures.items():
        verdict = "✅ OUI" if m["poursuite"] else "⛔ NON"
        lignes.append(
            f"| `{modele}` | {m['juges']} | {m['undecidable']} | "
            f"{m['exactitude_hors_undecidable']:.1%} | {m['faux_same_work']} | "
            f"**{m['faux_same_work_haute']}** | {verdict} |"
        )
    if comparaison:
        a, b = comparaison["modeles"]
        lignes += [
            "",
            f"**Écart {a} ↔ {b}** : {len(comparaison['desaccords'])} désaccords "
            f"sur {comparaison['communs']} cas communs.",
        ]
    lignes += ["", "## Coût mesuré (Batch)"]
    total = 0.0
    for modele, c in cout.items():
        lignes.append(
            f"- `{modele}` : {c['tokens_in']:,} in + {c['tokens_out']:,} out "
            f"→ **{c['cout']:.4f} $**".replace(",", " ")
        )
        total += c["cout"]
    lignes.append(f"- **total : {total:.4f} $**")
    lignes += [
        "",
        "Rappel §23.4 : le coût n'est pas le juge, l'exactitude l'est. Un seul "
        "faux `same_work` en confiance haute disqualifie un juge autonome.",
    ]
    return lignes


def _cout_modele(modele: str, resultats: dict) -> dict:
    tin = sum((r["tokens_in"] or 0) for r in resultats.values())
    tout = sum((r["tokens_out"] or 0) for r in resultats.values())
    tarif = TARIFS_BATCH.get(modele, {"in": 0.0, "out": 0.0})
    return {
        "tokens_in": tin,
        "tokens_out": tout,
        "cout": tin * tarif["in"] + tout * tarif["out"],
    }


# --------------------------------------------------------------------------- #
# La file — un verdict par candidat, puis ventilation
# --------------------------------------------------------------------------- #
def dossier_un_candidat(dossier: dict, candidat: dict) -> dict:
    """Vue du dossier réduite à UN candidat. Le juge tranche série vs ce
    candidat — comme à l'étalonnage. Le format (`texte_dossier`) est identique,
    et le drapeau `dossier_partiel` reste visible au juge."""
    return {**dossier, "candidats": [candidat]}


def preparer_file(cur) -> tuple[list[dict], dict]:
    """Assemble la file et l'ÉCLATE en une requête par candidat : le schéma
    tranche une identité à la fois. Un dossier à N candidats → N verdicts."""
    dossiers, mesures = construire_dossiers(cur, FILE_ATTENDUE)
    records = []
    for d in dossiers:
        n = len(d["candidats"])
        for c in d["candidats"]:
            records.append(
                {
                    "custom_id": identifiant(
                        "file", d["series_id"], c["type"], c["id"]
                    ),
                    "texte": texte_dossier(dossier_un_candidat(d, c)),
                    "series_id": d["series_id"],
                    "candidat_type": c["type"],
                    "candidat_id": c["id"],
                    "origine": d["origine"],
                    "cas": d["cas"] or d["origine"],
                    "pre_validation_bandes": d["pre_validation_bandes"],
                    "dossier_partiel": d["dossier_partiel"],
                    "n_candidats": n,
                }
            )
    return records, mesures


_COLS = [
    "same_work/haute",
    "same_work/moyenne",
    "different_work/haute",
    "different_work/moyenne",
    "undecidable",
]


def _distrib(records: list[dict]) -> Counter:
    c: Counter = Counter()
    for r in records:
        if r["verdict"] == "undecidable":
            c["undecidable"] += 1
        else:
            c[f"{r['verdict']}/{r['confiance']}"] += 1
    return c


def ventiler_file(modele: str, records: list[dict]) -> list[str]:
    """verdict × confiance × cas d'origine ; le seau et les partiels à part ;
    et le taux de « faux candidats francs » (candidat UNIQUE jugé different_work
    en confiance haute) — la mesure de la qualité du filet de la cascade."""
    series = {r["series_id"] for r in records}
    lignes = [
        "# Verdicts de la file — étage R (jalon R-b)",
        "",
        f"modèle : `{modele}` · phase : `file` · prompt_version : `{PROMPT_VERSION}`",
        f"candidats jugés : **{len(records)}** sur **{len(series)}** séries",
        "",
        "## Ventilation par cas d'origine (verdict × confiance)",
        "",
        "| cas d'origine | n | " + " | ".join(_COLS) + " | faux cand. francs |",
        "|---|" + "---:|" * (len(_COLS) + 1) + ":--:|",
    ]
    par_cas: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        par_cas[r["cas"]].append(r)
    for cas in sorted(par_cas):
        sous = par_cas[cas]
        d = _distrib(sous)
        uniques = [r for r in sous if r["n_candidats"] == 1]
        francs = [
            r
            for r in uniques
            if r["verdict"] == "different_work" and r["confiance"] == "haute"
        ]
        taux = f"{len(francs)}/{len(uniques)}" if uniques else "—"
        lignes.append(
            f"| {cas} | {len(sous)} | "
            + " | ".join(str(d.get(c, 0)) for c in _COLS)
            + f" | {taux} |"
        )

    for nom, sous in (
        (
            "seau adjacent (pré-validation des bandes §26)",
            [r for r in records if r["pre_validation_bandes"]],
        ),
        ("dossier_partiel (§28.1)", [r for r in records if r["dossier_partiel"]]),
    ):
        d = _distrib(sous)
        s = len({r["series_id"] for r in sous})
        lignes += [
            "",
            f"## {nom} — {len(sous)} candidats / {s} séries",
            "",
            "| " + " | ".join(_COLS) + " |",
            "|" + "---:|" * len(_COLS),
            "| " + " | ".join(str(d.get(c, 0)) for c in _COLS) + " |",
        ]

    lignes += [
        "",
        "**Faux candidats francs** = dossier à candidat UNIQUE jugé "
        "`different_work` en confiance haute : la cascade n'a proposé qu'un "
        "candidat, clairement faux — elle a eu raison de NE PAS l'auto-valider. "
        "Le taux par cas mesure la qualité du filet (ce que le seuil a retenu à "
        "raison).",
        "",
        "Régime **avis-seulement** : rien n'écrit dans `match_decision`. La "
        "promotion des verdicts est une décision humaine sur ces mesures.",
        "",
    ]
    return lignes


# --------------------------------------------------------------------------- #
# L'échantillon C3 — contrôle humain des AUTO (la strate pont, mesure réelle)
# --------------------------------------------------------------------------- #
def preparer_echantillon(cur, graine: int) -> list[dict]:
    """Les 100 AUTO stratifiés → dossiers (série vs SON candidat auto). Pas de
    vérité : c'est un contrôle. La strate pont y est désormais une mesure réelle
    (l'étalonnage a montré qu'elle n'est pas 100 %)."""
    cases = echantillonner_c3(cur, STRATES, graine)
    records = []
    for e in cases:
        cur.execute(
            "SELECT wikidata_qid, kitsu_id FROM manga.work_identity "
            "WHERE series_id = %s",
            (e["series_id"],),
        )
        row = cur.fetchone()
        qid, kitsu_id = row if row else (None, None)
        cur.execute(
            "SELECT series_title, "
            "concat_ws(' / ', series_scenariste, series_dessinateur), "
            "series_year FROM manga.ms_series_enriched WHERE series_id = %s",
            (e["series_id"],),
        )
        titre, auteurs, annee = cur.fetchone()
        cas = {
            "series_id": e["series_id"],
            "titre": titre,
            "auteurs": auteurs,
            "annee": annee,
            "qid": qid,
            "kitsu_id": kitsu_id,
            "fabrication": e["strate"],
        }
        serie = charger_serie(cur, cas)
        candidat = enrichir_candidat(cur, cas)
        dossier = assembler_dossier(cas, serie, candidat)
        records.append(
            {
                "custom_id": identifiant(
                    "echantillon", e["series_id"], candidat["type"], candidat["id"]
                ),
                "texte": texte_dossier(dossier),
                "series_id": e["series_id"],
                "candidat_type": candidat["type"],
                "candidat_id": candidat["id"],
                "strate": e["strate"],
                "method": e["method"],
                "score": e["score"],
                "cas": e["cas"],
            }
        )
    return records


def ecrire_csv_arbitrage(chemin: Path, records: list[dict]) -> None:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    with chemin.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "series_id",
                "strate",
                "method",
                "score",
                "cas",
                "candidat_type",
                "candidat_id",
                "AVIS_LLM",
                "CONFIANCE",
                "JUSTIFICATION",
                "VERDICT_HUMAIN",
            ]
        )
        for r in sorted(records, key=lambda x: (x["strate"], x["series_id"])):
            w.writerow(
                [
                    r["series_id"],
                    r["strate"],
                    r["method"],
                    r["score"],
                    r["cas"],
                    r["candidat_type"],
                    r["candidat_id"],
                    r.get("verdict", ""),
                    r.get("confiance", ""),
                    r.get("justification", ""),
                    "",
                ]
            )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
@app.command()
def preparer(
    rapport_dir: str = typer.Option(None),  # noqa: B008
    taille: int = typer.Option(TAILLE_ETALONNAGE),  # noqa: B008
    graine: int = typer.Option(20260719),  # noqa: B008
    modeles: str = typer.Option(MODELES_DEFAUT),  # noqa: B008
) -> None:
    """Assemble les dossiers d'étalonnage et chiffre le coût RÉEL (tiktoken).

    Lecture seule, ZÉRO réseau : rien n'est envoyé, rien n'est écrit.
    """
    with psycopg.connect(dsn()) as cx, cx.cursor() as cur:
        verifier_prerequis(cur)
        prepares = preparer_dossiers(cur, taille, graine)
        cx.rollback()

    tin = sum(compter_tokens(p["texte"]) for p in prepares)
    tin_moyen = tin / len(prepares)
    # Sortie : bornée par le schéma + raisonnement medium (incertitude assumée).
    out_bas, out_haut = 60 * len(prepares), 1400 * len(prepares)
    typer.echo(f"cas préparés     : {len(prepares)}")
    typer.echo(f"tokens d'entrée  : {tin} (moyenne {tin_moyen:.0f}/cas)")
    for nom in modeles.split(","):
        modele = resoudre_modele(nom.strip())
        tarif = TARIFS_BATCH.get(modele)
        if not tarif:
            continue
        cb = tin * tarif["in"] + out_bas * tarif["out"]
        ch = tin * tarif["in"] + out_haut * tarif["out"]
        typer.echo(f"  {modele:16s} : {cb:.4f} $ – {ch:.4f} $ (Batch)")
    typer.echo(f"(sortie bornée {out_bas}–{out_haut} tok : raisonnement medium)")


@app.command()
def valider(
    modeles: str = typer.Option(MODELES_DEFAUT),  # noqa: B008
    effort: str = typer.Option(EFFORT_DEFAUT),  # noqa: B008
    graine: int = typer.Option(20260719),  # noqa: B008
) -> None:
    """UN appel synchrone par modèle : l'enveloppe passe-t-elle ? (~0,001 $/appel)"""
    with psycopg.connect(dsn()) as cx, cx.cursor() as cur:
        verifier_prerequis(cur)
        prepares = preparer_dossiers(cur, TAILLE_ETALONNAGE, graine)
        cx.rollback()
    client = client_openai()
    echantillon = prepares[0]["texte"]
    for nom in modeles.split(","):
        modele = resoudre_modele(nom.strip())
        res = valider_enveloppe(client, modele, effort, echantillon)
        v = res["verdict"]
        typer.echo(
            f"{modele:16s} → {v['verdict']}/{v['confiance']} "
            f"({res['tokens_in']} in, {res['tokens_out']} out) OK"
        )
    typer.echo("Enveloppe validée : structured outputs + reasoning_effort OK.")


@app.command()
def etalonnage(
    rapport_dir: str = typer.Option(None),  # noqa: B008
    modeles: str = typer.Option(MODELES_DEFAUT),  # noqa: B008
    effort: str = typer.Option(EFFORT_DEFAUT),  # noqa: B008
    graine: int = typer.Option(20260719),  # noqa: B008
    attente_max: int = typer.Option(540),  # noqa: B008
    intervalle: int = typer.Option(20),  # noqa: B008
    ecrire: bool = typer.Option(True),  # noqa: B008
) -> None:
    """Soumet le double étalonnage en Batch, note, décide, écrit les avis."""
    dest = _dest(rapport_dir, "etalonnage")
    dest.mkdir(parents=True, exist_ok=True)
    with psycopg.connect(dsn()) as cx, cx.cursor() as cur:
        verifier_prerequis(cur)
        prepares = preparer_dossiers(cur, TAILLE_ETALONNAGE, graine)
        cx.rollback()
    typer.echo(f"dossiers préparés : {len(prepares)} (graine {graine})")

    client = client_openai()
    noms = [resoudre_modele(n.strip()) for n in modeles.split(",")]

    # 1) Soumission de tous les lots d'abord (ils tournent en parallèle).
    lots = {}
    for modele in noms:
        lots[modele] = soumettre_batch(client, modele, effort, prepares, dest)
        typer.echo(f"lot soumis : {modele} → {lots[modele]}")
    (dest / "lots.json").write_text(json.dumps(lots, indent=2), encoding="utf-8")

    # 2) Attente + collecte — échéance partagée.
    resultats = attendre_et_collecter(client, lots, attente_max, intervalle, typer.echo)
    if resultats is None:
        typer.echo(f"Reprise : `collecter --lots {dest}/lots.json`.")
        return
    _finaliser(dest, prepares, resultats, ecrire)


@app.command()
def collecter(
    lots: str = typer.Option(...),  # noqa: B008
    graine: int = typer.Option(20260719),  # noqa: B008
    attente_max: int = typer.Option(60),  # noqa: B008
    intervalle: int = typer.Option(20),  # noqa: B008
    ecrire: bool = typer.Option(True),  # noqa: B008
) -> None:
    """Reprend un run Batch soumis : collecte, note, décide, écrit."""
    chemin_lots = Path(lots)
    dest = chemin_lots.parent
    ids = json.loads(chemin_lots.read_text(encoding="utf-8"))
    with psycopg.connect(dsn()) as cx, cx.cursor() as cur:
        verifier_prerequis(cur)
        prepares = preparer_dossiers(cur, TAILLE_ETALONNAGE, graine)
        cx.rollback()
    client = client_openai()
    resultats = attendre_et_collecter(client, ids, attente_max, intervalle, typer.echo)
    if resultats is None:
        typer.echo(f"Pas encore terminé. Réessayez : `collecter --lots {lots}`.")
        return
    _finaliser(dest, prepares, resultats, ecrire)


def _finaliser(dest: Path, prepares, resultats_par_modele, ecrire: bool) -> None:
    index = {p["custom_id"]: p for p in prepares}
    mesures, cout = {}, {}
    for modele, resultats in resultats_par_modele.items():
        notes = [
            {
                "attendu": index[cid]["attendu"],
                "verdict": res["verdict"]["verdict"],
                "confiance": res["verdict"]["confiance"],
            }
            for cid, res in resultats.items()
        ]
        mesures[modele] = noter(notes)
        cout[modele] = _cout_modele(modele, resultats)
    comparaison = comparer(resultats_par_modele)
    lignes = _lignes_rapport(mesures, comparaison, cout)
    (dest / "rapport.md").write_text("\n".join(lignes), encoding="utf-8")

    if ecrire:
        with psycopg.connect(dsn()) as cx:
            with cx.cursor() as cur:
                verifier_prerequis(cur)
                for i, (modele, resultats) in enumerate(resultats_par_modele.items()):
                    run_ts = datetime.now(UTC).replace(microsecond=i)
                    ecrire_avis(cur, modele, run_ts, PHASE, prepares, resultats)
            cx.commit()

    for ligne in lignes:
        typer.echo(ligne)
    typer.echo(f"\nLivrable : {dest}/rapport.md")


def _attacher_verdicts(records: list[dict], resultats: dict) -> None:
    for r in records:
        v = resultats[r["custom_id"]]["verdict"]
        r["verdict"] = v["verdict"]
        r["confiance"] = v["confiance"]
        r["justification"] = v.get("justification", "")


@app.command()
def file(
    rapport_dir: str = typer.Option(None),  # noqa: B008
    modele: str = typer.Option("luna"),  # noqa: B008
    effort: str = typer.Option(EFFORT_DEFAUT),  # noqa: B008
    attente_max: int = typer.Option(540),  # noqa: B008
    intervalle: int = typer.Option(20),  # noqa: B008
    ecrire: bool = typer.Option(True),  # noqa: B008
) -> None:
    """Juge la file (1 932) sous le modèle retenu, un verdict par candidat.

    Écrit les avis (phase='file'), ventile dans verdicts_file.md. AVIS-SEULEMENT :
    rien n'écrit dans match_decision.
    """
    modele = resoudre_modele(modele)
    dest = _dest(rapport_dir, "file")
    dest.mkdir(parents=True, exist_ok=True)
    with psycopg.connect(dsn()) as cx, cx.cursor() as cur:
        verifier_prerequis(cur)
        records, mesures = preparer_file(cur)
        cx.rollback()
    typer.echo(f"file : {mesures['file']} dossiers, {len(records)} candidats à juger")

    client = client_openai()
    lots = {modele: soumettre_batch(client, modele, effort, records, dest)}
    (dest / "lots.json").write_text(json.dumps(lots, indent=2), encoding="utf-8")
    typer.echo(f"lot soumis : {modele} → {lots[modele]}")

    resultats = attendre_et_collecter(client, lots, attente_max, intervalle, typer.echo)
    if resultats is None:
        typer.echo(f"Reprise : `collecter --lots {dest}/lots.json` (phase file).")
        return
    _attacher_verdicts(records, resultats[modele])

    if ecrire:
        with psycopg.connect(dsn()) as cx:
            with cx.cursor() as cur:
                verifier_prerequis(cur)
                ecrire_avis(
                    cur, modele, datetime.now(UTC), "file", records, resultats[modele]
                )
            cx.commit()

    lignes = ventiler_file(modele, records)
    (dest / "verdicts_file.md").write_text("\n".join(lignes), encoding="utf-8")
    cout = _cout_modele(modele, resultats[modele])
    for ligne in lignes:
        typer.echo(ligne)
    typer.echo(f"coût file (Batch) : {cout['cout']:.4f} $")
    typer.echo(f"Livrable : {dest}/verdicts_file.md")


@app.command()
def echantillon(
    rapport_dir: str = typer.Option(None),  # noqa: B008
    modele: str = typer.Option("luna"),  # noqa: B008
    effort: str = typer.Option(EFFORT_DEFAUT),  # noqa: B008
    graine: int = typer.Option(20260719),  # noqa: B008
    attente_max: int = typer.Option(300),  # noqa: B008
    intervalle: int = typer.Option(20),  # noqa: B008
    ecrire: bool = typer.Option(True),  # noqa: B008
) -> None:
    """Juge l'échantillon C3 (100 AUTO stratifiés) sous le modèle retenu.

    Écrit les avis (phase='echantillon') et livre echantillon_c3_arbitrage.csv
    pour l'arbitrage humain. AVIS-SEULEMENT.
    """
    modele = resoudre_modele(modele)
    dest = _dest(rapport_dir, "echantillon")
    dest.mkdir(parents=True, exist_ok=True)
    with psycopg.connect(dsn()) as cx, cx.cursor() as cur:
        verifier_prerequis(cur)
        records = preparer_echantillon(cur, graine)
        cx.rollback()
    typer.echo(f"échantillon C3 : {len(records)} AUTO à contrôler")

    client = client_openai()
    lots = {modele: soumettre_batch(client, modele, effort, records, dest)}
    (dest / "lots.json").write_text(json.dumps(lots, indent=2), encoding="utf-8")
    typer.echo(f"lot soumis : {modele} → {lots[modele]}")

    resultats = attendre_et_collecter(client, lots, attente_max, intervalle, typer.echo)
    if resultats is None:
        typer.echo(f"Reprise : `collecter --lots {dest}/lots.json`.")
        return
    _attacher_verdicts(records, resultats[modele])

    if ecrire:
        with psycopg.connect(dsn()) as cx:
            with cx.cursor() as cur:
                verifier_prerequis(cur)
                ecrire_avis(
                    cur,
                    modele,
                    datetime.now(UTC),
                    "echantillon",
                    records,
                    resultats[modele],
                )
            cx.commit()

    ecrire_csv_arbitrage(dest / "echantillon_c3_arbitrage.csv", records)
    par_strate: dict[str, Counter] = defaultdict(Counter)
    for r in records:
        par_strate[r["strate"]][r["verdict"]] += 1
    typer.echo("Verdicts par strate (contrôle) :")
    for strate in sorted(par_strate):
        d = par_strate[strate]
        typer.echo(
            f"  {strate:11s} : same={d['same_work']} diff={d['different_work']} "
            f"undecid={d['undecidable']}"
        )
    cout = _cout_modele(modele, resultats[modele])
    typer.echo(f"coût échantillon (Batch) : {cout['cout']:.4f} $")
    typer.echo(f"Livrable : {dest}/echantillon_c3_arbitrage.csv")


@app.command()
def couts(rapport_dir: str = typer.Option(None)) -> None:  # noqa: B008
    """Coût RÉEL du run R-b, lu dans llm_avis (tokens facturés, tarif Batch)."""
    dest = _dest(rapport_dir, "couts")
    with psycopg.connect(dsn()) as cx, cx.cursor() as cur:
        cur.execute(
            "SELECT phase, modele, count(*), coalesce(sum(tokens_in),0), "
            "coalesce(sum(tokens_out),0) FROM manga.llm_avis "
            "GROUP BY phase, modele ORDER BY phase, modele"
        )
        rows = cur.fetchall()
        cx.rollback()
    lignes = [
        "# Coût réel du run R-b (OpenAI Batch, tokenizer OpenAI)",
        "",
        "Tokens **facturés** relevés dans `llm_avis` ; tarifs Batch (½ prix) "
        "de la page pricing OpenAI. Mesuré, pas estimé.",
        "",
        "| phase | modèle | avis | tokens in | tokens out | coût |",
        "|---|---|---:|---:|---:|---:|",
    ]
    total = 0.0
    for phase, modele, n, tin, tout in rows:
        tarif = TARIFS_BATCH.get(modele, {"in": 0.0, "out": 0.0})
        cout = tin * tarif["in"] + tout * tarif["out"]
        total += cout
        lignes.append(
            f"| {phase} | `{modele}` | {n} | {tin:,} | {tout:,} | "
            f"{cout:.4f} $ |".replace(",", " ")
        )
    lignes += ["", f"**Total R-b : {total:.4f} $**", ""]
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "couts.md").write_text("\n".join(lignes), encoding="utf-8")
    for ligne in lignes:
        typer.echo(ligne)
    typer.echo(f"Livrable : {dest}/couts.md")


def main() -> int:
    try:
        app()
    except ErreurEtageR as erreur:
        typer.echo(f"ERREUR : {erreur}", err=True)
        return 1
    except psycopg.Error as erreur:
        typer.echo(f"ERREUR SQL : {erreur}", err=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
