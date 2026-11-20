// The Wellness charts. Loaded by frontend.extra_module_url in configuration.yaml. All cards share one view: the range
// from input_select.wellness_range and a back/forward offset kept in this browser. They also share the hover time,
// so hovering one chart draws the same moment on all of them.
//
// Periods are calendar ones: Day is a day from midnight, Week is Monday to Sunday, Month a calendar month,
// 3 months three calendar months, Year a calendar year. Back goes one period further.
//
//   type: custom:frog-wellness-nav        the range buttons, ◀ period ▶ and Today. Pinned under the top bar once it
//   range: input_select.wellness_range    scrolls away
//
//   type: custom:frog-nutrition-total     a day's total against the goal, or the period's total against the goal
//   range: input_select.wellness_range    times the logged days (today and incomplete days left out)
//   sheet: {source: sensor.calories_sheet, updated: sensor.nutrition_last_read}
//   column: 1                             (the daily CSV column; keep_column 5 is the Full flag)
//   label: Calories
//   unit: kcal
//   icon: mdi:food-apple
//   goal: input_number.nutrition_goal_kcal
//   today: sensor.nutrition_today_kcal    (today's Day is live from this sensor)
//   steps: {0: green, 100: amber, 115: red}
//   skip_zero: true                       (optional, a day with 0 is not logged, e.g. water before it was tracked)
//
//   type: custom:frog-daily-chart         one value per day, or a live line for Day
//   title: Calories
//   info: text                            (optional, an ⓘ next to the title shows it)
//   range: input_select.wellness_range
//   day_note: text                        (optional, Day shows only this text)
//   stack: true                           (optional, the bar series are stacked)
//   sheet:                                (only for column series)
//     source: sensor.calories_sheet       (its url attribute is the daily CSV, meals_url the CSV of every meal with its
//                                         time and day: Day adds up the day's meals at their times)
//     date_column: 0
//     keep_column: 5                      (optional, only rows where it is TRUE are drawn)
//     updated: sensor.nutrition_last_read (optional, today's Day reads the meals again when it changes)
//   health_note: text                     (optional, shown when the Google data from nutrition-sync is missing)
//   updated: sensor.nutrition_last_read   (optional, today's Day reads the Google file again when it changes)
//   activity:                             (needed for a sleep series)
//     watch: sensor.watch_activity        (its asleep and exercise states)
//     sleep: sensor.pixel_7_sleep_segment (Health Connect segments, start and end attributes in ms)
//     health: true                        (optional, Google sleep stages first and Google workouts as exercise)
//   series:
//     - column: 1                         (a sheet column)
//       name: Eaten
//       unit: kcal
//       color: "#f59e0b"
//       bar: true                         (optional, bars instead of a line, not in Day)
//     - entity: sensor.pixel_7_total_calories_burned
//       stat: change                      (change for daily totals, or mean / min / max)
//       axis: right                       (optional, a second scale on the right)
//       skip_today: true                  (optional, today is not over yet)
//     - entity: [sensor.phone_steps, sensor.watch_steps]
//       stat: change                      (a list of entities draws the highest value of each day)
//     - entity: sensor.health_weight
//       measured:                         (optional, a day is the average of the measurements taken on it: the day's
//         sum: sensor.weight_sum          change of sum / the day's change of count. Days from before these sensors
//         count: sensor.weight_count      use stat)
//       day:                              (optional, how Day draws it. By default 5-minute statistics, hourly for days
//                                         more than 10 days ago, and change series add up through the day)
//         history: sensor.health_weight   (the raw states, each held until the next one)
//         time_in: sensor.watch_state     (or hours spent in a state since midnight, rising only while in it)
//         state: exercise
//                                         (day: false leaves the series out of Day)
//     - sleep: true                       (hours asleep per calendar day from the activity, Day rises while asleep.
//       entity: sensor.health_sleep       Days older than the 31 days of raw history use this entity's stat)
//       stat: max
//       unstaged: true                    (optional, only nights without Google sleep stages)
//     - health: sleep.deep_min            (a value of days.json from nutrition-sync, e.g. resting_hr, azm.total)
//       day:
//         health: heart_rate              (or burned, a running total, or exercise, minutes rising through workouts)
//
//   type: custom:frog-activity-timeline   sleep stages and workouts on one line, from the activity config above
//   title, info, range, activity
const DAY_MS = 24 * 60 * 60 * 1000;
const HOUR_MS = 60 * 60 * 1000;
const FIVE_MINUTES_MS = 5 * 60 * 1000;
const CACHE_MS = 10 * 60 * 1000;
// Today reads again when a watched entity changes, but not more often than this
const LIVE_RELOAD_MS = 20 * 1000;
// HA keeps raw states (recorder purge_keep_days) and 5-minute statistics this long
const RAW_DAYS = 31;
const FIVE_MINUTE_DAYS = 10;
const HEIGHT = 200;
const PAD = { left: 44, right: 12, top: 12, bottom: 24 };
const RIGHT_AXIS_WIDTH = 36;
const SLEEP_COLOR = "#8b5cf6";
const STAGES = [
  ["deep", "Deep", "#4c1d95"],
  ["rem", "REM", "#7c3aed"],
  ["light", "Light", "#a78bfa"],
  ["asleep", "Asleep", SLEEP_COLOR],
  ["awake", "Awake", "#ddd6fe"],
];
const STAGE_COLOR = Object.fromEntries(STAGES.map(([stage, , color]) => [stage, color]));
const STAGE_NAME = Object.fromEntries(STAGES.map(([stage, name]) => [stage, name]));
const HEALTH_URL = "/local/nutrition/health/";
const EXERCISE_COLOR = "#f97316";

const VIEW_EVENT = "frog-wellness-view";
const HOVER_EVENT = "frog-wellness-hover";
const view = { range: null, offset: 0, hover: null };
const notify = (name) => window.dispatchEvent(new CustomEvent(name));

// A new range starts again at the current period
const setRange = (range) => {
  if (!range || range === view.range) return;
  view.range = range;
  view.offset = 0;
  notify(VIEW_EVENT);
};

const setHover = (time) => {
  view.hover = time;
  notify(HOVER_EVENT);
};

const startOfDay = (time) => {
  const date = new Date(time);
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
};

// By the calendar, so a day with a clock change still ends at midnight
const addDays = (time, days) => {
  const date = new Date(time);
  date.setDate(date.getDate() + days);
  return date.getTime();
};

const monthStart = (year, month) => new Date(year, month, 1).getTime();

const period = (range, offset) => {
  const today = startOfDay(Date.now());
  const date = new Date(today);
  const year = date.getFullYear();
  const month = date.getMonth();
  let start;
  let end;
  if (range === "Day") (start = addDays(today, -offset)), (end = addDays(start, 1));
  else if (range === "Week") {
    start = addDays(today, -((date.getDay() + 6) % 7) - 7 * offset);
    end = addDays(start, 7);
  } else if (range === "3 months") (start = monthStart(year, month - 2 - 3 * offset)), (end = monthStart(year, month + 1 - 3 * offset));
  else if (range === "Year") (start = monthStart(year - offset, 0)), (end = monthStart(year - offset + 1, 0));
  else (start = monthStart(year, month - offset)), (end = monthStart(year, month - offset + 1));
  return { range: range || "Month", start, end, isDay: range === "Day", key: `${range}|${start}` };
};

const days = (p) => {
  const list = [];
  for (let day = p.start; day < p.end; day = addDays(day, 1)) list.push(day);
  return list;
};

