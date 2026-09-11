"""La definition validee le 2026-09-11, eprouvee sur du wikitexte reel.

Les fixtures reprennent les formes relevees a l'inventaire, y compris les deux
qui manquaient a la premiere ecriture du parseur.
"""

import extraction_wikipedia_fr as x

LISTE_DEFINITION = """
== Personnages ==
; {{japonais|[[Izuku Midoriya]] (Deku)|緑谷 出久 (デク)}}
: C'est le personnage principal de l'histoire et ami d'enfance de Katsuki, un
: garcon ne sans Alter qui veut devenir le meilleur super-heros de son temps.
; {{japonais|Katsuki Bakugo|爆豪 勝己}}
: Rival d'enfance du heros, au caractere explosif, il vise lui aussi le sommet
: et supporte mal d'etre rattrape par celui qu'il meprisait.
; {{japonais|All Might|オール・マイト}}
: Le symbole de la paix, heros numero un, dont la puissance decline et qui
: cherche un successeur a qui transmettre son alter.
"""

PUCE_SANS_GRAS = """
== Personnages ==
* Edward Elric : c'est le plus jeune alchimiste d'Etat, a la recherche de la pierre philosophale avec son frere Alphonse, dont il veut restaurer le corps perdu.
* Alphonse Elric : le petit frere d'Edward, dont l'ame est scellee dans une armure depuis la transmutation ratee de leur mere, et qui l'accompagne partout.
* Roy Mustang : colonel alchimiste surnomme l'alchimiste de flamme, ambitieux et protecteur envers ses subordonnes, il vise le sommet de la hierarchie militaire.
"""


def test_la_liste_de_definition_isole_et_capte_le_japonais() -> None:
    a = x.analyser(LISTE_DEFINITION)
    assert a.exploitable
    assert len(a.personnages) == 3
    assert a.personnages[0].nom == "Izuku Midoriya (Deku)"
    assert a.personnages[0].forme_japonaise == "緑谷 出久 (デク)"
    assert len(a.avec_japonais) == 3


def test_la_puce_sans_gras_est_reconnue() -> None:
    """Forme de Fullmetal Alchemist — absente de la premiere ecriture."""
    a = x.analyser(PUCE_SANS_GRAS)
    assert a.exploitable
    assert [p.nom for p in a.personnages] == [
        "Edward Elric",
        "Alphonse Elric",
        "Roy Mustang",
    ]


def test_le_motif_de_titre_est_ancre() -> None:
    """Parametre 1 : « Techniques de combat des personnages » ne compte pas."""
    assert x.MOTIF_TITRE.match("personnages")
    assert x.MOTIF_TITRE.match("personnages principaux")
    assert x.MOTIF_TITRE.match("les personnages")
    assert x.MOTIF_TITRE.match("personnages et trame principale")
    assert not x.MOTIF_TITRE.match("techniques de combat des personnages")
    assert not x.MOTIF_TITRE.match("distribution")


def test_une_section_absente_est_rejetee_avec_son_motif() -> None:
    a = x.analyser("== Synopsis ==\nUn recit.\n== Manga ==\nDes tomes.\n")
    assert not a.exploitable
    assert a.motif_rejet == "aucune section satisfaisant C1"


def test_la_prose_continue_est_comptee_a_part() -> None:
    """Parametre 4 : ni exploitable, ni ignoree."""
    a = x.analyser(
        "== Personnages ==\n"
        + "Le recit suit un groupe de lyceens dont les trajectoires se croisent "
        "au fil des chapitres, sans qu'aucun ne soit presente separement du "
        "reste, la narration preferant les decrire ensemble et par touches. " * 2
    )
    assert a.prose
    assert not a.exploitable
    assert a.motif_rejet == "prose continue, personnages non isoles"


def test_le_seuil_de_80_caracteres_separe_mention_et_description() -> None:
    """Parametre 3."""
    a = x.analyser("== Personnages ==\n* Bob : la mere de Sacchan.\n")
    assert len(a.personnages) == 1
    assert not a.personnages[0].decrit
    assert not a.exploitable


def test_le_renvoi_ne_compte_que_depuis_une_section_c1() -> None:
    """Parametre 2 : c'est ce qui ecarte les faux positifs mesures."""
    depuis_c1 = x.analyser(
        "== Personnages ==\n{{Article détaillé|Personnages de Hokuto no Ken}}\n"
    )
    assert depuis_c1.article_dedie == "Personnages de Hokuto no Ken"
    depuis_ailleurs = x.analyser(
        "== Auteur ==\n{{Article détaillé|Personnages de Love Hina}}\n"
    )
    assert depuis_ailleurs.article_dedie is None


def test_les_marqueurs_de_denouement_sont_releves() -> None:
    a = x.analyser(
        "== Personnages ==\n* Yato : dieu mineur dont on apprend qu'il "
        "se revele etre un instrument de malheur, avant qu'il ne se sacrifie.\n"
    )
    assert "se revele etre" in a.marqueurs_denouement
    assert "se sacrifie" in a.marqueurs_denouement
