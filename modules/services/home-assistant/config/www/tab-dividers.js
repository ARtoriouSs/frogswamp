// Vertical lines between groups of tabs on the Main dashboard. Loaded by frontend.extra_module_url in configuration.yaml
// HA has no setting for this, so the style goes into the shadow root of the tab bar
const DASHBOARD = "/main-yaml";
const FIRST_TAB_OF_GROUP = ["climate", "monitor", "living-room", "guest"];
const REFRESH_MS = 1000;

// A line only when a visible tab comes before: Guest sees one tab and needs no line
const tabs = FIRST_TAB_OF_GROUP.map(
  (path) => `ha-tab-group-tab:not(.hide-tab) ~ ha-tab-group-tab[data-path="${path}"]`
);

const style = document.createElement("style");
style.textContent = `
  ${tabs.join(",\n")} {
    position: relative;
    margin-inline-start: 9px;
  }
  ${tabs.map((tab) => `${tab}::before`).join(",\n")} {
    content: "";
    position: absolute;
    inset-inline-start: -5px;
    top: 25%;
    height: 50%;
    width: 1px;
    background: var(--app-header-text-color, currentColor);
    opacity: 0.35;
  }
`;

// Polling also puts the style back when HA creates the tab bar again
const refresh = () => {
  if (location.pathname !== DASHBOARD && !location.pathname.startsWith(`${DASHBOARD}/`)) {
    style.remove();
    return;
  }
  const root = document
    .querySelector("home-assistant")
    ?.shadowRoot?.querySelector("home-assistant-main")
    ?.shadowRoot?.querySelector("ha-panel-lovelace")
    ?.shadowRoot?.querySelector("hui-root")?.shadowRoot;
  if (root && style.parentNode !== root) root.append(style);
};

setInterval(refresh, REFRESH_MS);
refresh();
