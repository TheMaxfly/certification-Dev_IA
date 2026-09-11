"""Application de la definition validee le 2026-09-11.

Quatre parametres, tous consignes au document de definition :

1. titres de section : motif ancre sur les six formes reellement rencontrees ;
2. article dedie : compte, mais **le lien doit partir d'une section satisfaisant
   C1** — c'est ce qui ecarte les quatre faux positifs mesures a l'inventaire ;
3. seuil de texte : 80 caracteres apres nettoyage du balisage ;
4. prose continue : comptee a part, ni exploitable ni ignoree.

Le module lit du wikitexte, jamais du HTML rendu.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

#: Parametre 1. Ancre aux deux bouts : « Techniques de combat des personnages »
#: ne passe pas, « Personnages et trame principale » passe.
MOTIF_TITRE = re.compile(r"^(les )?personnages?( (principaux|secondaires|et .+))?$")

#: Parametre 3.
SEUIL_DESCRIPTION = 80

CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿]")
TITRE_SECTION = re.compile(r"^(={2,6})\s*(.+?)\s*\1\s*$", re.MULTILINE)
ARTICLE_DETAILLE = re.compile(
    r"\{\{\s*(?:article d[ée]taill[ée]|loupe|voir aussi)\s*\|"
    r"((?:(?!\{\{|\}\}|\|).)+)",
    re.IGNORECASE,
)

#: Marqueurs lexicaux de denouement. C'est une **appreciation**, pas une mesure
#: exacte : la liste est explicite pour que le taux produit soit relisable et
#: contestable, comme le paragraphe 5 l'exige.
MARQUEURS_DENOUEMENT = [
    "a la fin de la serie",
    "a la fin du manga",
    "en realite",
    "se revele etre",
    "il s'avere que",
    "veritable identite",
    "sa vraie identite",
    "finit par mourir",
    "meurt dans",
    "est tue par",
    "se sacrifie",
    "trahit",
    "dernier chapitre",
    "denouement",
    "ressuscite",
]


def normaliser(texte: str) -> str:
    """Minuscules, sans accents — pour comparer des titres, jamais pour stocker."""
    plat = unicodedata.normalize("NFD", texte.strip().lower())
    return "".join(c for c in plat if unicodedata.category(c) != "Mn")


def _params(corps: str) -> list[str]:
    """Decoupe les parametres d'un modele sur `|`, en ignorant les imbrications."""
    params, courant, profondeur = [], [], 0
    i = 0
    while i < len(corps):
        duo = corps[i : i + 2]
        if duo in ("{{", "[["):
            profondeur += 1
            courant.append(duo)
            i += 2
            continue
        if duo in ("}}", "]]"):
            profondeur -= 1
            courant.append(duo)
            i += 2
            continue
        if corps[i] == "|" and profondeur == 0:
            params.append("".join(courant))
            courant = []
        else:
            courant.append(corps[i])
        i += 1
    params.append("".join(courant))
    return params


def _modeles(texte: str, noms: set[str]):
    """Rend (debut, fin, parametres) de chaque modele dont le nom est vise."""
    for m in re.finditer(r"\{\{", texte):
        debut = m.start()
        profondeur, i = 0, debut
        while i < len(texte) - 1:
            duo = texte[i : i + 2]
            if duo == "{{":
                profondeur += 1
                i += 2
                continue
            if duo == "}}":
                profondeur -= 1
                i += 2
                if profondeur == 0:
                    break
                continue
            i += 1
        else:
            continue
        params = _params(texte[debut + 2 : i - 2])
        if params and normaliser(params[0]) in noms:
            yield debut, i, params


#: `{{japonais|Nom francais|漢字|romaji}}` est la forme dominante sur frwiki, et
#: c'est elle qui porte la graphie japonaise. La supprimer avec le reste du
#: balisage effacerait a la fois le nom et la meilleure cle de fusion
#: disponible — le raw Kitsu porte `names.ja_jp` sur 77,5 % des personnages.
MODELES_JAPONAIS = {"japonais", "nihongo"}
MODELES_LANGUE = {"langue", "lang"}


def deballer(texte: str) -> str:
    """Remplace les modeles porteurs de texte par leur contenu utile."""
    for noms, indice in ((MODELES_JAPONAIS, 1), (MODELES_LANGUE, 2)):
        for _ in range(6):  # imbrications rares, borne explicite
            remplace = False
            for debut, fin, params in _modeles(texte, noms):
                if len(params) > indice:
                    texte = texte[:debut] + params[indice] + texte[fin:]
                    remplace = True
                    break
            if not remplace:
                break
    return texte


