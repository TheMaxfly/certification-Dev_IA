# Image de référence du lakehouse — versions FIGÉES et vérifiées (Étape 0) :
# Apache Spark 3.5.9 (image officielle, Java 17) ↔ delta-spark 3.3.2 (matrice Delta).
# pyspark est installé par pip (hermétique) et les jars Delta sont déposés dans
# SON dossier jars → aucune résolution réseau au runtime (LAKEHOUSE_DELTA_BUNDLED=1).
# Le code est mis sur PYTHONPATH et lancé via `python -m` : pas de packaging à
# construire dans l'image (le pip/setuptools de l'image est trop ancien pour PEP 621).
FROM apache/spark:3.5.9

ARG DELTA_VERSION=3.3.2
ENV HOME=/tmp
USER root

# curl + pip + libnss-wrapper (uid hôte arbitraire sans entrée passwd).
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl python3-pip libnss-wrapper \
    && rm -rf /var/lib/apt/lists/*

# Dépendances runtime figées (pyspark 3.5.9 = l'image ; delta/typer/duckdb).
RUN pip install --no-cache-dir \
      "pyspark==3.5.9" "delta-spark==${DELTA_VERSION}" "typer>=0.12" "duckdb>=1.1"

# Jars Delta bundlés dans ${SPARK_HOME}/jars : c'est le Spark de l'image
# (SPARK_HOME) que pyspark utilise au runtime, PAS les jars du wheel pip.
# delta-spark n'inclut que l'API Python — le couple exact de la matrice, aucun
# réseau au runtime.
RUN curl -fSL -o "${SPARK_HOME}/jars/delta-spark_2.12-${DELTA_VERSION}.jar" \
      "https://repo1.maven.org/maven2/io/delta/delta-spark_2.12/${DELTA_VERSION}/delta-spark_2.12-${DELTA_VERSION}.jar" \
    && curl -fSL -o "${SPARK_HOME}/jars/delta-storage-${DELTA_VERSION}.jar" \
      "https://repo1.maven.org/maven2/io/delta/delta-storage/${DELTA_VERSION}/delta-storage-${DELTA_VERSION}.jar"

# Extension DuckDB delta pré-installée (lecteur externe hors réseau), sous /tmp
# pour être lisible par l'utilisateur non-root au runtime (HOME=/tmp).
RUN python3 -c "import duckdb; duckdb.connect().execute('INSTALL delta;')"

# Le code, sur PYTHONPATH (aucun wheel à construire).
COPY src /opt/lakehouse/src
COPY entrypoint.sh /opt/lakehouse/entrypoint.sh
RUN chmod +x /opt/lakehouse/entrypoint.sh

ENV LAKEHOUSE_DELTA_BUNDLED=1 \
    MASTER=local[*] \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/lakehouse/src \
    HADOOP_USER_NAME=lakehouse

# Utilisateur NON-ROOT (uid par défaut de l'image officielle : spark=185).
# Le compose surcharge par l'uid hôte pour lire le raw monté en lecture seule ;
# l'entrypoint fabrique alors une entrée passwd via nss_wrapper.
USER spark

ENTRYPOINT ["/opt/lakehouse/entrypoint.sh"]
CMD ["--help"]