const pad = (number) => String(number).padStart(2, "0");
const formatDay = (time) => `${pad(new Date(time).getDate())}.${pad(new Date(time).getMonth() + 1)}`;
const formatDate = (time) => `${formatDay(time)}.${new Date(time).getFullYear()}`;
const formatTime = (time) => new Date(time).toTimeString().slice(0, 5);
const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const formatMonth = (time, style = "short") => {
  const name = MONTHS[new Date(time).getMonth()];
  return style === "long" ? name : name.slice(0, 3);
};
const formatValue = (value) => (Math.abs(value) >= 100 ? Math.round(value) : Math.round(value * 10) / 10);
const isoDate = (time) => {
  const date = new Date(time);
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
};

const periodLabel = (p) => {
  const today = startOfDay(Date.now());
  if (p.range === "Day") {
    if (p.start === today) return "Today";
    if (p.start === addDays(today, -1)) return "Yesterday";
    return `${new Date(p.start).toLocaleDateString("en-GB", { weekday: "short" })} ${formatDate(p.start)}`;
  }
  const last = addDays(p.end, -1);
  if (p.range === "Week") return `${formatDay(p.start)} – ${formatDate(last)}`;
  if (p.range === "Year") return String(new Date(p.start).getFullYear());
  if (p.range === "3 months") return `${formatMonth(p.start)} – ${formatMonth(last)} ${new Date(last).getFullYear()}`;
  return `${formatMonth(p.start, "long")} ${new Date(p.start).getFullYear()}`;
};

// Shared by all cards, so a tab with several charts reads a file once
const fileCache = new Map();

const fetchFile = (url, maxAge, parse) => {
  const hit = fileCache.get(url);
  if (hit && Date.now() - hit.time < maxAge) return hit.promise;
  const promise = fetch(url, { cache: "no-store" }).then((response) => {
    if (!response.ok) throw new Error(`${url} answered ${response.status}`);
    return response.text().then(parse);
  });
  promise.catch(() => fileCache.delete(url));
  fileCache.set(url, { promise, time: Date.now() });
  return promise;
};

const fetchCsv = (url, maxAge = CACHE_MS) => fetchFile(url, maxAge, (text) => parseCsv(text));

// The Google data from nutrition-sync: days.json has a summary per day, day/<date>.json the details of the last 31 days.
// A missing file gives null
const loadHealthDays = (maxAge = CACHE_MS) => fetchFile(`${HEALTH_URL}days.json`, maxAge, JSON.parse).then((file) => file.days, () => null);

const loadHealthDay = (time) =>
  fetchFile(`${HEALTH_URL}day/${isoDate(time)}.json`, time >= startOfDay(Date.now()) ? 0 : CACHE_MS, JSON.parse).catch(() => null);

// {date: day file} for the days of the period that have one
const loadHealthDetails = (p) => {
  const first = Math.max(p.start, addDays(startOfDay(Date.now()), -(RAW_DAYS - 1)));
  const list = days({ start: first, end: Math.min(p.end, addDays(startOfDay(Date.now()), 1)) });
  return Promise.all(list.map(loadHealthDay)).then((files) => Object.fromEntries(list.map((day, i) => [isoDate(day), files[i]]).filter(([, file]) => file)));
};

// A value of a days.json day by its path, e.g. "sleep.deep_min"
const healthValue = (healthDays, date, path) => {
  let value = healthDays?.[date];
  for (const key of path.split(".")) value = value?.[key];
  return typeof value === "number" ? value : null;
};

const heartLine = (detail) => (detail?.heart_rate || []).map(([time, value]) => ({ time, value }));

// Hourly kcal from Google as a running total, each hour counted at its end
const burnedLine = (detail, start, until) => {
  const points = [{ time: start, value: 0 }];
  let total = 0;
  for (const [time, kcal] of detail?.burned || []) {
    if (time < start || time >= until) continue;
    points.push({ time: Math.min(time + HOUR_MS, until), value: (total += kcal), mark: true });
  }
  return points;
};

// Minutes in workouts since midnight: rises during a workout, flat otherwise
const exerciseLine = (detail, start, until) => {
  const points = [{ time: start, value: 0 }];
  let total = 0;
  for (const workout of [...(detail?.workouts || [])].sort((a, b) => a.start - b.start)) {
    const from = Math.max(workout.start, start);
    const to = Math.min(workout.end, until);
    if (to <= from) continue;
    points.push({ time: from, value: total });
    points.push({ time: to, value: (total += (to - from) / 60000), mark: true });
  }
  points.push({ time: until, value: total });
  return points;
};

const parseCsv = (text) => {
  const rows = [];
  let row = [];
  let cell = "";
  let quoted = false;
  for (let i = 0; i < text.length; i++) {
    const char = text[i];
    if (quoted) {
      if (char === '"' && text[i + 1] === '"') cell += text[++i];
      else if (char === '"') quoted = false;
      else cell += char;
    } else if (char === '"') quoted = true;
    else if (char === ",") (row.push(cell), (cell = ""));
    else if (char === "\n") (row.push(cell), rows.push(row), (row = []), (cell = ""));
    else if (char !== "\r") cell += char;
  }
  if (cell || row.length) (row.push(cell), rows.push(row));
  return rows;
};

// 2026-11-13, 13.11.2026 or 13/11/2026. A header row gives null and is skipped
const parseDate = (value) => {
  const text = (value || "").trim();
  let match = text.match(/^(\d{4})-(\d{1,2})-(\d{1,2})/);
  if (match) return new Date(+match[1], match[2] - 1, +match[3]);
  match = text.match(/^(\d{1,2})[./](\d{1,2})[./](\d{4})/);
  if (match) return new Date(+match[3], match[2] - 1, +match[1]);
  return null;
};

// A decimal comma and spaces between thousands are fine
const parseNumber = (value) => {
  const text = (value || "").replace(/[\s ]/g, "").replace(",", ".");
  if (!text) return null;
  const number = Number(text);
  return Number.isFinite(number) ? number : null;
};

const statTime = (stat) => (typeof stat.start === "number" ? stat.start : Date.parse(stat.start));

// One stat type of an entity per day
const byDay = (stats, type) => {
  const result = new Map();
  for (const stat of stats || []) {
    const value = stat[type];
    if (value !== null && value !== undefined) result.set(startOfDay(statTime(stat)), value);
  }
  return result;
};

// History entries: {s: state, a: attributes, lu: seconds}
const historyTime = (entry) => (entry.lu ?? entry.lc) * 1000;

// A line that holds each value until the next one. initial is the value at start, or null when there is none
const steps = (initial, events, start, until) => {
  const points = initial === null ? [] : [{ time: start, value: initial }];
  for (const { time, value } of events) {
    const at = Math.min(Math.max(time, start), until);
    if (points.length) points.push({ time: at, value: points[points.length - 1].value });
    points.push({ time: at, value, mark: true });
  }
  if (points.length) points.push({ time: until, value: points[points.length - 1].value });
  return points;
};

// The value of a Day line at a time: the last point before it
const valueAt = (line, time) => {
  let value = null;
  for (const point of line) {
    if (point.time > time) break;
    value = point.value;
  }
  return line.length && time <= line[line.length - 1].time ? value : null;
};

const clip = (intervals, from, to) =>
  intervals
    .filter((interval) => interval.to > from && interval.from < to)
    .map((interval) => ({ ...interval, from: Math.max(interval.from, from), to: Math.min(interval.to, to) }));