def japonais_de(texte: str) -> str | None:
    """Graphie japonaise portee par un `{{japonais}}`, ou trouvee en clair."""
    for _, _, params in _modeles(texte, MODELES_JAPONAIS):
        for param in params[2:3]:
            trouve = CJK.search(param)
            if trouve:
                return param.strip()
    trouve = CJK.search(texte)
    return trouve.group(0) if trouve else None


def nettoyer(wikitexte: str) -> str:
    """Retire le balisage pour mesurer une longueur de texte, pas de markup."""
    t = re.sub(r"<ref[^>]*>.*?</ref>", " ", wikitexte, flags=re.DOTALL)
    t = re.sub(r"<ref[^>]*/>", " ", t)
    t = re.sub(r"<[^>]+>", " ", t)
    t = deballer(t)
    t = re.sub(r"\[\[(?:Fichier|Image|File):[^\]]*\]\]", " ", t, flags=re.IGNORECASE)
    t = re.sub(r"\{\{[^{}]*\}\}", " ", t)
    t = re.sub(r"\{\{[^{}]*\}\}", " ", t)
    t = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]|]*)\]\]", r"\1", t)
    t = re.sub(r"'{2,}", "", t)
    t = re.sub(r"^[:*#]+", " ", t, flags=re.MULTILINE)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


@dataclass
class Personnage:
    nom: str
    description: str
    forme_japonaise: str | None
    position: int

    @property
    def decrit(self) -> bool:
        return len(self.description) >= SEUIL_DESCRIPTION


@dataclass
class Analyse:
    sections_c1: list[str] = field(default_factory=list)
    personnages: list[Personnage] = field(default_factory=list)
    article_dedie: str | None = None
    prose: bool = False
    marqueurs_denouement: list[str] = field(default_factory=list)
    motif_rejet: str | None = None

    @property
    def decrits(self) -> list[Personnage]:
        return [p for p in self.personnages if p.decrit]

    @property
    def exploitable(self) -> bool:
        """C1 et C2 et C3 — au moins 3 personnages isoles et decrits."""
        return len(self.decrits) >= 3

    @property
    def avec_japonais(self) -> list[Personnage]:
        return [p for p in self.personnages if p.forme_japonaise]


def _decouper_sections(wikitexte: str) -> list[tuple[int, str, str]]:
    """Rend (niveau, titre, corps) pour chaque section du wikitexte."""
    marques = list(TITRE_SECTION.finditer(wikitexte))
    sections = []
    for i, m in enumerate(marques):
        fin = marques[i + 1].start() if i + 1 < len(marques) else len(wikitexte)
        sections.append((len(m.group(1)), m.group(2), wikitexte[m.end() : fin]))
    return sections


def _corps_c1(wikitexte: str) -> tuple[list[str], str]:
    """Rend les titres satisfaisant C1 et le corps cumule de ces sections.

    Une sous-section d'une section C1 est incluse : « Personnages principaux »
    sous « Personnages » ne doit pas etre perdue.
    """
    sections = _decouper_sections(wikitexte)
    titres: list[str] = []
    morceaux: list[str] = []
    niveau_actif: int | None = None
    for niveau, titre, corps in sections:
        if MOTIF_TITRE.match(normaliser(titre)):
            titres.append(titre)
            morceaux.append(corps)
            niveau_actif = niveau
        elif niveau_actif is not None and niveau > niveau_actif:
            morceaux.append(f"\n=== {titre} ===\n{corps}")
        else:
            niveau_actif = None
    return titres, "\n".join(morceaux)


