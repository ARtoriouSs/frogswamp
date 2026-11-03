// A heading and/or labels with a "?" that shows a hint on hover, keyboard focus or tap.
// Loaded by frontend.extra_module_url in configuration.yaml. HA has no card with a tooltip.
//   type: custom:frog-hint-card
//   heading: Prices        (optional, with icon and hint)
//   items:                 (optional, a row of labels)
//     - label: Per kWh
//       hint: Text of the hint
const GAP = 8;
const WIDTH = 320;

class FrogHintCard extends HTMLElement {
  setConfig(config) {
    this.config = config;
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this.render();
  }

  getCardSize() {
    return 1;
  }

  getGridOptions() {
    return { columns: "full", rows: "auto" };
  }

  render() {
    const { heading, icon, hint, items = [] } = this.config;
    const root = this.shadowRoot;
    root.innerHTML = `<style>
      .row { display: flex; align-items: center; flex-wrap: wrap; gap: 8px 16px; min-height: 28px; }
      .entry { display: inline-flex; align-items: center; gap: 6px; color: var(--primary-text-color); font-size: 14px; }
      .heading { font-size: 16px; font-weight: 500; }
      ha-icon { --mdc-icon-size: 16px; color: var(--secondary-text-color); }
      button {
        all: unset; width: 18px; height: 18px; border-radius: 50%; text-align: center; font-size: 12px; line-height: 18px;
        cursor: help; color: var(--text-primary-color, #fff); background: var(--primary-color);
      }
      button:focus-visible { outline: 2px solid var(--primary-text-color); }
      .tip {
        display: none; position: fixed; z-index: 1000; box-sizing: border-box; padding: 10px 12px; border-radius: 8px;
        max-width: min(${WIDTH}px, calc(100vw - ${2 * GAP}px)); white-space: pre-line; font-size: 13px; line-height: 1.4;
        color: var(--primary-text-color); background: var(--card-background-color, #222);
        border: 1px solid var(--divider-color); box-shadow: 0 4px 16px rgba(0, 0, 0, 0.4);
      }
    </style><div class="row"></div><div class="tip" role="tooltip"></div>`;
    const row = root.querySelector(".row");
    if (heading) row.append(this.entry(heading, hint, icon, "heading"));
    for (const item of items) row.append(this.entry(item.label, item.hint));
  }

  entry(label, hint, icon, className = "") {
    const entry = document.createElement("span");
    entry.className = `entry ${className}`;
    if (icon) {
      const iconElement = document.createElement("ha-icon");
      iconElement.setAttribute("icon", icon);
      entry.append(iconElement);
    }
    entry.append(label);
    if (hint) entry.append(this.button(label, hint));
    return entry;
  }

  button(label, hint) {
    const button = document.createElement("button");
    button.textContent = "?";
    button.setAttribute("aria-label", `About ${label}`);
    button.addEventListener("pointerenter", (event) => {
      if (event.pointerType === "mouse") this.show(button, hint);
    });
    button.addEventListener("pointerleave", (event) => {
      if (event.pointerType === "mouse") this.hide();
    });
    button.addEventListener("focus", () => this.show(button, hint));
    button.addEventListener("blur", () => this.hide());
    // A touch screen has no hover: a tap opens the hint, a second tap closes it
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      if (this.owner === button && event.pointerType !== "mouse") this.hide();
      else this.show(button, hint);
    });
    return button;
  }

  show(button, hint) {
    const tip = this.shadowRoot.querySelector(".tip");
    tip.textContent = hint;
    tip.style.display = "block";
    this.owner = button;
    const place = button.getBoundingClientRect();
    const size = tip.getBoundingClientRect();
    const left = Math.min(place.left, window.innerWidth - size.width - GAP);
    const below = place.bottom + GAP;
    const top = below + size.height > window.innerHeight ? place.top - size.height - GAP : below;
    tip.style.left = `${Math.max(GAP, left)}px`;
    tip.style.top = `${Math.max(GAP, top)}px`;
  }

  hide() {
    this.shadowRoot.querySelector(".tip").style.display = "none";
    this.owner = null;
  }
}

if (!customElements.get("frog-hint-card")) customElements.define("frog-hint-card", FrogHintCard);
