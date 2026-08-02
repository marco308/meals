// Repeatable ingredient-line rows, shared by the recipe editor and the meal
// editor's loose ingredients. Quantities follow the server's convention:
// metric (g/kg/ml/l) or a count of a natural unit — the API's 422 explains
// any conversion, so the editor doesn't police units itself.

import { html, render } from "../dom.js";

const UNIT_SUGGESTIONS = ["g", "kg", "ml", "l", "item", "tin", "clove", "bunch", "head", "pack", "jar", "slice", "stick", "sprig", "leaf", "sheet", "sausage", "egg"];

export function linesEditor(container, initial = []) {
  let lines = initial.map((line) => ({
    name: line.name,
    quantity: line.quantity,
    unit: line.unit,
    raw: line.raw ?? null,
    dirty: false,
  }));
  if (lines.length === 0) lines.push(blank());

  function blank() {
    return { name: "", quantity: null, unit: null, raw: null, dirty: true };
  }

  // A wholly blank row is dropped by read(), so it must never block submit;
  // `required` on the name only guards rows that would otherwise lose typed
  // qty/unit data silently.
  const hasContent = (line) => line.quantity !== null || (line.unit ?? "").trim() !== "" || line.name.trim() !== "";

  function draw() {
    render(container, html`
      <datalist id="unit-suggestions">
        ${UNIT_SUGGESTIONS.map((u) => html`<option value="${u}"></option>`)}
      </datalist>
      ${lines.map(
        (line, index) => html`
          <div class="line-row" data-index="${index}">
            <input type="number" step="any" min="0" placeholder="qty" value="${line.quantity ?? ""}" data-k="quantity" aria-label="Quantity">
            <input type="text" placeholder="unit" value="${line.unit ?? ""}" list="unit-suggestions" data-k="unit" aria-label="Unit">
            <input type="text" placeholder="ingredient" value="${line.name}" data-k="name" aria-label="Ingredient" ${hasContent(line) ? "required" : ""}>
            <button type="button" class="icon-btn warm" data-drop tabindex="-1" title="Remove line">✕</button>
          </div>
        `,
      )}
      <button type="button" class="icon-btn" data-more>＋ another ingredient</button>
    `);

    for (const row of container.querySelectorAll(".line-row")) {
      const index = Number(row.dataset.index);
      for (const input of row.querySelectorAll("[data-k]")) {
        input.oninput = () => {
          const key = input.dataset.k;
          lines[index][key] = key === "quantity" ? (input.value === "" ? null : Number(input.value)) : input.value;
          // Hand-edited lines drop their scraped raw text — it no longer
          // describes what the row says.
          lines[index].dirty = true;
          row.querySelector('[data-k="name"]').required = hasContent(lines[index]);
        };
        if (input.dataset.k === "name") {
          input.onkeydown = (event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              if (index === lines.length - 1) {
                lines.push(blank());
                draw();
                container.querySelectorAll('[data-k="quantity"]')[lines.length - 1].focus();
              }
            }
          };
        }
      }
      row.querySelector("[data-drop]").onclick = () => {
        lines.splice(index, 1);
        if (lines.length === 0) lines.push(blank());
        draw();
      };
    }
    container.querySelector("[data-more]").onclick = () => {
      lines.push(blank());
      draw();
      container.querySelectorAll('[data-k="quantity"]')[lines.length - 1].focus();
    };
  }

  draw();

  return {
    read() {
      return lines
        .filter((line) => line.name.trim() !== "")
        .map((line) => ({
          name: line.name.trim(),
          quantity: line.quantity,
          unit: line.unit?.trim() || null,
          raw: line.dirty ? null : line.raw,
        }));
    },
  };
}
