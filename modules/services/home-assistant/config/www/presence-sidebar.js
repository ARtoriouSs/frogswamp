// Home/away status of each person, under Map in the sidebar. Loaded by frontend.extra_module_url in configuration.yaml
// Custom Sidebar items have only an icon and a name, so this is its own element
const PEOPLE = ["person.aleksander_cieslowski", "person.natallia_radziuk"];
const HIDDEN_FOR_USERS = ["Guest"];
const REFRESH_MS = 2000;

const STATUS = {
  home: { text: "Home", color: "var(--green-color, #4caf50)" },
  away: { text: "Away", color: "var(--orange-color, #ff9800)" },
  unknown: { text: "Unknown", color: "var(--secondary-text-color)" },
};

const statusOf = (state) => {
  if (state === "home") return STATUS.home;
  return !state || state === "unknown" || state === "unavailable" ? STATUS.unknown : STATUS.away;
};

const initialsOf = (name) =>
  name
    .split(" ")
    .map((word) => word[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();

class FrogPresence extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" }).innerHTML = `
      <style>
        :host {
          display: flex;
          flex-wrap: wrap;
          justify-content: center;
          gap: 8px 12px;
          padding: 8px 4px 12px;
          margin-bottom: 8px;
          border-bottom: 1px solid var(--divider-color);
        }
        .person {
          width: 48px;
          padding: 0;
          border: 0;
          background: none;
          color: inherit;
          cursor: pointer;
          font: inherit;
          font-size: 11px;
          line-height: 16px;
          text-align: center;
        }
        .avatar {
          width: 36px;
          height: 36px;
          margin: 0 auto 2px;
          border-radius: 50%;
          background: var(--primary-color) center / cover no-repeat;
          color: var(--text-primary-color);
          font-size: 14px;
          line-height: 36px;
        }
      </style>
    `;
  }

  update(hass) {
    PEOPLE.forEach((entityId, index) => {
      const entity = hass.states[entityId];
      const name = entity?.attributes.friendly_name ?? entityId;
      const picture = entity?.attributes.entity_picture;
      const status = statusOf(entity?.state);
      const person = this.personAt(index);
      person.title = `${name}: ${status.text}`;
      person.avatar.style.backgroundImage = picture ? `url("${hass.hassUrl(picture)}")` : "";
      person.avatar.textContent = picture ? "" : initialsOf(name);
      person.label.textContent = status.text;
      person.label.style.color = status.color;
    });
  }

  personAt(index) {
    let person = this.shadowRoot.querySelectorAll(".person")[index];
    if (!person) {
      person = document.createElement("button");
      person.className = "person";
      // HA's own person dialog, its map shows the person even at home. The /map page has no link to one person and hides people at home
      person.addEventListener("click", () =>
        this.dispatchEvent(
          new CustomEvent("hass-more-info", {
            detail: { entityId: PEOPLE[index] },
            bubbles: true,
            composed: true,
          })
        )
      );
      person.avatar = person.appendChild(document.createElement("div"));
      person.avatar.className = "avatar";
      person.label = person.appendChild(document.createElement("div"));
      this.shadowRoot.append(person);
    }
    return person;
  }
}
customElements.define("frog-presence", FrogPresence);

const widget = document.createElement("frog-presence");

// Polling also puts the widget back when the sidebar redraws its items
const refresh = () => {
  const home = document.querySelector("home-assistant");
  const hass = home?.hass;
  const sidebar = home?.shadowRoot
    ?.querySelector("home-assistant-main")
    ?.shadowRoot?.querySelector("ha-sidebar");
  // href is a property on these items, not an attribute, so a CSS selector can't match it
  const items = sidebar?.shadowRoot?.querySelectorAll("ha-list-item-base, ha-list-item-button") ?? [];
  const mapItem = Array.from(items).find((item) => item.href === "/map");
  if (!hass?.user || !mapItem || HIDDEN_FOR_USERS.includes(hass.user.name)) {
    widget.remove();
    return;
  }
  // Same CSS order as Map and right after it in the DOM keeps the two together
  widget.style.order = mapItem.style.order;
  if (mapItem.nextElementSibling !== widget) mapItem.after(widget);
  widget.update(hass);
};

setInterval(refresh, REFRESH_MS);
refresh();
