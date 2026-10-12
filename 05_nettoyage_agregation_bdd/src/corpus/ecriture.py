"""Écrire dans le corpus de `bench` — la seule porte du module 05.

Règle (CONTRIBUTING.md, « Écrire dans le corpus ou les vecteurs ») : toute
écriture de document ou de fragment — ajout, modification, suppression — passe
par `ecrire_corpus`, qui exige le corpus en argument NOMMÉ, sans valeur par
défaut. La base donne `'v1'` par défaut à `corpus_id` (migration 021, une dette) :
le code ne s'y fie jamais. Un test vérifie qu'aucune autre écriture de ces tables
n'existe dans le module, et que le corpus n'a pas de défaut.

La fonction écrit l'ÉCART entre la cible (tables temporaires `cible_docs` et
`cible_chunks`, préparées par l'appelant, sans colonne de corpus) et le corpus
nommé : une ligne identique n'est pas réécrite, un fragment identique garde son
`chunk_id`. Un corpus clos refuse toute écriture (déclencheur de 021) : un rejeu
sans écart passe, un écart échoue.
"""

from __future__ import annotations

from collections import Counter

import psycopg


def ecrire_corpus(
    curseur: psycopg.Cursor, *, corpus_id: str, vider: bool = False
) -> dict[str, Counter]:
    """Écrit l'écart cible → corpus `corpus_id` ; rend les écritures par source.

    `vider` supprime d'abord tout le corpus nommé (rechargement complet, que
    l'appelant annule). Aucune ligne d'un autre corpus n'est lue ni écrite.
    """
    p = {"corpus": corpus_id}
    operations = []
    if vider:
        operations.append(
            (
                "vidage",
                "DELETE FROM bench.corpus_docs WHERE corpus_id = %(corpus)s"
                " RETURNING source",
            )
        )
    operations += [
        (
            "docs retirés",
            """
DELETE FROM bench.corpus_docs d
WHERE d.corpus_id = %(corpus)s
  AND NOT EXISTS (SELECT 1 FROM cible_docs c WHERE c.doc_key = d.doc_key)
RETURNING d.source
""",
        ),
        (
            "docs modifiés",
            """
UPDATE bench.corpus_docs d
SET source = c.source, series_id = c.series_id, kitsu_id = c.kitsu_id,
    boost_score = c.boost_score, doc_text = c.doc_text,
    metadata_json = c.metadata_json, title = c.title
FROM cible_docs c
WHERE d.corpus_id = %(corpus)s AND c.doc_key = d.doc_key
  AND (d.source, d.series_id, d.kitsu_id, d.boost_score, d.doc_text,
       d.metadata_json, d.title)
      IS DISTINCT FROM
      (c.source, c.series_id, c.kitsu_id, c.boost_score, c.doc_text,
       c.metadata_json, c.title)
RETURNING d.source
""",
        ),
        (
            "docs ajoutés",
            """
INSERT INTO bench.corpus_docs
  (corpus_id, doc_key, source, series_id, kitsu_id, boost_score, doc_text,
   metadata_json, title)
SELECT %(corpus)s, c.doc_key, c.source, c.series_id, c.kitsu_id, c.boost_score,
       c.doc_text, c.metadata_json, c.title
FROM cible_docs c
WHERE NOT EXISTS (SELECT 1 FROM bench.corpus_docs d
                  WHERE d.corpus_id = %(corpus)s AND d.doc_key = c.doc_key)
ORDER BY c.doc_key
RETURNING source
""",
        ),
        (
            "fragments retirés",
            """
DELETE FROM bench.corpus_chunks k
WHERE k.corpus_id = %(corpus)s
  AND NOT EXISTS (
    SELECT 1 FROM cible_chunks c
    WHERE c.doc_key = k.doc_key AND c.chunk_index = k.chunk_index
      AND c.chunk_text = k.chunk_text
      AND c.char_start IS NOT DISTINCT FROM k.char_start
      AND c.char_end IS NOT DISTINCT FROM k.char_end
      AND c.chunk_hash IS NOT DISTINCT FROM k.chunk_hash
      AND k.token_count IS NULL)
RETURNING split_part(k.doc_key, ':', 1)
""",
        ),
        (
            "fragments ajoutés",
            """
INSERT INTO bench.corpus_chunks
  (corpus_id, doc_key, chunk_index, chunk_text, char_start, char_end, token_count,
   chunk_hash)
SELECT %(corpus)s, c.doc_key, c.chunk_index, c.chunk_text, c.char_start,
       c.char_end, NULL, c.chunk_hash
FROM cible_chunks c
WHERE NOT EXISTS (SELECT 1 FROM bench.corpus_chunks k
                  WHERE k.corpus_id = %(corpus)s AND k.doc_key = c.doc_key
                    AND k.chunk_index = c.chunk_index)
ORDER BY c.doc_key, c.chunk_index
RETURNING split_part(doc_key, ':', 1)
""",
        ),
    ]
    return {
        nom: Counter(s for (s,) in curseur.execute(requete, p))
        for nom, requete in operations
    }