// Raw history from start to end. Sleep segments come in after the night, so it reads two more days
const loadActivity = (hass, activity, p) => {
  const rawStart = startOfDay(Date.now()) - RAW_DAYS * DAY_MS;
  const from = Math.max(p.start, rawStart);
  if (from >= p.end) return Promise.resolve({ sleep: [], exercise: [] });
  const google = activity.health ? loadHealthDetails({ start: addDays(from, -1), end: p.end }) : Promise.resolve({});
  const history = hass
    .callWS({
      type: "history/history_during_period",
      start_time: new Date(from).toISOString(),
      end_time: new Date(Math.min(Date.now(), addDays(p.end, 2))).toISOString(),
      entity_ids: [activity.watch, activity.sleep],
      minimal_response: false,
      no_attributes: false,
      significant_changes_only: false,
    });
  return Promise.all([history, google]).then(([history, google]) => activityIntervals(history, activity, google));
};

// Sleep and exercise as time intervals. A calendar day takes its sleep from the first source that has some on it:
// Google (with stages, needs the watch worn at night), the watch state, then the Health Connect segments.
// Exercise is the Google workouts with activity.health, otherwise the watch state
const activityIntervals = (history, activity, google = {}) => {
  const now = Date.now();
  const watch = { sleep: [], exercise: [] };
  const states = history[activity.watch] || [];
  states.forEach((entry, i) => {
    const kind = { asleep: "sleep", exercise: "exercise" }[entry.s];
    const from = historyTime(entry);
    const to = i + 1 < states.length ? historyTime(states[i + 1]) : now;
    if (kind && to > from) watch[kind].push({ from, to, source: "watch", stage: "asleep" });
  });
  const seen = new Set();
  const health = [];
  for (const entry of history[activity.sleep] || []) {
    const { start, end } = entry.a || {};
    if (!(start < end) || seen.has(`${start}-${end}`)) continue;
    seen.add(`${start}-${end}`);
    health.push({ from: start, to: Math.min(end, now), source: "Health Connect", stage: "asleep" });
  }
  const files = Object.values(google);
  const stages = files.flatMap((file) => file.sleep || []).map((stage) => ({ ...stage, source: "Google" }));
  const sleep = [];
  const all = [...stages, ...watch.sleep, ...health];
  if (all.length) {
    for (let day = startOfDay(Math.min(...all.map((interval) => interval.from))); day <= now; day = addDays(day, 1)) {
      const next = addDays(day, 1);
      const first = [stages, watch.sleep, health].map((list) => clip(list, day, next)).find((list) => list.length);
      sleep.push(...(first || []));
    }
  }
  const workouts = files.flatMap((file) => file.workouts || []).map((workout) => ({ from: workout.start, to: workout.end, workout }));
  return { sleep, exercise: activity.health ? workouts : watch.exercise };
};

const asleep = (intervals) => intervals.filter((interval) => interval.stage !== "awake");

const xAxis = (p, left, width) => (time) => left + ((time - p.start) / (p.end - p.start)) * width;

// Labels under the plot: hours for Day, months for the long ranges, days otherwise
const ticks = (p, plotWidth) => {
  if (p.isDay) return [0, 4, 8, 12, 16, 20].map((hour) => ({ time: p.start + hour * HOUR_MS, label: `${pad(hour)}:00` }));
  if (p.range === "3 months" || p.range === "Year") {
    const list = [];
    for (let month = p.start; month < p.end; month = new Date(new Date(month).setMonth(new Date(month).getMonth() + 1)).getTime())
      list.push({ time: month, label: formatMonth(month), left: true });
    return list;
  }
  const all = days(p);
  const every = Math.ceil(all.length / Math.max(1, Math.floor(plotWidth / 48)));
  return all.filter((_, i) => i % every === 0).map((day) => ({ time: day + DAY_MS / 2, label: formatDay(day) }));
};

const STYLE = `
  :host { display: block; }
  ha-card { padding: 12px 16px; position: relative; }
  .title { font-size: 16px; font-weight: 500; margin-bottom: 8px; display: flex; align-items: center; gap: 6px; }
  .info { cursor: pointer; color: var(--secondary-text-color); font-size: 14px; }
  .info-text { font-size: 12px; color: var(--secondary-text-color); margin: -4px 0 8px; display: none; }
  .info-text.open, .title:hover + .info-text { display: block; }
  .legend, .hint { display: flex; flex-wrap: wrap; gap: 4px 12px; font-size: 12px; color: var(--secondary-text-color); margin-bottom: 4px; }
  .legend i, .tip i { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 4px; }
  .note { height: ${HEIGHT}px; display: flex; align-items: center; justify-content: center; text-align: center; color: var(--secondary-text-color); }
  .plot { position: relative; }
  svg { display: block; overflow: visible; touch-action: pan-y; }
  text { fill: var(--secondary-text-color); font-size: 11px; }
  .grid { stroke: var(--divider-color); }
  .track { fill: var(--divider-color); opacity: 0.4; }
  .cursor { stroke: var(--primary-text-color); stroke-width: 1; stroke-dasharray: 3 3; opacity: 0.7; }
  .tip { position: absolute; top: 0; pointer-events: none; background: var(--card-background-color); color: var(--primary-text-color);
    border: 1px solid var(--divider-color); border-radius: 6px; padding: 4px 8px; font-size: 12px; white-space: nowrap;
    box-shadow: 0 2px 6px rgba(0, 0, 0, 0.2); display: none; z-index: 1; }
  .tip b { display: block; margin-bottom: 2px; }
`;

// The parts every card shares: the view, hover, the ⓘ and the title
class WellnessCard extends HTMLElement {
  setConfig(config) {
    this.config = config;
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this.data = null;
    this.error = null;
    this.loaded = 0;
    this.period = null;
    this.watchKey = null;
  }

  set hass(hass) {
    this._hass = hass;
    if (this.config.range) setRange(hass.states[this.config.range]?.state);
    this.sync();
  }

  connectedCallback() {
    this.onView = () => this.sync();
    this.onHover = () => this.showHover(view.hover);
    window.addEventListener(VIEW_EVENT, this.onView);
    window.addEventListener(HOVER_EVENT, this.onHover);
    this.resize = new ResizeObserver(() => this.render());
    this.resize.observe(this);
    // Today's lines end at now
    this.timer = setInterval(() => view.offset === 0 && this.render(), 60 * 1000);
  }

  disconnectedCallback() {
    window.removeEventListener(VIEW_EVENT, this.onView);
    window.removeEventListener(HOVER_EVENT, this.onHover);
    this.resize?.disconnect();
    clearInterval(this.timer);
  }

  getCardSize() {
    return 5;
  }

  getGridOptions() {
    return { columns: "full", rows: "auto" };
  }

  // A new period loads at once. The current one reads again now and then, and today also when a watched entity changes
  sync() {
    if (!this._hass || !this.config || !this.ready()) return;
    const p = period(view.range, view.offset);
    if (p.key !== this.period?.key) {
      this.period = p;
      this.data = null;
      this.render();
      this.load(p);
      return;
    }
    if (view.offset !== 0 || this.idle(p)) return;
    const age = Date.now() - this.loaded;
    const key = this.watched(p).map((id) => this._hass.states[id]?.last_updated).join();
    if (age > (p.isDay ? FIVE_MINUTES_MS : CACHE_MS) || (key !== this.watchKey && age > LIVE_RELOAD_MS)) this.load(p);
  }

  idle() {
    return false;
  }

  ready() {
    return true;
  }

  load(p) {
    this.loaded = Date.now();
    this.watchKey = this.watched(p).map((id) => this._hass.states[id]?.last_updated).join();
    this.fetch(p).then(
      (data) => p.key === this.period.key && ((this.data = data), (this.error = null), this.render()),
      (error) => p.key === this.period.key && ((this.error = error.message || String(error)), this.render())
    );
  }

