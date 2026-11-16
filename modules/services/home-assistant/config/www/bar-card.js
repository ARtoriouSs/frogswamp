// A card with an icon, a name, a text and a bar under them, filled to a percentage. Loaded by frontend.extra_module_url
// in configuration.yaml. It takes the keys of a mushroom-template-card, so primary, secondary, icon and color can be
// templates with `entity` in them, and tap_action / hold_action work the same
//   type: custom:frog-bar-card
//   entity: sensor.pixel_7_battery_level
//   primary: Phone
//   secondary: "{{ states(entity) }}%"
//   icon: mdi:cellphone
//   color: "{{ 'red' if states(entity) | float(0) < 25 else 'green' }}"
//   multiline_secondary: true              (optional, the text may wrap)
//   vertical: true                         (optional, the icon above the text, for tall narrow cards)
//   bar:
//     attribute: uptime                   (optional, the value is the entity state by default)
//     steps: {0: red, 25: yellow, 75: green}   (the colour of the highest step at or below the value)
//     color: grey                         (optional, one colour instead of steps)
//   bar: null                             (no bar, for a merged card that has no percentage)
const TEMPLATE_KEYS = ["primary", "secondary", "icon", "color"];
const HOLD_MS = 500;

const isTemplate = (value) => typeof value === "string" && (value.includes("{{") || value.includes("{%"));
const cssColor = (name) => (!name ? "var(--disabled-color)" : name.startsWith("#") || name.startsWith("rgb") ? name : `var(--${name}-color)`);
const escape = (text) => String(text ?? "").replace(/[&<>"]/g, (char) => `&#${char.charCodeAt(0)};`);

class FrogBarCard extends HTMLElement {
  setConfig(config) {
    this.config = config;
    this.values = {};
    this.unsubscribe();
    if (!this.shadowRoot) this.build();
    this.subscribe();
    this.render();
  }

  set hass(hass) {
    this._hass = hass;
    this.subscribe();
    this.render();
  }

  connectedCallback() {
    this.subscribe();
  }

  disconnectedCallback() {
    this.unsubscribe();
  }

  getCardSize() {
    return 1;
  }

  getGridOptions() {
    return { columns: 12, rows: 1 };
  }

  // HA renders the templates and sends a new result when an entity in them changes
  subscribe() {
    if (this.subscriptions || !this._hass || !this.config || !this.isConnected) return;
    this.subscriptions = TEMPLATE_KEYS.filter((key) => isTemplate(this.config[key])).map((key) =>
      this._hass.connection.subscribeMessage(
        (message) => {
          this.values[key] = message.result;
          this.render();
        },
        { type: "render_template", template: this.config[key], variables: { entity: this.config.entity, config: this.config } }
      )
    );
  }

  unsubscribe() {
    for (const subscription of this.subscriptions || []) subscription.then((stop) => stop()).catch(() => {});
    this.subscriptions = null;
  }

  value(key) {
    return isTemplate(this.config[key]) ? this.values[key] : this.config[key];
  }

  build() {
    this.attachShadow({ mode: "open" }).innerHTML = `<style>
      ha-card { height: 100%; box-sizing: border-box; padding: 8px 12px; display: flex; align-items: center; gap: 10px;
                cursor: pointer; user-select: none; }
      .icon { flex: none; width: 36px; height: 36px; border-radius: 50%; display: flex; align-items: center; justify-content: center;
              --mdc-icon-size: 20px; }
      .info { flex: 1; min-width: 0; }
      .primary { font-size: 14px; font-weight: 500; line-height: 18px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .secondary { font-size: 12px; line-height: 16px; color: var(--secondary-text-color); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .secondary.multiline { white-space: normal; }
      .bar { height: 6px; margin-top: 4px; border-radius: 3px; background: var(--divider-color); overflow: hidden; }
      .bar[hidden] { display: none; }
      ha-card.vertical { flex-direction: column; justify-content: center; gap: 6px; text-align: center; }
      ha-card.vertical .info { flex: none; width: 100%; }
      .fill { height: 100%; border-radius: 3px; transition: width 0.4s; }
    </style>
    <ha-card>
      <div class="icon"><ha-icon></ha-icon></div>
      <div class="info"><div class="primary"></div><div class="secondary"></div><div class="bar"><div class="fill"></div></div></div>
    </ha-card>`;
    const card = this.shadowRoot.querySelector("ha-card");
    let timer = null;
    let held = false;
    card.addEventListener("pointerdown", () => {
      held = false;
      timer = setTimeout(() => ((held = true), this.action("hold")), HOLD_MS);
    });
    for (const name of ["pointerup", "pointerleave", "pointercancel"]) card.addEventListener(name, () => clearTimeout(timer));
    card.addEventListener("click", () => held || this.action("tap"));
  }

  // HA does the action itself, with the confirmation dialog
  action(kind) {
    const { entity, tap_action = { action: "more-info" }, hold_action = { action: "more-info" } } = this.config;
    this.dispatchEvent(
      new CustomEvent("hass-action", {
        detail: { config: { entity, tap_action, hold_action }, action: kind },
        bubbles: true,
        composed: true,
      })
    );
  }

  render() {
    const root = this.shadowRoot;
    if (!root || !this.config) return;
    root.querySelector("ha-card").classList.toggle("vertical", !!this.config.vertical);
    const color = cssColor(this.value("color"));
    const icon = root.querySelector(".icon");
    icon.style.color = color;
    icon.style.background = `color-mix(in srgb, ${color} 20%, transparent)`;
    root.querySelector("ha-icon").icon = this.value("icon") || "mdi:information";
    root.querySelector(".primary").innerHTML = escape(this.value("primary") ?? this.config.entity);
    const secondary = root.querySelector(".secondary");
    secondary.innerHTML = escape(this.value("secondary"));
    secondary.classList.toggle("multiline", !!this.config.multiline_secondary);

    const { bar } = this.config;
    root.querySelector(".bar").hidden = !bar;
    if (!bar || !this._hass) return;
    const stateObj = this._hass.states[this.config.entity];
    const number = Number.parseFloat(bar.attribute ? stateObj?.attributes[bar.attribute] : stateObj?.state);
    const level = Number.isFinite(number) ? Math.max(0, Math.min(100, number)) : null;
    const steps = Object.entries(bar.steps || { 0: "primary" }).sort((a, b) => a[0] - b[0]);
    const name = bar.color || steps.filter(([at]) => level >= Number(at)).pop()?.[1] || steps[0][1];
    const fill = root.querySelector(".fill");
    fill.style.width = `${level ?? 0}%`;
    fill.style.background = level === null ? "transparent" : cssColor(name);
  }
}

if (!customElements.get("frog-bar-card")) customElements.define("frog-bar-card", FrogBarCard);
