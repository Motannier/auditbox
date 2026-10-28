// Load before React and its dependencies; build.target only transforms syntax.
import "core-js/actual/object/has-own.js";
import "core-js/actual/array/at.js";
import "core-js/actual/array/find-last.js";
import "core-js/actual/array/find-last-index.js";
import "core-js/actual/string/replace-all.js";

// Chrome 80 supports grid gap, so CSS.supports('gap') cannot detect flex gap.
if (typeof document !== "undefined") {
  // xterm uses replaceChildren for rows and selection; Chrome 80 only has append.
  for (const prototype of [Element.prototype, DocumentFragment.prototype]) {
    if (typeof prototype.replaceChildren === "function") continue;
    Object.defineProperty(prototype, "replaceChildren", {
      configurable: true,
      writable: true,
      value: function (...nodes) {
        if (nodes.some((node) => node instanceof Node && node.contains(this))) {
          throw new DOMException("A node cannot contain itself", "HierarchyRequestError");
        }
        const fragment = this.ownerDocument.createDocumentFragment();
        fragment.append(...nodes);
        this.textContent = "";
        this.appendChild(fragment);
      },
    });
  }

  const probe = document.createElement("div");
  probe.style.cssText = "position:absolute;left:-9999px;visibility:hidden;display:flex;flex-direction:column;row-gap:1px";
  for (let index = 0; index < 2; index++) {
    const child = document.createElement("div");
    child.style.cssText = "height:1px;flex-shrink:0";
    probe.appendChild(child);
  }
  document.body.appendChild(probe);
  document.documentElement.classList.toggle("no-flex-gap", probe.scrollHeight !== 3);
  probe.remove();

  // Chromium only enabled flex/grid inside buttons in version 83.
  const button = document.createElement("button");
  button.style.cssText = "position:absolute;left:-9999px;display:grid;grid-template-columns:1px 1px;gap:0;padding:0;border:0";
  for (let index = 0; index < 2; index++) {
    const child = document.createElement("span");
    child.style.cssText = "display:block;width:1px;height:1px";
    button.appendChild(child);
  }
  document.body.appendChild(button);
  const children = button.children;
  document.documentElement.classList.toggle("no-button-layout",
    children[0].getBoundingClientRect().left === children[1].getBoundingClientRect().left);
  button.remove();
}
