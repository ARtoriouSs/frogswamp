// A line chart with one point per day, or a live line for Day. A series comes from a CSV (read by the browser)
// or from the HA long-term statistics of an entity. Loaded by frontend.extra_module_url in configuration.yaml.
//   type: custom:frog-daily-chart
//   title: Calories
//   range: input_select.wellness_range  (Day / Week / Month / 3 months / Year)
//                                       Day is today from midnight. By default it uses 5-minute statistics (HA keeps them
//                                       for 10 days) and change series add up through the day. A series' day option changes that
//   day_note: No live data              (optional, Day shows only this text)
//   sheet:                              (only for column series)
//     source: sensor.calories_sheet     (its url attribute is the daily CSV, meals_url the CSV of today's meals with times:
//                                       Day adds them up at the time of each meal)
//     date_column: 0
//     keep_column: 5                    (optional, only rows where it is TRUE are drawn)
//     updated: sensor.nutrition_last_read  (optional, Day reads the meals again when it changes)
//   series:
//     - column: 1                       (a sheet column)
//       name: Eaten
//       unit: kcal
//       color: "#f59e0b"
//     - entity: sensor.pixel_7_total_calories_burned
//       stat: change                    (change for daily totals, or mean / min / max)
//       name: Burned
//       unit: kcal
//       color: "#ef4444"
//       axis: right                     (optional, a second scale on the right)
//     - entity: [sensor.phone_steps, sensor.watch_steps]
//       stat: change                    (a list of entities draws the highest value of each day)
//       skip_today: true                (optional, today is not over yet)
//     - entity: sensor.health_weight
//       measured:                       (optional, a day is the average of the measurements taken on it: the day's change
//         sum: sensor.weight_sum        of sum / the day's change of count. Days from before these sensors use stat)
//         count: sensor.weight_count
//       join: true                      (optional, the line goes over days without a value)
//       day:                            (optional, how Day draws it)
//         history: sensor.health_weight (the raw states, each held until the next one)
//         time_in: sensor.watch_state   (or hours spent in a state since midnight, rising only while in it)
//         state: asleep
//                                       (day: false leaves the series out of Day)
const DAY_MS = 24 * 60 * 60 * 1000;
const RANGE_DAYS = { Day: 1, Week: 7, Month: 30, "3 months": 91, Year: 365 };
const FIVE_MINUTES_MS = 5 * 60 * 1000;
const CACHE_MS = 10 * 60 * 1000;
const HEIGHT = 200;
const PAD = { left: 44, right: 12, top: 12, bottom: 24 };
const RIGHT_AXIS_WIDTH = 36;
// Day reads again when a watched entity changes, but not more often than this
const DAY_RELOAD_MS = 20 * 1000;
const HOUR_MS = 60 * 60 * 1000;

// Shared by all cards, so a tab with several charts reads the sheet once
const sheetCache = new Map();

