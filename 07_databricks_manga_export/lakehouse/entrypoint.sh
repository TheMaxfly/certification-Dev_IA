#!/bin/bash
# Le compose lance le conteneur avec l'uid HÔTE (pour lire le raw monté en ro).
# Cet uid arbitraire n'a pas d'entrée passwd → Hadoop UGI lève une LoginException
# (« invalid null input: name ») au démarrage de Spark. nss_wrapper fabrique une
# entrée passwd/group à la volée, sans root et sans toucher /etc/passwd.
set -e

if ! getent passwd "$(id -u)" >/dev/null 2>&1; then
  export NSS_WRAPPER_PASSWD="${NSS_WRAPPER_PASSWD:-/tmp/passwd.nss}"
  export NSS_WRAPPER_GROUP="${NSS_WRAPPER_GROUP:-/tmp/group.nss}"
  echo "lakehouse:x:$(id -u):$(id -g):lakehouse:/tmp:/bin/bash" > "$NSS_WRAPPER_PASSWD"
  echo "lakehouse:x:$(id -g):" > "$NSS_WRAPPER_GROUP"
  wrapper="$(ls /usr/lib/*/libnss_wrapper.so 2>/dev/null | head -1)"
  [ -n "$wrapper" ] && export LD_PRELOAD="$wrapper"
fi

exec python3 -m lakehouse.cli "$@"
