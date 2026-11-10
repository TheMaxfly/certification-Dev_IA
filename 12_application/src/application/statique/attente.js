// Pose « je lis » de Kōhai pendant l'attente de la réponse. Une fonction ; aucune donnée.
(function () {
  "use strict";
  var formulaire = document.querySelector("form[action='/question']");
  if (!formulaire) { return; }
  formulaire.addEventListener("submit", function () {
    document.body.classList.add("en-attente");
  });
})();
