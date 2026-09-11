"""Les quatre règles validées le 2026-09-12, éprouvées hors ligne."""

import chargeur_kitsu as ck


class TestFormes:
    """Les formes viennent de TROIS champs, et la graphie japonaise n'est
    pas un alias."""

    ATTRIBUTS = {
        "canonicalName": "Katsuki Bakugo",
        "names": {"en": "Katsuki Bakugo", "ja_jp": "爆豪 勝己", "jp": "かっちゃん"},
        "otherNames": ["Kacchan", "Great Explosion Murder God Dynamight", "", "  "],
    }

    def test_les_trois_champs_produisent_trois_types(self) -> None:
        types = {t for _, t, _ in ck.formes_de(self.ATTRIBUTS)}
        assert types == {"canonical", "name_lang", "alias"}

    def test_la_graphie_japonaise_est_name_lang_jamais_alias(self) -> None:
        """§4 : c'est le pont de fusion, et il a son index distinct."""
        formes = ck.formes_de(self.ATTRIBUTS)
        japonaises = [(f, t, lg) for f, t, lg in formes if lg == "ja"]
        assert ("爆豪 勝己", "name_lang", "ja") in japonaises
        assert ("かっちゃん", "name_lang", "ja") in japonaises, (
            "la clé `jp` vaut aussi ja"
        )
        alias = {f for f, t, _ in formes if t == "alias"}
        assert "爆豪 勝己" not in alias, "reléguée en alias, elle serait inexploitable"

    def test_l_attendu_des_alias_ne_porte_que_sur_othernames(self) -> None:
        alias = [f for f, t, _ in ck.formes_de(self.ATTRIBUTS) if t == "alias"]
        assert alias == ["Kacchan", "Great Explosion Murder God Dynamight"]

    def test_les_formes_vides_sont_ecartees(self) -> None:
        """Contrôle 11 : 8 chaînes vides et 2 blancs seuls dans le raw."""
        formes = ck.formes_de({"canonicalName": "X", "otherNames": ["", "   ", "Y"]})
        assert [f for f, t, _ in formes if t == "alias"] == ["Y"]

    def test_une_cle_de_langue_inconnue_est_conservee_telle_quelle(self) -> None:
        """`en_jp` (9 formes) : sa sémantique n'est pas établie, on ne l'invente pas."""
        formes = ck.formes_de({"canonicalName": "X", "names": {"en_jp": "Hiroto"}})
        assert ("Hiroto", "name_lang", "en_jp") in formes


class TestLangue:
    """Règle B : trois classes, et NULL quand la règle ne conclut pas."""

    def test_un_texte_anglais_est_classe_en(self) -> None:
        assert ck.langue_de("He is the son of the village chief and her rival.") == "en"

    def test_un_texte_majoritairement_cjk_est_classe_ja(self) -> None:
        texte = "黒坂玄悟は普通の高校生である。ある日、彼の体は侵略される。"
        assert ck.langue_de(texte) == "ja"

    def test_un_nom_japonais_dans_une_phrase_anglaise_reste_en(self) -> None:
        """Le seuil existe pour ça : quelques idéogrammes ne font pas un
        texte japonais."""
        phrase = "His real name is 爆豪 勝己 and he is her rival."
        assert ck.langue_de(phrase) == "en"

    def test_sans_marqueur_la_regle_ne_conclut_pas(self) -> None:
        assert ck.langue_de("Sacchan.") is None
        assert ck.langue_de("   ") is None

    def test_le_francais_n_est_pas_tente(self) -> None:
        """Mesuré à 0,4 % : le faux positif coûterait plus que le gain."""
        phrase = "Le frère de la protagoniste, qui vit dans le village."
        assert ck.langue_de(phrase) is None


class TestLicence:
    """Règle C, telle que proposée et validée le 2026-09-12."""

    def test_un_wiki_de_la_galaxie_donne_cc_by_sa(self) -> None:
        for citee in ("Wikipedia", "One Piece Wikia", "Naruto Fandom"):
            licence, provenance = ck.licence_de(f"Un personnage. (Source: {citee})")
            assert licence == "CC BY-SA"
            assert provenance == citee

    def test_une_autre_source_reste_a_instruire(self) -> None:
        licence, provenance = ck.licence_de("Un personnage. (Source: TV Tropes)")
        assert licence == "à instruire"
        assert provenance == "TV Tropes"

    def test_sans_citation_aucune_licence_n_est_inventee(self) -> None:
        assert ck.licence_de("Un personnage sans provenance.") == (None, None)

    def test_la_provenance_est_stockee_telle_quelle(self) -> None:
        """C'est elle qui rend l'attribution vérifiable : on ne la normalise pas."""
        _, provenance = ck.licence_de("X (Source: Medaka Box Wiki.)")
        assert provenance == "Medaka Box Wiki"


class TestRoles:
    """Règle D : quatre valeurs françaises, granularité préservée."""

    def test_les_quatre_valeurs_kitsu_sont_mappees(self) -> None:
        assert ck.ROLES == {
            "main": "principal",
            "supporting": "secondaire",
            "recurring": "recurrent",
            "cameo": "apparition",
        }

    def test_un_role_inconnu_ne_force_aucune_case(self) -> None:
        """Il reste NULL avec son role_source intact, pour être arbitré."""
        assert ck.ROLES.get("background") is None
