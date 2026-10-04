// Résumé Design: clicking a suggested font fills its box.
document.addEventListener("click", (event) => {
  const button = event.target.closest(".chip-button[data-fill]");
  if (!button) return;
  const box = document.querySelector(`input[name="${button.dataset.fill}"]`);
  if (box) { box.value = button.dataset.font; box.focus(); }
});