  header() {
    const { title, info } = this.config;
    if (!title) return "";
    // Open on hover, or on a tap that stays open over the re-renders
    const icon = info ? `<span class="info">ⓘ</span>` : "";
    const text = info ? `<div class="info-text${this.infoOpen ? " open" : ""}">${info}</div>` : "";
    return `<div class="title">${title}${icon}</div>${text}`;
  }

  // Mouse hover clears on leave, a touch stays until the next one
  wire() {
    const root = this.shadowRoot;
    root.querySelector(".info")?.addEventListener("click", () => {
      this.infoOpen = !this.infoOpen;
      root.querySelector(".info-text").classList.toggle("open", this.infoOpen);
    });
    const svg = root.querySelector("svg.chart");
    if (!svg || !this.plot) return;
    const move = (event) => {
      const { left, width, p } = this.plot;
      const x = event.clientX - svg.getBoundingClientRect().left;
      const time = p.start + ((x - left) / width) * (p.end - p.start);
      setHover(time >= p.start && time < p.end ? time : null);
    };
    svg.addEventListener("pointermove", move);
    svg.addEventListener("pointerdown", move);
    svg.addEventListener("pointerleave", (event) => event.pointerType === "mouse" && setHover(null));
    this.showHover(view.hover);
  }

  // The dashed line and a box with the values at that time
  showHover(time) {
    const root = this.shadowRoot;
    const cursor = root?.querySelector("line.cursor");
    const tip = root?.querySelector(".tip");
    if (!cursor || !tip || !this.plot) return;
    const { p, top, bottom } = this.plot;
    const hit = time === null || time < p.start || time >= p.end ? null : this.hoverAt(time);
    if (!hit) {
      cursor.style.display = tip.style.display = "none";
      return;
    }
    cursor.setAttribute("x1", hit.x);
    cursor.setAttribute("x2", hit.x);
    cursor.setAttribute("y1", top);
    cursor.setAttribute("y2", bottom);
    cursor.style.display = "";
    tip.innerHTML = hit.html;
    tip.style.display = "block";
    const width = this.plot.svgWidth;
    const tipWidth = tip.offsetWidth;
    tip.style.left = `${hit.x + 10 + tipWidth > width ? Math.max(0, hit.x - 10 - tipWidth) : hit.x + 10}px`;
  }
}

class FrogDailyChart extends WellnessCard {
  setConfig(config) {
    if (!Array.isArray(config.series)) throw new Error("series is required");
    if (config.series.some((line) => line.column !== undefined) && !config.sheet?.source)
      throw new Error("sheet.source is required for column series");
    if (config.series.some((line) => line.sleep) && !(config.activity?.watch && config.activity?.sleep))
      throw new Error("activity.watch and activity.sleep are required for a sleep series");
    super.setConfig(config);
  }

  get hasSleep() {
    return this.config.series.some((line) => line.sleep);
  }

  // Series that read days.json in the daily ranges
  get hasHealth() {
    return this.config.series.some((line) => line.health || line.unstaged);
  }

  idle(p) {
    return p.isDay && this.config.day_note;
  }

  // Right after an HA restart the CSV sensor isn't there yet, and loading without it would cache an empty chart
  ready() {
    const { sheet } = this.config;
    return !sheet || Boolean(this._hass.states[sheet.source]?.attributes.url);
  }

  watched(p) {
    if (!p.isDay) return [];
    const { sheet = {}, series, activity = {} } = this.config;
    const sleep = this.hasSleep ? [activity.watch, activity.sleep] : [];
    return [sheet.updated, this.config.updated, ...sleep, ...series.flatMap((line) => [line.day?.history, line.day?.time_in])].filter(Boolean);
  }

  loadSleep(p) {
    return this.hasSleep ? loadActivity(this._hass, this.config.activity, p) : Promise.resolve({ sleep: [], exercise: [] });
  }

  fetch(p) {
    if (this.idle(p)) return Promise.resolve({});
    return p.isDay ? this.fetchDay(p) : this.fetchDays(p);
  }

  // Daily statistics of the period, the daily CSV and the sleep intervals
  fetchDays(p) {
    const { sheet, series } = this.config;
    const url = sheet ? this._hass.states[sheet.source]?.attributes.url : null;
    const entities = series.filter((line) => line.entity);
    const ids = entities.flatMap((line) => [line.entity, line.measured?.sum, line.measured?.count].flat().filter(Boolean));
    const types = entities.flatMap((line) => [line.stat || "mean", ...(line.measured ? ["change"] : [])]);
    const stats = ids.length
      ? this._hass.callWS({
          type: "recorder/statistics_during_period",
          start_time: new Date(p.start).toISOString(),
          end_time: new Date(p.end).toISOString(),
          statistic_ids: [...new Set(ids)],
          period: "day",
          types: [...new Set(types)],
        })
      : Promise.resolve({});
    const rows = url ? fetchCsv(url) : Promise.resolve([]);
    const health = this.hasHealth ? loadHealthDays() : Promise.resolve(undefined);
    return Promise.all([rows, stats, this.loadSleep(p), health]).then(([rows, stats, activity, health]) => ({ rows, stats, activity, health }));
  }

  // 5-minute statistics (hourly after 10 days), raw states, the meals and the sleep intervals of one day
  fetchDay(p) {
    const { sheet, series } = this.config;
    const statLines = series.filter((line) => line.entity && !line.day && !line.sleep && !line.health);
    const historyIds = [...new Set(series.flatMap((line) => [line.day?.history, line.day?.time_in]).filter(Boolean))];
    const fine = p.start >= addDays(startOfDay(Date.now()), -(FIVE_MINUTE_DAYS - 1));
    const mealsUrl =
      sheet && series.some((line) => line.column !== undefined && line.day !== false)
        ? this._hass.states[sheet.source]?.attributes.meals_url
        : null;
    const stats = statLines.length
      ? this._hass.callWS({
          type: "recorder/statistics_during_period",
          start_time: new Date(p.start).toISOString(),
          end_time: new Date(p.end).toISOString(),
          statistic_ids: [...new Set(statLines.flatMap((line) => [line.entity].flat()))],
          period: fine ? "5minute" : "hour",
          types: [...new Set(statLines.map((line) => line.stat || "mean"))],
        })
      : Promise.resolve({});
    const history = historyIds.length
      ? this._hass.callWS({
          type: "history/history_during_period",
          start_time: new Date(p.start).toISOString(),
          end_time: new Date(p.end).toISOString(),
          entity_ids: historyIds,
          minimal_response: true,
          no_attributes: true,
          significant_changes_only: false,
        })
      : Promise.resolve({});
    // Today's meals change all the time, older ones come from the shared cache
    const meals = mealsUrl ? fetchCsv(mealsUrl, view.offset === 0 ? 0 : CACHE_MS) : Promise.resolve(null);
    const detail = series.some((line) => line.day?.health) ? loadHealthDay(p.start) : Promise.resolve(null);
    return Promise.all([stats, history, meals, this.loadSleep(p), detail]).then(([stats, history, meals, activity, detail]) => ({
      stats,
      history,
      meals,
      activity,
      detail,
      step: fine ? FIVE_MINUTES_MS : HOUR_MS,
    }));
  }

  dayLines(p) {
    const until = Math.min(Date.now(), p.end);
    return this.config.series.map((line) => {
      if (line.day === false) return [];
      if (line.column !== undefined) return this.mealLine(line, p, until);
      if (line.sleep) return this.sleepLine(p.start, until);
      if (line.day?.health === "heart_rate") return heartLine(this.data.detail);
      if (line.day?.health === "burned") return burnedLine(this.data.detail, p.start, until);
      if (line.day?.health === "exercise") return exerciseLine(this.data.detail, p.start, until);
      if (line.health) return [];
      if (line.day?.history) return this.historyLine(line, p.start, until);
      if (line.day?.time_in) return this.timeInLine(line, p.start, until);
      return this.statLine(line);
    });
  }