const fetchSheet = (url) => {
  const hit = sheetCache.get(url);
  if (hit && Date.now() - hit.time < CACHE_MS) return hit.promise;
  const promise = fetch(url, { cache: "no-store" }).then((response) => {
    if (!response.ok) throw new Error(`Sheet answered ${response.status}`);
    return response.text().then(parseCsv);
  });
  promise.catch(() => sheetCache.delete(url));
  sheetCache.set(url, { promise, time: Date.now() });
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

// The sheet may use a decimal comma and spaces between thousands
const parseNumber = (value) => {
  const text = (value || "").replace(/[\s ]/g, "").replace(",", ".");
  if (!text) return null;
  const number = Number(text);
  return Number.isFinite(number) ? number : null;
};

const startOfDay = (time) => {
  const date = new Date(time);
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
};

const formatDay = (time) => {
  const date = new Date(time);
  return `${String(date.getDate()).padStart(2, "0")}.${String(date.getMonth() + 1).padStart(2, "0")}`;
};

const formatHour = (time) => `${String(new Date(time).getHours()).padStart(2, "0")}:00`;
const formatTime = (time) => new Date(time).toTimeString().slice(0, 5);

const formatValue = (value) => (Math.abs(value) >= 100 ? Math.round(value) : Math.round(value * 10) / 10);

const statTime = (stat) => (typeof stat.start === "number" ? stat.start : Date.parse(stat.start));

// One stat type of an entity per day
const byDay = (stats, type) => {
  const days = new Map();
  for (const stat of stats || []) {
    const value = stat[type];
    if (value !== null && value !== undefined) days.set(startOfDay(statTime(stat)), value);
  }
  return days;
};

// A line that holds each value until the next one. initial is the value at start, or null when there is none
const steps = (initial, events, start, now) => {
  const points = initial === null ? [] : [{ time: start, value: initial }];
  for (const { time, value } of events) {
    const at = Math.min(Math.max(time, start), now);
    if (points.length) points.push({ time: at, value: points[points.length - 1].value });
    points.push({ time: at, value, mark: true });
  }
  if (points.length) points.push({ time: now, value: points[points.length - 1].value });
  return Object.assign(points, { joined: true });
};

// History entries: {s: state, lu: seconds}
const historyTime = (entry) => (entry.lu ?? entry.lc) * 1000;

class FrogDailyChart extends HTMLElement {
  setConfig(config) {
    if (!Array.isArray(config.series)) throw new Error("series is required");
    if (config.series.some((line) => line.column !== undefined) && !config.sheet?.source)
      throw new Error("sheet.source is required for column series");
    this.config = config;
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this.rows = null;
    this.stats = null;
    this.day = null;
    this.error = null;
    this.loaded = 0;
    this.dayLoaded = 0;
    this.dayKey = null;
  }

  get isDay() {
    return this.range === "Day";
  }

  set hass(hass) {
    this._hass = hass;
    const range = this.config.range ? hass.states[this.config.range]?.state : null;
    if (Date.now() - this.loaded > CACHE_MS) this.load();
    if (range !== this.range) {
      this.range = range;
      this.render();
    }
    if (this.isDay && !this.config.day_note) {
      const key = this.watched().map((id) => hass.states[id]?.last_updated).join();
      const age = Date.now() - this.dayLoaded;
      if (age > FIVE_MINUTES_MS || (key !== this.dayKey && age > DAY_RELOAD_MS)) {
        this.dayKey = key;
        this.loadDay();
      }
    }
  }

  connectedCallback() {
    this.resize = new ResizeObserver(() => this.render());
    this.resize.observe(this);
    // Day lines end at now
    this.timer = setInterval(() => this.isDay && this.render(), 60 * 1000);
  }

  disconnectedCallback() {
    this.resize?.disconnect();
    clearInterval(this.timer);
  }

  watched() {
    const { sheet = {}, series } = this.config;
    return [sheet.updated, ...series.flatMap((line) => [line.day?.history, line.day?.time_in])].filter(Boolean);
  }

  getCardSize() {
    return 5;
  }

  getGridOptions() {
    return { columns: "full", rows: "auto" };
  }

  // Always a year back, so switching the range needs no new request
  load() {
    this.loaded = Date.now();
    const { sheet, series } = this.config;
    const url = sheet ? this._hass.states[sheet.source]?.attributes.url : null;
    const entities = series.filter((line) => line.entity);
    const ids = entities.flatMap((line) => [line.entity, line.measured?.sum, line.measured?.count].flat().filter(Boolean));
    const types = entities.flatMap((line) => [line.stat || "mean", ...(line.measured ? ["change"] : [])]);
    const rows = url ? fetchSheet(url) : Promise.resolve([]);
    const stats = entities.length
      ? this._hass.callWS({
          type: "recorder/statistics_during_period",
          start_time: new Date(startOfDay(Date.now()) - RANGE_DAYS.Year * DAY_MS).toISOString(),
          statistic_ids: [...new Set(ids)],
          period: "day",
          types: [...new Set(types)],
        })
      : Promise.resolve({});
    Promise.all([rows, stats]).then(
      ([rows, stats]) => ((this.rows = rows), (this.stats = stats), (this.error = null), this.render()),
      (error) => ((this.error = error.message || String(error)), this.render())
    );
  }

  // Today: 5-minute statistics, raw states and the meals CSV
  loadDay() {
    this.dayLoaded = Date.now();
    const { sheet, series } = this.config;
    const start = new Date(startOfDay(Date.now())).toISOString();
    const statLines = series.filter((line) => line.entity && !line.day);
    const historyIds = [...new Set(series.flatMap((line) => [line.day?.history, line.day?.time_in]).filter(Boolean))];
    const mealsUrl =
      sheet && series.some((line) => line.column !== undefined && line.day !== false)
        ? this._hass.states[sheet.source]?.attributes.meals_url
        : null;
    const stats = statLines.length
      ? this._hass.callWS({
          type: "recorder/statistics_during_period",
          start_time: start,
          statistic_ids: [...new Set(statLines.flatMap((line) => [line.entity].flat()))],
          period: "5minute",
          types: [...new Set(statLines.map((line) => line.stat || "mean"))],
        })
      : Promise.resolve({});
    const history = historyIds.length
      ? this._hass.callWS({
          type: "history/history_during_period",
          start_time: start,
          entity_ids: historyIds,
          minimal_response: true,
          no_attributes: true,
          significant_changes_only: false,
        })
      : Promise.resolve({});
    const meals = mealsUrl
      ? fetch(mealsUrl, { cache: "no-store" }).then((response) => {
          if (!response.ok) throw new Error(`Meals answered ${response.status}`);
          return response.text().then(parseCsv);
        })
      : Promise.resolve(null);
    Promise.all([stats, history, meals]).then(
      ([stats, history, meals]) => ((this.day = { stats, history, meals }), (this.error = null), this.render()),
      (error) => ((this.error = error.message || String(error)), this.render())
    );
  }

  dayPoints() {
    const start = startOfDay(Date.now());
    const now = Date.now();
    const lines = this.config.series.map((line, index) => {
      if (line.day === false) return [];
      if (line.column !== undefined) return this.mealLine(line, index, start, now);
      if (line.day?.history) return this.historyLine(line, start, now);
      if (line.day?.time_in) return this.timeInLine(line, start, now);
      return this.fiveMinuteLine(line);
    });
    return { start, end: start + DAY_MS, lines, step: FIVE_MINUTES_MS };
  }

  // The running total of today's meals, a step at each meal. Without the meals CSV, the day's total as a flat line
  mealLine(line, index, start, now) {
    const scale = line.scale ?? 1;
    if (!this.day.meals) {
      const value = this.points(1).lines[index].find((point) => point.time === start)?.value;
      return steps(value ?? null, [], start, now);
    }
    let total = 0;
    const events = this.day.meals
      .map((row) => ({ time: Date.parse(row[0]), value: parseNumber(row[line.column]) }))
      .filter((meal) => Number.isFinite(meal.time) && meal.value !== null)
      .sort((a, b) => a.time - b.time)
      .map((meal) => ({ time: meal.time, value: (total += meal.value) * scale }));
    return steps(0, events, start, now);
  }

  historyLine(line, start, now) {
    const scale = line.scale ?? 1;
    const values = (this.day.history[line.day.history] || [])
      .map((entry) => ({ time: historyTime(entry), value: parseFloat(entry.s) * scale }))
      .filter((entry) => Number.isFinite(entry.value));
    const initial = values.length && values[0].time <= start ? values.shift().value : null;
    return steps(initial, values, start, now);
  }

  // Hours in the state since midnight: rises while in it, flat otherwise
  timeInLine(line, start, now) {
    const entries = this.day.history[line.day.time_in] || [];
    const points = [{ time: start, value: 0 }];
    let total = 0;
    entries.forEach((entry, i) => {
      const from = Math.max(historyTime(entry), start);
      const to = i + 1 < entries.length ? Math.min(historyTime(entries[i + 1]), now) : now;
      if (to <= from) return;
      if (entry.s === line.day.state) total += (to - from) / HOUR_MS;
      points.push({ time: to, value: total });
    });
    return Object.assign(points, { joined: true });
  }

  // A change series is a running total from midnight, a list of entities takes the highest total at each time
  fiveMinuteLine(line) {
    const scale = line.scale ?? 1;
    const highest = new Map();
    for (const entity of [line.entity].flat()) {
      let total = 0;
      for (const stat of this.day.stats[entity] || []) {
        const value = stat[line.stat || "mean"];
        if (value === null || value === undefined) continue;
        const time = statTime(stat) + FIVE_MINUTES_MS;
        const point = line.stat === "change" ? (total += value) : value;
        highest.set(time, Math.max(highest.get(time) ?? -Infinity, point * scale));
      }
    }
    return [...highest].map(([time, value]) => ({ time, value })).sort((a, b) => a.time - b.time);
  }

  points(daysOverride) {
    const { sheet = {}, series } = this.config;
    const days = daysOverride || RANGE_DAYS[this.range] || RANGE_DAYS.Month;
    const end = startOfDay(Date.now());
    const start = end - (days - 1) * DAY_MS;
    const lines = series.map((line) => {
      const points = [];
      const add = (time, value) => {
        if (value === null || value === undefined || time < start || time > end) return;
        if (line.skip_today && time === end && !daysOverride) return;
        points.push({ time, value: value * (line.scale ?? 1) });
      };
      if (line.column !== undefined) {
        for (const row of this.rows) {
          const date = parseDate(row[sheet.date_column ?? 0]);
          if (!date) continue;
          if (sheet.keep_column !== undefined && (row[sheet.keep_column] || "").trim().toUpperCase() !== "TRUE") continue;
          add(date.getTime(), parseNumber(row[line.column]));
        }
      } else if (line.measured) {
        // A day with the counter but no new measurement gets no point
        const means = byDay(this.stats[line.entity], line.stat || "mean");
        const sums = byDay(this.stats[line.measured.sum], "change");
        const counts = byDay(this.stats[line.measured.count], "change");
        for (const time of new Set([...means.keys(), ...counts.keys()])) {
          if (!counts.has(time)) add(time, means.get(time));
          else if (counts.get(time) > 0) add(time, (sums.get(time) ?? 0) / counts.get(time));
        }
      } else {
        const highest = new Map();
        for (const entity of [line.entity].flat()) {
          byDay(this.stats[entity], line.stat || "mean").forEach((value, time) =>
            highest.set(time, Math.max(highest.get(time) ?? -Infinity, value))
          );
        }
        highest.forEach((value, time) => add(time, value));
      }
      return Object.assign(points.sort((a, b) => a.time - b.time), { joined: line.join });
    });
    return { start, end, lines, step: DAY_MS };
  }

  render() {
    const root = this.shadowRoot;
    if (!root || !this.config) return;
    const width = Math.max(this.clientWidth - 32, 200);
    let body;
    if (this.isDay && this.config.day_note) body = `<div class="note">${this.config.day_note}</div>`;
    else if (this.error) body = `<div class="note">${this.error}</div>`;
    else if (!this.rows || !this.stats || (this.isDay && !this.day)) body = `<div class="note">Loading…</div>`;
    else {
      const { start, end, lines, step } = this.isDay ? this.dayPoints() : this.points();
      body = lines.some((line) => line.length)
        ? this.chart(width, start, end, lines, step)
        : `<div class="note">No data in this range yet</div>`;
    }
    const { title, series } = this.config;
    const shown = series.filter((line) => !(this.isDay && (line.day === false || this.config.day_note)));
    const legend =
      shown.length > 1
        ? `<div class="legend">${shown
            .map((line) => `<span><i style="background:${line.color}"></i>${line.name}${line.unit ? `, ${line.unit}` : ""}</span>`)
            .join("")}</div>`
        : "";
    // A custom element is inline by default and has no width, the chart takes its width from this host
    root.innerHTML = `<style>
      :host { display: block; }
      ha-card { padding: 12px 16px; }
      .title { font-size: 16px; font-weight: 500; margin-bottom: 8px; }
      .legend { display: flex; flex-wrap: wrap; gap: 4px 12px; font-size: 12px; color: var(--secondary-text-color); margin-bottom: 4px; }
      .legend i { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 4px; }
      .note { height: ${HEIGHT}px; display: flex; align-items: center; justify-content: center; color: var(--secondary-text-color); }
      svg { display: block; overflow: visible; }
      text { fill: var(--secondary-text-color); font-size: 11px; }
      .grid { stroke: var(--divider-color); }
    </style><ha-card>${title ? `<div class="title">${title}</div>` : ""}${legend}${body}</ha-card>`;
  }

  scale(lines, axis) {
    const values = lines
      .filter((_, index) => (this.config.series[index].axis || "left") === axis)
      .flat()
      .map((point) => point.value);
    if (!values.length) return null;
    let low = Math.min(...values);
    let high = Math.max(...values);
    const margin = (high - low) * 0.1 || Math.abs(high) * 0.1 || 1;
    return { low: Math.max(0, low - margin), high: high + margin };
  }

  chart(width, start, end, lines, step) {
    const scales = { left: this.scale(lines, "left"), right: this.scale(lines, "right") };
    const right = scales.right ? RIGHT_AXIS_WIDTH : PAD.right;
    const plotWidth = width - PAD.left - right;
    const plotHeight = HEIGHT - PAD.top - PAD.bottom;
    const x = (time) => PAD.left + ((time - start) / (end - start || DAY_MS)) * plotWidth;
    const y = (value, axis) => {
      const { low, high } = scales[axis] || scales.left || scales.right;
      return PAD.top + (1 - (value - low) / (high - low)) * plotHeight;
    };

    let svg = "";
    for (let i = 0; i <= 4; i++) {
      const top = PAD.top + (plotHeight * (4 - i)) / 4;
      svg += `<line class="grid" x1="${PAD.left}" x2="${width - right}" y1="${top}" y2="${top}"/>`;
      for (const [axis, anchor, at] of [["left", "end", PAD.left - 6], ["right", "start", width - right + 6]]) {
        const scale = scales[axis];
        if (!scale) continue;
        const value = scale.low + ((scale.high - scale.low) * i) / 4;
        svg += `<text x="${at}" y="${top + 4}" text-anchor="${anchor}">${formatValue(value)}</text>`;
      }
    }
    const ticks = Math.min(6, Math.floor(plotWidth / 70));
    const tickStep = this.isDay ? 60 * 60 * 1000 : DAY_MS;
    const format = this.isDay ? formatHour : formatDay;
    for (let i = 0; i <= ticks; i++) {
      const time = start + Math.round(((end - start) / tickStep) * (i / ticks)) * tickStep;
      svg += `<text x="${x(time)}" y="${HEIGHT - 6}" text-anchor="middle">${format(time)}</text>`;
    }

    lines.forEach((line, index) => {
      const { color, name, unit = "", axis = "left" } = this.config.series[index];
      // A missing day (or 5 minutes for Day) breaks the line, unless it is joined. Dots only when there are few points,
      // and on a joined Day line only where a value came in
      let path = "";
      line.forEach((point, i) => {
        const joined = i > 0 && (line.joined || point.time - line[i - 1].time <= step * 1.5);
        path += `${joined ? "L" : "M"}${x(point.time)},${y(point.value, axis)}`;
      });
      svg += `<path d="${path}" fill="none" stroke="${color}" stroke-width="2" stroke-linejoin="round"/>`;
      const dots = this.isDay && line.joined ? line.filter((point) => point.mark) : line;
      for (const point of dots.length > 60 ? [] : dots) {
        svg += `<circle cx="${x(point.time)}" cy="${y(point.value, axis)}" r="3" fill="${color}">
          <title>${(this.isDay ? formatTime : formatDay)(point.time)} · ${name} ${formatValue(point.value)} ${unit}</title></circle>`;
      }
    });
    return `<svg width="${width}" height="${HEIGHT}" viewBox="0 0 ${width} ${HEIGHT}">${svg}</svg>`;
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
