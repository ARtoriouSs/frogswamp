// Any card with a thin bar at its bottom, filled to a percentage. Loaded by frontend.extra_module_url in configuration.yaml.
// The config is the inner card's config with card_type for its type and a bar block. So `<<:` merges and
// overrides work as with the inner card itself
//   type: custom:frog-bar-card
//   card_type: custom:mushroom-template-card
//   entity: sensor.pixel_7_battery_level
//   primary: Phone
//   bar:
//     attribute: uptime                   (optional, the value is the entity state by default)
//     steps: {0: red, 25: yellow, 75: green}   (the colour of the highest step at or below the value)
//     color: grey                         (optional, one colour instead of steps)
//   bar: null                             (no bar, for a merged card that has no percentage)
const helpers = window.loadCardHelpers ? window.loadCardHelpers() : null;

class FrogBarCard extends HTMLElement {
  setConfig(config) {
    if (!config.card_type) throw new Error("card_type is required");
    const { bar, card_type, ...inner } = config;
    this.bar = bar;
    this.inner = { ...inner, type: card_type };
    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" }).innerHTML = `<style>
        :host { display: block; height: 100%; }
        .wrap { position: relative; height: 100%; }
        .wrap > :first-child { height: 100%; }
        .bar { position: absolute; left: 12px; right: 12px; bottom: 4px; height: 4px; border-radius: 2px;
               background: var(--divider-color); overflow: hidden; pointer-events: none; }
        .bar[hidden] { display: none; }
        .fill { height: 100%; border-radius: 2px; transition: width 0.4s; }
      </style><div class="wrap"><div></div><div class="bar"><div class="fill"></div></div></div>`;
    }
    this.key = undefined;
    this.build();
  }

  async build() {
    const config = this.inner;
    const card = (await helpers).createCardElement(config);
    if (config !== this.inner) return;
    this.card = card;
    if (this._hass) card.hass = this._hass;
    this.shadowRoot.querySelector(".wrap").firstElementChild.replaceWith(card);
  }

  set hass(hass) {
    this._hass = hass;
    if (this.card) this.card.hass = hass;
    this.update();
  }

  getCardSize() {
    return this.card?.getCardSize ? this.card.getCardSize() : 1;
  }

  update() {
    const stateObj = this._hass.states[this.inner.entity];
    const raw = this.bar?.attribute ? stateObj?.attributes[this.bar.attribute] : stateObj?.state;
    const number = Number.parseFloat(raw);
    const value = Number.isFinite(number) ? Math.max(0, Math.min(100, number)) : null;
    if (value === this.key) return;
    this.key = value;
    this.shadowRoot.querySelector(".bar").hidden = !this.bar;
    if (!this.bar) return;
    const steps = Object.entries(this.bar.steps || { 0: "primary" }).sort((a, b) => a[0] - b[0]);
    const name = this.bar.color || steps.filter(([at]) => value >= Number(at)).pop()?.[1] || steps[0][1];
    const fill = this.shadowRoot.querySelector(".fill");
    fill.style.width = `${value ?? 0}%`;
    fill.style.background = value === null ? "transparent" : `var(--${name}-color)`;
  }
}

if (!customElements.get("frog-bar-card")) customElements.define("frog-bar-card", FrogBarCard);
