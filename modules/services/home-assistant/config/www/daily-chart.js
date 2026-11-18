// The Wellness charts. Loaded by frontend.extra_module_url in configuration.yaml. All cards share one view: the range
// from input_select.wellness_range and a back/forward offset kept in this browser. They also share the hover time,
// so hovering one chart draws the same moment on all of them.
//
// Periods are calendar ones: Day is a day from midnight, Week is Monday to Sunday, Month a calendar month,
// 3 months three calendar months, Year a calendar year. Back goes one period further.
//
//   type: custom:frog-wellness-nav        ◀ period ▶ and Today
//   range: input_select.wellness_range
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
//   activity:                             (needed for a sleep series)
//     watch: sensor.watch_activity        (its asleep and exercise states)
//     sleep: sensor.pixel_7_sleep_segment (Health Connect segments, start and end attributes in ms)
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
//
//   type: custom:frog-activity-timeline   sleep and exercise on one line, from the activity config above
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

// Shared by all cards, so a tab with several charts reads a CSV once
const csvCache = new Map();

const fetchCsv = (url, maxAge = CACHE_MS) => {
  const hit = csvCache.get(url);
  if (hit && Date.now() - hit.time < maxAge) return hit.promise;
  const promise = fetch(url, { cache: "no-store" }).then((response) => {
    if (!response.ok) throw new Error(`${url} answered ${response.status}`);
    return response.text().then(parseCsv);
  });
  promise.catch(() => csvCache.delete(url));
  csvCache.set(url, { promise, time: Date.now() });
  return promise;
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
  return hass
    .callWS({
      type: "history/history_during_period",
      start_time: new Date(from).toISOString(),
      end_time: new Date(Math.min(Date.now(), addDays(p.end, 2))).toISOString(),
      entity_ids: [activity.watch, activity.sleep],
      minimal_response: false,
      no_attributes: false,
      significant_changes_only: false,
    })
    .then((history) => activityIntervals(history, activity));
};

