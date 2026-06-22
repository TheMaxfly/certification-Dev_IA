# Certification Bloc 1 IA
# Certification Dev IA

Dépôt principal regroupant les différents modules réalisés dans le cadre du projet de certification Dev IA.

## Structure du dépôt

- `01_scraping_manganews/` : extraction des données Manga-News avec Scrapy, nettoyage, validation Great Expectations et import PostgreSQL.
- `00_docs/` : rapports, schémas, preuves de reproductibilité et documentation de certification.
- `02_pgvector_embeddings/` : préparation future des embeddings et de l’index vectoriel.
- `03_rag_langchain/` : intégration future du système RAG avec LangChain et LLM.
- `04_benchmark_llm/` : tests et comparaison des modèles LLM.

## Objectif général

Construire une chaîne complète de traitement de données et d’intelligence artificielle autour d’un assistant de recommandation manga/BD :

1. collecte des données ;
2. nettoyage et validation ;
3. import en base de données ;
4. enrichissement pour le RAG ;
5. embeddings et recherche vectorielle ;
6. génération de réponses avec un LLM.