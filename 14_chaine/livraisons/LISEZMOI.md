# Les rapports de livraison

Chaque passage de la chaîne (`make chaine CANDIDAT=…`) écrit ici deux fichiers,
`<horodatage UTC>_<candidat>.md` pour un humain et `<horodatage UTC>_<candidat>.json`
pour la commande de promotion. Ils se commitent avant le passage suivant :
`docs(chaine): rapport de livraison <horodatage>_<candidat>`.

Un rapport ne porte ni secret, ni pseudonyme, ni texte de passage. Le
[README du module](../README.md) dit comment le lire.
