// A battery card: icon, name, level and a bar filled to the level. Loaded by frontend.extra_module_url in configuration.yaml.
// Green from 75%, yellow from 25%, red below. " · charging" when the matching _battery_state sensor says so
//   type: custom:frog-battery-card
//   entity: sensor.pixel_7_battery_level
//   name: Phone
//   icon: mdi:cellphone
const colorOf = (level) =>
  level === null ? "var(--disabled-color)" : level >= 75 ? "var(--green-color)" : level >= 25 ? "var(--yellow-color)" : "var(--red-color)";

class FrogBatteryCard extends HTMLElement {
  setConfig(config) {
    if (!config.entity) throw new Error("entity is required");
    this.config = config;
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this.key = null;
    if (this._hass) this.render();
  }

  set hass(hass) {
    this._hass = hass;
    this.render();
  }

  getCardSize() {
    return 1;
  }

  getGridOptions() {
    return { columns: 12, rows: 1 };
  }

  render() {
    const { entity, name, icon } = this.config;
    const state = this._hass?.states[entity]?.state;
    const charging = this._hass?.states[entity.replace("_level", "_state")]?.state === "charging";
    const number = Number.parseFloat(state);
    const level = Number.isFinite(number) ? Math.max(0, Math.min(100, number)) : null;
    // Render only when something shown has changed, hass is set on every state change in the house
    const key = `${state}|${charging}`;
    if (key === this.key) return;
    this.key = key;

    const color = colorOf(level);
    const text = level === null ? "Unavailable" : `${Math.round(level)}%${charging ? " · charging" : ""}`;
    this.shadowRoot.innerHTML = `<style>
      ha-card { height: 100%; box-sizing: border-box; padding: 0 12px; display: flex; align-items: center; gap: 10px; cursor: pointer; }
      .icon { flex: none; width: 36px; height: 36px; border-radius: 50%; display: flex; align-items: center; justify-content: center;
              color: ${color}; background: color-mix(in srgb, ${color} 20%, transparent); --mdc-icon-size: 20px; }
      .info { flex: 1; min-width: 0; }
      .line { display: flex; justify-content: space-between; gap: 8px; font-size: 14px; line-height: 18px; }
      .name { font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .level { color: var(--secondary-text-color); white-space: nowrap; }
      .bar { height: 6px; margin-top: 5px; border-radius: 3px; background: var(--divider-color); overflow: hidden; }
      .fill { height: 100%; width: ${level ?? 0}%; border-radius: 3px; background: ${color}; transition: width 0.4s; }
    </style>
    <ha-card>
      <div class="icon"><ha-icon icon="${icon || "mdi:battery"}"></ha-icon></div>
      <div class="info">
        <div class="line"><span class="name">${name || entity}</span><span class="level">${text}</span></div>
        <div class="bar"><div class="fill"></div></div>
      </div>
    </ha-card>`;
    this.shadowRoot.querySelector("ha-card").addEventListener("click", () =>
      this.dispatchEvent(new CustomEvent("hass-more-info", { detail: { entityId: entity }, bubbles: true, composed: true }))
    );
  }
}

if (!customElements.get("frog-battery-card")) customElements.define("frog-battery-card", FrogBatteryCard);