// Sleep and exercise as time intervals. The watch is more exact, so a calendar day takes its sleep from the watch
// when the watch reported any sleep on it, otherwise from the Health Connect segments. Exercise is only on the watch
const activityIntervals = (history, activity) => {
  const now = Date.now();
  const watch = { sleep: [], exercise: [] };
  const states = history[activity.watch] || [];
  states.forEach((entry, i) => {
    const kind = { asleep: "sleep", exercise: "exercise" }[entry.s];
    const from = historyTime(entry);
    const to = i + 1 < states.length ? historyTime(states[i + 1]) : now;
    if (kind && to > from) watch[kind].push({ from, to, source: "watch" });
  });
  const seen = new Set();
  const health = [];
  for (const entry of history[activity.sleep] || []) {
    const { start, end } = entry.a || {};
    if (!(start < end) || seen.has(`${start}-${end}`)) continue;
    seen.add(`${start}-${end}`);
    health.push({ from: start, to: Math.min(end, now), source: "Health Connect" });
  }
  const sleep = [];
  const all = [...watch.sleep, ...health];
  if (all.length) {
    for (let day = startOfDay(Math.min(...all.map((interval) => interval.from))); day <= now; day = addDays(day, 1)) {
      const fromWatch = clip(watch.sleep, day, addDays(day, 1));
      sleep.push(...(fromWatch.length ? fromWatch : clip(health, day, addDays(day, 1))));
    }
  }
  return { sleep, exercise: watch.exercise };
};

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
    return [sheet.updated, ...sleep, ...series.flatMap((line) => [line.day?.history, line.day?.time_in])].filter(Boolean);
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
    return Promise.all([rows, stats, this.loadSleep(p)]).then(([rows, stats, activity]) => ({ rows, stats, activity }));
  }

  // 5-minute statistics (hourly after 10 days), raw states, the meals and the sleep intervals of one day
  fetchDay(p) {
    const { sheet, series } = this.config;
    const statLines = series.filter((line) => line.entity && !line.day && !line.sleep);
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
    return Promise.all([stats, history, meals, this.loadSleep(p)]).then(([stats, history, meals, activity]) => ({
      stats,
      history,
      meals,
      activity,
      step: fine ? FIVE_MINUTES_MS : HOUR_MS,
    }));
  }

  dayLines(p) {
    const until = Math.min(Date.now(), p.end);
    return this.config.series.map((line) => {
      if (line.day === false) return [];
      if (line.column !== undefined) return this.mealLine(line, p, until);
      if (line.sleep) return this.sleepLine(p.start, until);
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
    for (const interval of clip(this.data.activity.sleep, start, until).sort((a, b) => a.from - b.from)) {
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
      } else if (line.sleep) {
        // Raw history covers the last 31 days, older days use the entity's daily stat
        const rawStart = today - RAW_DAYS * DAY_MS;
        const hours = new Map();
        for (const interval of this.data.activity.sleep) {
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

// Sleep and exercise as blocks on one line
class FrogActivityTimeline extends WellnessCard {
  setConfig(config) {
    if (!config.activity?.watch || !config.activity?.sleep) throw new Error("activity.watch and activity.sleep are required");
    super.setConfig(config);
  }

  getCardSize() {
    return 2;
  }

  watched() {
    return [this.config.activity.watch, this.config.activity.sleep];
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
      for (const [intervals, color] of [[this.data.sleep, SLEEP_COLOR], [this.data.exercise, EXERCISE_COLOR]])
        for (const interval of clip(intervals, p.start, until)) {
          const left = x(interval.from);
          svg += `<rect x="${left}" y="0" width="${Math.max(x(interval.to) - left, 1)}" height="${height}" fill="${color}"/>`;
        }
      for (const tick of ticks(p, plotWidth))
        svg += `<text x="${x(tick.time)}" y="${height + 16}" text-anchor="${tick.left ? "start" : "middle"}">${tick.label}</text>`;
      svg += `<line class="cursor" style="display:none"/>`;
      body = `<div class="plot"><svg class="chart" width="${width}" height="${height + 20}" viewBox="0 0 ${width} ${height + 20}">${svg}</svg><div class="tip"></div></div>`;
    }
    const hint = p.start < rawStart && p.end > rawStart ? `<div class="hint">Only the last ${RAW_DAYS} days: HA keeps raw states that long</div>` : "";
    root.innerHTML = `<style>${STYLE}</style><ha-card>${this.header()}
      <div class="legend"><span><i style="background:${SLEEP_COLOR}"></i>Sleep</span><span><i style="background:${EXERCISE_COLOR}"></i>Exercise</span></div>
      ${body}${hint}</ha-card>`;
    this.wire();
  }

  hoverAt(time) {
    const { x } = this.plot;
    const found = [["Sleep", this.data.sleep, SLEEP_COLOR], ["Exercise", this.data.exercise, EXERCISE_COLOR]]
      .map(([name, intervals, color]) => [name, intervals.find((interval) => interval.from <= time && time < interval.to), color])
      .filter(([, interval]) => interval);
    const rows = found.length
      ? found
          .map(
            ([name, interval, color]) =>
              `<div><i style="background:${color}"></i>${name} ${formatTime(interval.from)}–${formatTime(interval.to)}, ${formatValue(
                (interval.to - interval.from) / HOUR_MS
              )} h</div>`
          )
          .join("")
      : "<div>No sleep or exercise</div>";
    return { x: x(time), html: `<b>${this.period.isDay ? formatTime(time) : `${formatDay(time)} ${formatTime(time)}`}</b>${rows}` };
  }
}

// ◀ period ▶ and Today, for all the cards
class FrogWellnessNav extends HTMLElement {
  setConfig(config) {
    this.config = config;
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
  }

  set hass(hass) {
    if (this.config.range) setRange(hass.states[this.config.range]?.state);
    this.render();
  }

  connectedCallback() {
    this.onView = () => this.render();
    window.addEventListener(VIEW_EVENT, this.onView);
  }

  disconnectedCallback() {
    window.removeEventListener(VIEW_EVENT, this.onView);
  }

  getCardSize() {
    return 1;
  }

  getGridOptions() {
    return { columns: "full", rows: 1 };
  }

  move(offset) {
    view.offset = Math.max(0, offset);
    notify(VIEW_EVENT);
  }

  render() {
    const root = this.shadowRoot;
    if (!root || !view.range) return;
    const label = periodLabel(period(view.range, view.offset));
    if (label === this.label && root.innerHTML) return;
    this.label = label;
    root.innerHTML = `<style>
      ha-card { display: flex; align-items: center; gap: 8px; padding: 6px 8px; height: 100%; box-sizing: border-box; }
      button { background: none; border: none; color: var(--primary-text-color); font-size: 18px; cursor: pointer;
        padding: 4px 12px; border-radius: 8px; }
      button:hover:not(:disabled) { background: var(--secondary-background-color); }
      button:disabled { opacity: 0.3; cursor: default; }
      .label { flex: 1; text-align: center; font-weight: 500; }
      .today { font-size: 14px; color: var(--primary-color); }
    </style><ha-card>
      <button class="back" title="Back">◀</button>
      <div class="label">${label}</div>
      <button class="forward" title="Forward" ${view.offset === 0 ? "disabled" : ""}>▶</button>
      <button class="today" ${view.offset === 0 ? "disabled" : ""}>Today</button>
    </ha-card>`;
    root.querySelector(".back").addEventListener("click", () => this.move(view.offset + 1));
    root.querySelector(".forward").addEventListener("click", () => this.move(view.offset - 1));
    root.querySelector(".today").addEventListener("click", () => this.move(0));
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