  // The running total of the day's meals (by the 04:00 rule), a step at each meal
  mealLine(line, p, until) {
    if (!this.data.meals) return [];
    const scale = line.scale ?? 1;
    const day = isoDate(p.start);
    let total = 0;
    const events = this.data.meals
      .filter((row) => row[5] === day)
      .map((row) => ({ time: Date.parse(row[0]), value: parseNumber(row[line.column]) }))
      .filter((meal) => Number.isFinite(meal.time) && meal.value !== null)
      .sort((a, b) => a.time - b.time)
      .map((meal) => ({ time: meal.time, value: (total += meal.value) * scale }));
    return steps(0, events, p.start, until);
  }

  historyLine(line, start, until) {
    const scale = line.scale ?? 1;
    const values = (this.data.history[line.day.history] || [])
      .map((entry) => ({ time: historyTime(entry), value: parseFloat(entry.s) * scale }))
      .filter((entry) => Number.isFinite(entry.value));
    const initial = values.length && values[0].time <= start ? values.shift().value : null;
    return steps(initial, values, start, until);
  }

  // Hours in a state since midnight: rises while in it, flat otherwise
  timeInLine(line, start, until) {
    const entries = this.data.history[line.day.time_in] || [];
    const points = [{ time: start, value: 0 }];
    let total = 0;
    entries.forEach((entry, i) => {
      const from = Math.max(historyTime(entry), start);
      const to = i + 1 < entries.length ? Math.min(historyTime(entries[i + 1]), until) : until;
      if (to <= from) return;
      if (entry.s === line.day.state) total += (to - from) / HOUR_MS;
      points.push({ time: to, value: total });
    });
    return points;
  }

  sleepLine(start, until) {
    const points = [{ time: start, value: 0 }];
    let total = 0;
    for (const interval of asleep(clip(this.data.activity.sleep, start, until)).sort((a, b) => a.from - b.from)) {
      points.push({ time: interval.from, value: total });
      total += (interval.to - interval.from) / HOUR_MS;
      points.push({ time: interval.to, value: total, mark: true });
    }
    points.push({ time: until, value: total });
    return points;
  }

  // A change series is a running total from midnight, a list of entities takes the highest total at each time
  statLine(line) {
    const scale = line.scale ?? 1;
    const highest = new Map();
    for (const entity of [line.entity].flat()) {
      let total = 0;
      for (const stat of this.data.stats[entity] || []) {
        const value = stat[line.stat || "mean"];
        if (value === null || value === undefined) continue;
        const time = statTime(stat) + this.data.step;
        const point = line.stat === "change" ? (total += value) : value;
        highest.set(time, Math.max(highest.get(time) ?? -Infinity, point * scale));
      }
    }
    return [...highest].map(([time, value]) => ({ time, value })).sort((a, b) => a.time - b.time);
  }

  // One point per day: {time: midnight, value}
  dailyLines(p) {
    const { sheet = {}, series } = this.config;
    const today = startOfDay(Date.now());
    return series.map((line) => {
      const points = [];
      const add = (time, value) => {
        if (value === null || value === undefined || time < p.start || time >= p.end) return;
        if (line.skip_today && time === today) return;
        points.push({ time, value: value * (line.scale ?? 1) });
      };
      if (line.column !== undefined) {
        for (const row of this.data.rows) {
          const date = parseDate(row[sheet.date_column ?? 0]);
          if (!date) continue;
          if (sheet.keep_column !== undefined && (row[sheet.keep_column] || "").trim().toUpperCase() !== "TRUE") continue;
          add(date.getTime(), parseNumber(row[line.column]));
        }
      } else if (line.health) {
        for (const day of days(p)) add(day, healthValue(this.data.health, isoDate(day), line.health));
      } else if (line.sleep) {
        // Raw history covers the last 31 days, older days use the entity's daily stat
        const rawStart = today - RAW_DAYS * DAY_MS;
        const hours = new Map();
        const staged = (time) => line.unstaged && this.data.health?.[isoDate(time)]?.sleep?.staged;
        for (const interval of asleep(this.data.activity.sleep)) {
          if (staged(interval.from)) continue;
          const day = startOfDay(interval.from);
          hours.set(day, (hours.get(day) ?? 0) + (interval.to - interval.from) / HOUR_MS);
        }
        byDay(this.data.stats[line.entity], line.stat || "max").forEach((value, time) => time < rawStart && add(time, value));
        hours.forEach((value, time) => time >= rawStart && add(time, value));
      } else if (line.measured) {
        // A day with the counter but no new measurement gets no point
        const means = byDay(this.data.stats[line.entity], line.stat || "mean");
        const sums = byDay(this.data.stats[line.measured.sum], "change");
        const counts = byDay(this.data.stats[line.measured.count], "change");
        for (const time of new Set([...means.keys(), ...counts.keys()])) {
          if (!counts.has(time)) add(time, means.get(time));
          else if (counts.get(time) > 0) add(time, (sums.get(time) ?? 0) / counts.get(time));
        }
      } else {
        const highest = new Map();
        for (const entity of [line.entity].flat()) {
          byDay(this.data.stats[entity], line.stat || "mean").forEach((value, time) =>
            highest.set(time, Math.max(highest.get(time) ?? -Infinity, value))
          );
        }
        highest.forEach((value, time) => add(time, value));
      }
      return points.sort((a, b) => a.time - b.time);
    });
  }

  // Series drawn as bars: only in the daily ranges
  isBar(index) {
    return !this.period.isDay && Boolean(this.config.series[index].bar);
  }

  shown(p) {
    return this.config.series.filter((line) => !(p.isDay && line.day === false));
  }

  render() {
    const root = this.shadowRoot;
    if (!root || !this.config || !this.period) return;
    const p = this.period;
    const width = Math.max(this.clientWidth - 32, 200);
    this.plot = null;
    let body;
    if (p.isDay && this.config.day_note) body = `<div class="note">${this.config.day_note}</div>`;
    else if (this.error) body = `<div class="note">${this.error}</div>`;
    else if (!this.data) body = `<div class="note">Loading…</div>`;
    // Only when Google is all the chart has, a chart with other sources draws those
    else if (!p.isDay && this.data.health === null && this.config.series.every((line) => line.health))
      body = `<div class="note">${this.config.health_note || "No Google data yet"}</div>`;
    else {
      this.lines = p.isDay ? this.dayLines(p) : this.dailyLines(p);
      body = this.lines.some((line) => line.length)
        ? this.chart(width, p, this.lines)
        : `<div class="note">No data for this period</div>`;
    }
    const shown = p.isDay && this.config.day_note ? [] : this.shown(p);
    const legend =
      shown.length > 1
        ? `<div class="legend">${shown
            .map((line) => `<span><i style="background:${line.color}"></i>${line.name}${line.unit ? `, ${line.unit}` : ""}</span>`)
            .join("")}</div>`
        : "";
    root.innerHTML = `<style>${STYLE}</style><ha-card>${this.header()}${legend}${body}</ha-card>`;
    this.wire();
  }

