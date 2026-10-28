import postcss from "postcss";
import flexGapPolyfill from "flex-gap-polyfill";

// Keep modern CSS untouched; margin-based emulation only applies without flex gap.
export function chrome80Styles() {
  return {
    postcssPlugin: "auditbox-chrome80-styles",
    async Once(root, { result }) {
      root.walkRules((rule) => {
        if (rule.selector.includes(":focus-visible")) {
          rule.cloneBefore({ selector: rule.selector.replaceAll(":focus-visible", ":focus") });
        }
      });
      const legacySource = root.clone();
      // Wrapping unsupported units in var() would invalidate earlier vh fallbacks.
      legacySource.walkDecls((decl) => {
        if (/\b\d*(?:dvh|svh|lvh|cqw|cqh)\b/.test(decl.value)) decl.remove();
      });
      const legacy = await postcss([flexGapPolyfill()]).process(legacySource, { from: result.opts.from });
      // Anonymous button text has no child element to restore pointer events on.
      legacy.root.walkDecls("pointer-events", (decl) => {
        if (/^var\(--(?:has-fgp|parent-has-fgp)\)/.test(decl.value)) decl.remove();
      });
      legacy.root.walkRules((rule) => {
        if (rule.parent.type === "atrule" && /keyframes$/.test(rule.parent.name)) return;
        rule.selectors = rule.selectors.map((selector) =>
          selector.startsWith(".no-button-layout")
            ? `.no-flex-gap${selector}`
            : /^(?:html|:root)(?=$|[\s.#[:>])/.test(selector)
            ? selector.replace(/^(html|:root)/, "$1.no-flex-gap")
            : `.no-flex-gap ${selector}`);
      });
      root.append(legacy.root.nodes);
      // A pre-83 button is not a flex container; never inherit its margin shim.
      root.append(`.no-button-layout button, .no-button-layout button > * {
        --fgp-margin-top: initial !important; --fgp-margin-left: initial !important;
        --fgp-width: initial !important; --fgp-height: initial !important;
        --fgp-min-width: initial !important; --fgp-max-width: initial !important;
        --fgp-min-height: initial !important; --fgp-max-height: initial !important;
      }`);
    },
  };
}
