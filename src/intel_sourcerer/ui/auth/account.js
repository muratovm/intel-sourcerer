"use strict";
const deleteDialog = document.getElementById("delete-account-dialog");
document.getElementById("delete-account").addEventListener("click", () => {
  deleteDialog.showModal();
  document.getElementById("delete-account-username").focus();
});
document.getElementById("cancel-delete-account").addEventListener("click", () => deleteDialog.close());
