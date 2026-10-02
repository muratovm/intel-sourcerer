"use strict";
const token = document.querySelector('meta[name="csrf-token"]').content;
document.querySelectorAll("[data-source]").forEach(
  (input) =>
    (input.onchange = async () => {
      const enabled = input.checked;
      input.disabled = true;
      try {
        const response = await fetch("/api/source-status", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-CSRF-Token": token,
          },
          body: JSON.stringify({ id: input.dataset.source, enabled }),
        });
        const result = await response.json();
        if (!response.ok)
          throw new Error(result.error || "Unable to save source");
        location.reload();
      } catch (error) {
        input.checked = !enabled;
        input.disabled = false;
        alert(error.message);
      }
    }),
);
