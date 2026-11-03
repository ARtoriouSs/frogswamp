// Coming back to a dashboard tab can leave a calendar card measured at width 0, with squashed columns.
// HA doesn't measure it again, so this does after each page change. Loaded by frontend.extra_module_url
const DELAYS_MS = [200, 1000, 2500];

const calendars = (root, found = []) => {
  for (const element of root.querySelectorAll("*")) {
    if (element.localName === "ha-full-calendar") found.push(element);
    if (element.shadowRoot) calendars(element.shadowRoot, found);
  }
  return found;
};

const fix = () => {
  for (const element of calendars(document)) {
    const body = element.shadowRoot?.querySelector(".fc-daygrid-body");
    if (body?.style.width === "0px") (element.calendar ?? element._calendar)?.updateSize();
  }
};

const fixSoon = () => DELAYS_MS.forEach((delay) => setTimeout(fix, delay));

window.addEventListener("location-changed", fixSoon);
window.addEventListener("popstate", fixSoon);
fixSoon();