def _isoler(corps: str) -> list[Personnage]:
    """Parametre C2 : un personnage est un nom en tete de bloc.

    Cinq formes reconnues, toutes relevees sur frwiki a l'inventaire :

    - liste de definition ``; Nom`` suivie de lignes ``:`` — forme dominante,
      et celle qui porte ``{{japonais}}`` (ex. *My Hero Academia*) ;
    - puce en gras, nom entre triples apostrophes ;
    - **puce sans gras**, ``* Nom : description`` (ex. *Fullmetal Alchemist*) ;
    - sous-section ``=== Nom ===`` ;
    - paragraphe ouvert par un nom en gras.

    Les deux formes sans gras manquaient a la premiere ecriture : elles ont ete
    ajoutees apres lecture du wikitexte reel, pas supposees.
    """
    blocs: list[tuple[str, str]] = []

    for m in re.finditer(r"^;\s*(.+?)\s*$\n((?:^[:*].*$\n?)*)", corps, re.MULTILINE):
        blocs.append((m.group(1), m.group(2)))
    for m in re.finditer(r"^\*+\s*'''(.+?)'''\s*(.*)$", corps, re.MULTILINE):
        blocs.append((m.group(1), m.group(2)))
    for m in re.finditer(
        r"^\*+\s*((?:\{\{|\[\[|[^:*\n])[^:\n]{1,79}?)\s*:\s*(.+)$",
        corps,
        re.MULTILINE,
    ):
        blocs.append((m.group(1), m.group(2)))
    for m in re.finditer(
        r"^={3,6}\s*(.+?)\s*={3,6}\s*$\n(.*?)(?=^=|\Z)",
        corps,
        re.MULTILINE | re.DOTALL,
    ):
        blocs.append((m.group(1), m.group(2)))
    for m in re.finditer(r"^'''(.+?)'''\s*[:,–—-]?\s*(.+)$", corps, re.MULTILINE):
        blocs.append((m.group(1), m.group(2)))

    personnages: list[Personnage] = []
    vus: set[str] = set()
    for brut_nom, brut_desc in blocs:
        nom = nettoyer(brut_nom)
        if not nom or len(nom) > 80:
            continue
        cle = normaliser(nom)
        if cle in vus:
            continue
        vus.add(cle)
        personnages.append(
            Personnage(
                nom=nom,
                description=nettoyer(brut_desc),
                forme_japonaise=japonais_de(brut_nom) or japonais_de(brut_desc[:300]),
                position=len(personnages) + 1,
            )
        )
    return personnages


def analyser(wikitexte: str) -> Analyse:
    """Applique la definition validee a un article."""
    titres, corps = _corps_c1(wikitexte)
    analyse = Analyse(sections_c1=titres)
    if not titres:
        analyse.motif_rejet = "aucune section satisfaisant C1"
        return analyse

    # Parametre 2 : le renvoi ne compte que s'il part d'une section C1.
    renvoi = ARTICLE_DETAILLE.search(corps)
    if renvoi:
        analyse.article_dedie = renvoi.group(1).strip()

    analyse.personnages = _isoler(corps)
    texte = nettoyer(corps)
    plat = normaliser(texte)
    analyse.marqueurs_denouement = [m for m in MARQUEURS_DENOUEMENT if m in plat]

    # Parametre 4 : de la matiere, mais aucun personnage isole.
    if not analyse.personnages and len(texte) >= 200:
        analyse.prose = True
        analyse.motif_rejet = "prose continue, personnages non isoles"
    elif not analyse.exploitable:
        analyse.motif_rejet = (
            f"{len(analyse.decrits)} personnage(s) decrit(s) sur "
            f"{len(analyse.personnages)} isole(s) — seuil C3 non atteint"
        )
    return analyse


def analyser_dedie(wikitexte: str) -> Analyse:
    """Analyse un article entierement consacre aux personnages.

    C1 ne s'y applique pas : l'article **est** la liste, son titre ne se
    retrouve pas dans une section interne. L'isolation et le seuil, eux,
    restent ceux de la definition validee.

    Mesure du 2026-09-12 : un article dedie porte 27,0 personnages decrits en
    moyenne contre 13,3 pour une section in situ — mais la dispersion (59, 22,
    0) interdit d'en faire une regle. *Personnages de Satan 666* isole 39 noms
    dont aucun n'atteint le seuil, et se revele **plus pauvre** que la section
    in situ du meme article. D'ou la regle retenue : retenir la plus riche des
    deux formes, jamais substituer l'une a l'autre par principe.
    """
    analyse = Analyse(sections_c1=["<article dedie>"])
    analyse.personnages = _isoler(wikitexte)
    texte = nettoyer(wikitexte)
    plat = normaliser(texte)
    analyse.marqueurs_denouement = [m for m in MARQUEURS_DENOUEMENT if m in plat]
    if not analyse.exploitable:
        analyse.motif_rejet = (
            f"{len(analyse.decrits)} personnage(s) decrit(s) sur "
            f"{len(analyse.personnages)} isole(s) — seuil C3 non atteint"
        )
    return analyse