  scale(lines, axis) {
    const { series, stack } = this.config;
    const onAxis = (index) => (series[index].axis || "left") === axis;
    const values = [];
    const stacked = new Map();
    lines.forEach((line, index) => {
      if (!onAxis(index)) return;
      for (const point of line) {
        if (stack && this.isBar(index)) stacked.set(point.time, (stacked.get(point.time) ?? 0) + point.value);
        else values.push(point.value);
      }
    });
    values.push(...stacked.values());
    if (!values.length) return null;
    const low = Math.min(...values);
    const high = Math.max(...values);
    const margin = (high - low) * 0.1 || Math.abs(high) * 0.1 || 1;
    const fromZero = lines.some((_, index) => onAxis(index) && this.isBar(index));
    return { low: fromZero ? 0 : Math.max(0, low - margin), high: high + margin };
  }

  chart(width, p, lines) {
    const { series, stack } = this.config;
    const scales = { left: this.scale(lines, "left"), right: this.scale(lines, "right") };
    const right = scales.right ? RIGHT_AXIS_WIDTH : PAD.right;
    const plotWidth = width - PAD.left - right;
    const plotHeight = HEIGHT - PAD.top - PAD.bottom;
    const x = xAxis(p, PAD.left, plotWidth);
    // A daily value sits in the middle of its day
    const at = (time) => (p.isDay ? x(time) : x(time + DAY_MS / 2));
    const y = (value, axis) => {
      const { low, high } = scales[axis] || scales.left || scales.right;
      return PAD.top + (1 - (value - low) / (high - low)) * plotHeight;
    };
    this.plot = { p, left: PAD.left, width: plotWidth, top: PAD.top, bottom: PAD.top + plotHeight, svgWidth: width, x, at };

    let svg = "";
    for (let i = 0; i <= 4; i++) {
      const top = PAD.top + (plotHeight * (4 - i)) / 4;
      svg += `<line class="grid" x1="${PAD.left}" x2="${width - right}" y1="${top}" y2="${top}"/>`;
      for (const [axis, anchor, position] of [["left", "end", PAD.left - 6], ["right", "start", width - right + 6]]) {
        const scale = scales[axis];
        if (!scale) continue;
        const value = scale.low + ((scale.high - scale.low) * i) / 4;
        svg += `<text x="${position}" y="${top + 4}" text-anchor="${anchor}">${formatValue(value)}</text>`;
      }
    }
    for (const tick of ticks(p, plotWidth))
      svg += `<text x="${x(tick.time)}" y="${HEIGHT - 6}" text-anchor="${tick.left ? "start" : "middle"}">${tick.label}</text>`;

    // Bars side by side in each day, or one stacked column
    const bars = series.map((_, index) => index).filter((index) => this.isBar(index));
    if (bars.length) {
      const slot = x(p.start + DAY_MS) - x(p.start);
      const columns = stack ? 1 : bars.length;
      const barWidth = Math.max(1, (slot * (stack ? 0.7 : 0.8)) / columns - (columns > 1 && slot > 12 ? 1 : 0));
      const base = new Map();
      bars.forEach((index, column) => {
        const { color, axis = "left" } = series[index];
        for (const point of lines[index]) {
          const left = x(point.time) + (slot - barWidth * columns) / 2 + (stack ? 0 : column * barWidth);
          const below = stack ? base.get(point.time) ?? 0 : 0;
          const top = y(below + point.value, axis);
          svg += `<rect x="${left}" y="${top}" width="${barWidth}" height="${Math.max(0, y(below, axis) - top)}" fill="${color}"/>`;
          if (stack) base.set(point.time, below + point.value);
        }
      });
    }

    // Lines join every point, also over days without a value. Dots for daily lines with few points, on Day where a value came in
    lines.forEach((line, index) => {
      if (this.isBar(index) || !line.length) return;
      const { color, axis = "left" } = series[index];
      const path = line.map((point, i) => `${i ? "L" : "M"}${at(point.time)},${y(point.value, axis)}`).join("");
      svg += `<path d="${path}" fill="none" stroke="${color}" stroke-width="2" stroke-linejoin="round"/>`;
      const dots = p.isDay ? line.filter((point) => point.mark) : line.length > 60 ? [] : line;
      for (const point of dots) svg += `<circle cx="${at(point.time)}" cy="${y(point.value, axis)}" r="3" fill="${color}"/>`;
    });
    svg += `<line class="cursor" style="display:none"/>`;
    return `<div class="plot"><svg class="chart" width="${width}" height="${HEIGHT}" viewBox="0 0 ${width} ${HEIGHT}">${svg}</svg><div class="tip"></div></div>`;
  }

  // The day under the pointer in the daily ranges, the exact time on Day
  hoverAt(time) {
    const { p, at } = this.plot;
    const day = startOfDay(time);
    const rows = this.config.series
      .map((line, index) => {
        if (p.isDay && line.day === false) return "";
        const points = this.lines[index];
        const value = p.isDay ? valueAt(points, time) : points.find((point) => point.time === day)?.value;
        const text = value === null || value === undefined ? "–" : `${formatValue(value)}${line.unit ? ` ${line.unit}` : ""}`;
        return `<div><i style="background:${line.color}"></i>${line.name}: ${text}</div>`;
      })
      .join("");
    return { x: p.isDay ? at(time) : at(day), html: `<b>${p.isDay ? formatTime(time) : formatDay(day)}</b>${rows}` };
  }
}

const escape = (text) => String(text).replace(/[&<>"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[char]);

// The hover rows of a workout
const workoutText = (workout) => {
  const parts = [`${workout.active_min} min`];
  if (workout.kcal !== null && workout.kcal !== undefined) parts.push(`${Math.round(workout.kcal)} kcal`);
  if (workout.distance_m) parts.push(`${(workout.distance_m / 1000).toFixed(2)} km`);
  if (workout.avg_hr) parts.push(`${Math.round(workout.avg_hr)} bpm`);
  const zones = Object.entries(workout.zone_min || {})
    .filter(([, minutes]) => minutes > 0)
    .map(([zone, minutes]) => `${zone} ${Math.round(minutes)}`)
    .join(", ");
  return `${escape(workout.name || workout.type)}: ${parts.join(", ")}${zones ? `<br>Heart rate zones, min: ${zones}` : ""}`;
};

// Sleep stages and exercise as blocks on one line
class FrogActivityTimeline extends WellnessCard {
  setConfig(config) {
    if (!config.activity?.watch || !config.activity?.sleep) throw new Error("activity.watch and activity.sleep are required");
    super.setConfig(config);
  }

  getCardSize() {
    return 2;
  }

  watched() {
    return [this.config.activity.watch, this.config.activity.sleep, this.config.updated].filter(Boolean);
  }

  fetch(p) {
    return loadActivity(this._hass, this.config.activity, p);
  }

  render() {
    const root = this.shadowRoot;
    if (!root || !this.config || !this.period) return;
    const p = this.period;
    const width = Math.max(this.clientWidth - 32, 200);
    const height = 28;
    const until = Math.min(Date.now(), p.end);
    const rawStart = startOfDay(Date.now()) - RAW_DAYS * DAY_MS;
    this.plot = null;
    let body;
    if (this.error) body = `<div class="note">${this.error}</div>`;
    else if (!this.data) body = `<div class="note" style="height:${height + 20}px">Loading…</div>`;
    else if (p.end <= rawStart)
      body = `<div class="note" style="height:${height + 20}px">HA keeps raw states for ${RAW_DAYS} days, there is nothing for this period</div>`;
    else {
      const plotWidth = width - PAD.left - PAD.right;
      const x = xAxis(p, PAD.left, plotWidth);
      this.plot = { p, left: PAD.left, width: plotWidth, top: 0, bottom: height, svgWidth: width, x };
      let svg = `<rect class="track" x="${PAD.left}" y="0" width="${plotWidth}" height="${height}" rx="3"/>`;
      const blocks = [
        ...this.data.sleep.map((interval) => [interval, STAGE_COLOR[interval.stage] || SLEEP_COLOR]),
        ...this.data.exercise.map((interval) => [interval, EXERCISE_COLOR]),
      ];
      for (const [interval, color] of blocks)
        for (const part of clip([interval], p.start, until)) {
          const left = x(part.from);
          svg += `<rect x="${left}" y="0" width="${Math.max(x(part.to) - left, 1)}" height="${height}" fill="${color}"/>`;
        }
      for (const tick of ticks(p, plotWidth))
        svg += `<text x="${x(tick.time)}" y="${height + 16}" text-anchor="${tick.left ? "start" : "middle"}">${tick.label}</text>`;
      svg += `<line class="cursor" style="display:none"/>`;
      body = `<div class="plot"><svg class="chart" width="${width}" height="${height + 20}" viewBox="0 0 ${width} ${height + 20}">${svg}</svg><div class="tip"></div></div>`;
    }
    const hint = p.start < rawStart && p.end > rawStart ? `<div class="hint">Only the last ${RAW_DAYS} days: HA keeps raw states that long</div>` : "";
    root.innerHTML = `<style>${STYLE}</style><ha-card>${this.header()}
      <div class="legend">${this.legend()}</div>
      ${body}${hint}</ha-card>`;
    this.wire();
  }

  // Only the sleep stages that are in the data, so a night without the watch shows just Sleep
  legend() {
    const seen = new Set((this.data?.sleep || []).map((interval) => interval.stage));
    const stages = STAGES.filter(([stage]) => seen.has(stage) && stage !== "asleep");
    const sleep = stages.length ? stages.map(([, name, color]) => [name, color]) : [];
    if (!stages.length || seen.has("asleep")) sleep.push([stages.length ? "Asleep" : "Sleep", SLEEP_COLOR]);
    return [...sleep, ["Exercise", EXERCISE_COLOR]].map(([name, color]) => `<span><i style="background:${color}"></i>${name}</span>`).join("");
  }

  hoverAt(time) {
    const { x } = this.plot;
    const inside = (interval) => interval.from <= time && time < interval.to;
    const span = (interval) => `${formatTime(interval.from)}–${formatTime(interval.to)}`;
    const rows = [];
    const sleep = this.data.sleep.find(inside);
    if (sleep) {
      const name = sleep.stage === "asleep" ? "Sleep" : `Sleep, ${STAGE_NAME[sleep.stage]}`;
      const color = STAGE_COLOR[sleep.stage] || SLEEP_COLOR;
      rows.push(`<div><i style="background:${color}"></i>${name} ${span(sleep)}, ${formatValue((sleep.to - sleep.from) / HOUR_MS)} h (${sleep.source})</div>`);
    }
    const exercise = this.data.exercise.find(inside);
    if (exercise)
      rows.push(
        `<div><i style="background:${EXERCISE_COLOR}"></i>${exercise.workout ? workoutText(exercise.workout) : "Exercise"} ${span(exercise)}${
          exercise.workout ? "" : `, ${formatValue((exercise.to - exercise.from) / HOUR_MS)} h`
        }</div>`
      );
    if (!rows.length) rows.push("<div>No sleep or exercise</div>");
    return { x: x(time), html: `<b>${this.period.isDay ? formatTime(time) : `${formatDay(time)} ${formatTime(time)}`}</b>${rows.join("")}` };
  }
}

// The total of one CSV column for a day, or for the period's logged days: {value, days}. A period leaves out today,
// which isn't over, and the days marked incomplete. A day shows its own row and says if it is incomplete
const nutritionTotal = (rows, p, { column, keepColumn = 5, skipZero = false, today = startOfDay(Date.now()) }) => {
  let value = null;
  let days = 0;
  let incomplete = false;
  for (const row of rows) {
    const date = parseDate(row[0]);
    if (!date) continue;
    const time = date.getTime();
    if (time < p.start || time >= p.end) continue;
    const number = parseNumber(row[column]);
    if (number === null || (skipZero && number === 0)) continue;
    const full = (row[keepColumn] || "").trim().toUpperCase() === "TRUE";
    if (p.isDay) {
      incomplete = !full;
    } else if (!full || time >= today) continue;
    value = (value ?? 0) + number;
    days += 1;
  }
  return incomplete ? { value, days, incomplete } : { value, days };
};

const stepColor = (steps, percent) => {
  let color = null;
  for (const [step, name] of Object.entries(steps || {}).sort((a, b) => a[0] - b[0])) if (percent >= Number(step)) color = name;
  return color;
};

const cssColor = (name) => (!name ? "var(--disabled-color)" : name.startsWith("#") ? name : `var(--${name}-color)`);
const formatAmount = (value) => Math.round(value).toLocaleString("ru-RU");

class FrogNutritionTotal extends WellnessCard {
  setConfig(config) {
    if (config.column === undefined || !config.sheet?.source) throw new Error("column and sheet.source are required");
    super.setConfig(config);
  }

  getCardSize() {
    return 1;
  }

  getGridOptions() {
    return { columns: 6, rows: 1 };
  }

  ready() {
    return Boolean(this._hass.states[this.config.sheet.source]?.attributes.url);
  }

  watched(p) {
    const live = p.isDay && view.offset === 0;
    return [this.config.sheet.updated, this.config.goal, live && this.config.today].filter(Boolean);
  }

  fetch() {
    // All the cards share one read; today's totals change, so they are read again after a short while
    return fetchCsv(this._hass.states[this.config.sheet.source].attributes.url, view.offset === 0 ? LIVE_RELOAD_MS : CACHE_MS);
  }

  set hass(hass) {
    super.hass = hass;
    // The live sensor and the goal can change without a new read
    const key = [this.config.today, this.config.goal].map((id) => hass.states[id]?.state).join();
    if (key !== this.liveKey) (this.liveKey = key), this.render();
  }

  render() {
    const root = this.shadowRoot;
    if (!root || !this.config || !this.period || !this._hass) return;
    const { label, unit = "", icon, goal: goalId, today, column, keep_column: keepColumn = 5, skip_zero: skipZero, steps } = this.config;
    const p = this.period;
    // Short, the range bar above says which period
    const where = p.isDay ? (view.offset === 0 ? "today" : formatDay(p.start)) : view.range;
    let total = this.data ? nutritionTotal(this.data, p, { column, keepColumn, skipZero }) : { value: null, days: 0 };
    const liveToday = p.isDay && view.offset === 0 && today && this._hass.states[today];
    if (liveToday && !isNaN(parseFloat(liveToday.state))) total = { ...total, value: parseFloat(liveToday.state), days: 1 };
    const daily = parseFloat(this._hass.states[goalId]?.state) || 0;
    const goal = daily * total.days;
    let text;
    let percent = null;
    if (this.error) text = this.error;
    else if (!this.data && !liveToday) text = "Loading…";
    else if (total.value === null) text = p.isDay ? "Nothing logged" : "No finished days logged";
    else {
      percent = goal > 0 ? Math.round((total.value / goal) * 100) : null;
      text = goal > 0 ? `${formatAmount(total.value)} / ${formatAmount(goal)} ${unit} · ${percent} %` : `${formatAmount(total.value)} ${unit} · no goal`;
      if (!p.isDay) text += ` · ⌀ ${formatAmount(total.value / total.days)} · ${total.days} d`;
      if (total.incomplete) text += " · incomplete";
    }
    const color = cssColor(percent === null ? null : stepColor(steps, percent));
    const title = p.isDay ? "" : `Goal: ${formatAmount(daily)} ${unit} a day × ${total.days} logged days. Today and incomplete days are left out`;
    root.innerHTML = `<style>
      :host { display: block; height: 100%; }
      ha-card { height: 100%; box-sizing: border-box; padding: 8px 12px; display: flex; align-items: center; gap: 10px; }
      .icon { flex: none; width: 36px; height: 36px; border-radius: 50%; display: flex; align-items: center; justify-content: center;
              --mdc-icon-size: 20px; color: ${color}; background: color-mix(in srgb, ${color} 20%, transparent); }
      .info { flex: 1; min-width: 0; }
      .primary { font-size: 14px; font-weight: 500; line-height: 18px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .secondary { font-size: 12px; line-height: 16px; color: var(--secondary-text-color); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .bar { height: 6px; margin-top: 4px; border-radius: 3px; background: var(--divider-color); overflow: hidden; }
      .fill { height: 100%; border-radius: 3px; background: ${color}; transition: width 0.4s; }
    </style><ha-card title="${title}">
      <div class="icon"><ha-icon icon="${icon || "mdi:food-apple"}"></ha-icon></div>
      <div class="info"><div class="primary">${label} · ${where}</div><div class="secondary">${text}</div>
        <div class="bar"><div class="fill" style="width:${Math.min(percent ?? 0, 100)}%"></div></div></div>
    </ha-card>`;
  }
}

const RANGES = [
  ["Day", "Day"],
  ["Week", "Week"],
  ["Month", "Month"],
  ["3 months", "3M"],
  ["Year", "Year"],
];

// The range buttons, ◀ period ▶ and Today, for all the cards. A copy is pinned under the top bar once the card
// scrolls away
class FrogWellnessNav extends HTMLElement {
  setConfig(config) {
    this.config = config;
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
  }

  set hass(hass) {
    this._hass = hass;
    if (this.config.range) setRange(hass.states[this.config.range]?.state);
    this.render();
  }

  connectedCallback() {
    this.onView = () => this.render();
    window.addEventListener(VIEW_EVENT, this.onView);
    this.onPlace = () => this.place();
    window.addEventListener("resize", this.onPlace);
    // Pinned once the card has gone up under HA's top bar
    this.observer = new IntersectionObserver(
      ([entry]) => {
        this.pinned = !entry.isIntersecting && entry.boundingClientRect.top < 0;
        this.place();
      },
      { rootMargin: `-${this.headerHeight()}px 0px 0px 0px` }
    );
    this.observer.observe(this);
  }

  disconnectedCallback() {
    window.removeEventListener(VIEW_EVENT, this.onView);
    window.removeEventListener("resize", this.onPlace);
    this.observer?.disconnect();
  }

  headerHeight() {
    return parseFloat(getComputedStyle(this).getPropertyValue("--header-height")) || 56;
  }

  place() {
    const bar = this.shadowRoot?.querySelector(".pinned");
    if (!bar) return;
    if (!this.pinned) return void (bar.style.display = "none");
    const box = this.getBoundingClientRect();
    Object.assign(bar.style, { display: "flex", left: `${box.left}px`, width: `${box.width}px`, top: `${this.headerHeight()}px` });
  }

  // Some browsers, on touch above all, jump to the top while the cards below redraw. Back to where it was
  keepScroll() {
    const y = window.scrollY;
    const back = () => window.scrollY < y - 40 && window.scrollTo(0, y);
    for (const ms of [0, 100, 300, 700, 1200]) setTimeout(back, ms);
  }

  pick(range) {
    if (!this._hass || !this.config.range) return;
    this.keepScroll();
    setRange(range);
    this._hass.callService("input_select", "select_option", { entity_id: this.config.range, option: range });
  }

  getCardSize() {
    return 1;
  }

  getGridOptions() {
    return { columns: "full", rows: "auto" };
  }

  move(offset) {
    this.keepScroll();
    view.offset = Math.max(0, offset);
    notify(VIEW_EVENT);
  }

  build() {
    const root = this.shadowRoot;
    const ranges = (short) => RANGES.map(([range, name]) => `<button class="range" data-range="${range}">${short ? name : range}</button>`).join("");
    const moves = `<button data-move="back" title="Back">◀</button><div class="label"></div>
      <button data-move="forward" title="Forward">▶</button><button data-move="today" class="today">Today</button>`;
    root.innerHTML = `<style>
      ha-card { padding: 6px 8px; height: 100%; box-sizing: border-box; display: flex; flex-direction: column; gap: 4px; }
      .row { display: flex; align-items: center; gap: 4px; }
      .ranges { justify-content: space-between; }
      button { background: none; border: none; color: var(--primary-text-color); font-size: 18px; cursor: pointer;
        padding: 4px 12px; border-radius: 8px; font-family: inherit; }
      button:hover:not(:disabled) { background: var(--secondary-background-color); }
      button:disabled { opacity: 0.3; cursor: default; }
      button.range { flex: 1; font-size: 14px; color: var(--secondary-text-color); padding: 6px 4px; }
      button.range.on { color: var(--primary-color); background: color-mix(in srgb, var(--primary-color) 15%, transparent); font-weight: 500; }
      .label { flex: 1; text-align: center; font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .today { font-size: 14px; color: var(--primary-color); }
      .pinned { position: fixed; z-index: 5; display: none; align-items: center; gap: 2px; box-sizing: border-box; padding: 4px 8px;
        background: var(--primary-background-color); border-bottom: 1px solid var(--divider-color); box-shadow: 0 2px 6px rgba(0, 0, 0, 0.25);
        border-radius: 0 0 12px 12px; }
      .pinned button { font-size: 14px; padding: 4px 8px; }
      .pinned .range { flex: none; }
      .pinned .label { font-size: 14px; }
    </style><ha-card>
      <div class="row ranges">${ranges(false)}</div>
      <div class="row">${moves}</div>
    </ha-card>
    <div class="pinned">${ranges(true)}${moves}</div>`;
    // A tap doesn't move the focus, so the browser has no reason to scroll to the button
    root.addEventListener("mousedown", (event) => event.target.closest("button") && event.preventDefault());
    root.addEventListener("click", (event) => {
      const button = event.target.closest("button");
      if (!button || button.disabled) return;
      if (button.dataset.range) return this.pick(button.dataset.range);
      const move = button.dataset.move;
      this.move(move === "back" ? view.offset + 1 : move === "forward" ? view.offset - 1 : 0);
    });
  }

  render() {
    const root = this.shadowRoot;
    if (!root || !view.range) return;
    const label = periodLabel(period(view.range, view.offset));
    if (!root.innerHTML) this.build();
    // Only the text and the states change, so the button just tapped stays in place
    root.querySelectorAll(".label").forEach((node) => (node.textContent = label));
    root.querySelectorAll("[data-range]").forEach((button) => button.classList.toggle("on", button.dataset.range === view.range));
    root.querySelectorAll('[data-move="forward"], [data-move="today"]').forEach((button) => (button.disabled = view.offset === 0));
    this.place();
  }
}

// HA puts its own registry in place of window.customElements while it starts, and it doesn't see an element
// defined in the browser's registry before that: the card stays "Custom element doesn't exist" until a reload.
// So define it once HA has started (5 s at most)
const defineWhenReady = (name, element) =>
  Promise.race([customElements.whenDefined("home-assistant"), new Promise((resolve) => setTimeout(resolve, 5000))]).then(
    () => window.customElements.get(name) || window.customElements.define(name, element)
  );

defineWhenReady("frog-daily-chart", FrogDailyChart);
defineWhenReady("frog-activity-timeline", FrogActivityTimeline);
defineWhenReady("frog-wellness-nav", FrogWellnessNav);
defineWhenReady("frog-nutrition-total", FrogNutritionTotal);
