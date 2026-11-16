// A line chart with one point per day. A series comes from a Google Sheet published as CSV (read by the browser)
// or from the HA long-term statistics of an entity. Loaded by frontend.extra_module_url in configuration.yaml.
//   type: custom:frog-daily-chart
//   title: Calories
//   range: input_select.wellness_range  (Month / 3 months / Year)
//   sheet:                              (only for column series)
//     source: sensor.calories_sheet     (its url attribute is the CSV link, from secrets.yaml)
//     date_column: 0
//     keep_column: 5                    (optional, only rows where it is TRUE are drawn)
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
const DAY_MS = 24 * 60 * 60 * 1000;
const RANGE_DAYS = { Month: 30, "3 months": 91, Year: 365 };
const CACHE_MS = 10 * 60 * 1000;
const HEIGHT = 200;
const PAD = { left: 44, right: 12, top: 12, bottom: 24 };
const RIGHT_AXIS_WIDTH = 36;

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

const formatValue = (value) => (Math.abs(value) >= 100 ? Math.round(value) : Math.round(value * 10) / 10);

class FrogDailyChart extends HTMLElement {
  setConfig(config) {
    if (!Array.isArray(config.series)) throw new Error("series is required");
    if (config.series.some((line) => line.column !== undefined) && !config.sheet?.source)
      throw new Error("sheet.source is required for column series");
    this.config = config;
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this.rows = null;
    this.stats = null;
    this.error = null;
    this.loaded = 0;
  }

  set hass(hass) {
    this._hass = hass;
    const range = this.config.range ? hass.states[this.config.range]?.state : null;
    if (Date.now() - this.loaded > CACHE_MS) this.load();
    if (range !== this.range) {
      this.range = range;
      this.render();
    }
  }

  connectedCallback() {
    this.resize = new ResizeObserver(() => this.render());
    this.resize.observe(this);
  }

  disconnectedCallback() {
    this.resize?.disconnect();
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
    const ids = entities.flatMap((line) => [line.entity].flat());
    const rows = url ? fetchSheet(url) : Promise.resolve([]);
    const stats = entities.length
      ? this._hass.callWS({
          type: "recorder/statistics_during_period",
          start_time: new Date(startOfDay(Date.now()) - RANGE_DAYS.Year * DAY_MS).toISOString(),
          statistic_ids: [...new Set(ids)],
          period: "day",
          types: [...new Set(entities.map((line) => line.stat || "mean"))],
        })
      : Promise.resolve({});
    Promise.all([rows, stats]).then(
      ([rows, stats]) => ((this.rows = rows), (this.stats = stats), (this.error = null), this.render()),
      (error) => ((this.error = error.message || String(error)), this.render())
    );
  }

  points() {
    const { sheet = {}, series } = this.config;
    const days = RANGE_DAYS[this.range] || RANGE_DAYS.Month;
    const end = startOfDay(Date.now());
    const start = end - (days - 1) * DAY_MS;
    const lines = series.map((line) => {
      const points = [];
      const add = (time, value) => {
        if (value === null || value === undefined || time < start || time > end) return;
        if (line.skip_today && time === end) return;
        points.push({ time, value: value * (line.scale ?? 1) });
      };
      if (line.column !== undefined) {
        for (const row of this.rows) {
          const date = parseDate(row[sheet.date_column ?? 0]);
          if (!date) continue;
          if (sheet.keep_column !== undefined && (row[sheet.keep_column] || "").trim().toUpperCase() !== "TRUE") continue;
          add(date.getTime(), parseNumber(row[line.column]));
        }
      } else {
        const highest = new Map();
        for (const entity of [line.entity].flat()) {
          for (const stat of this.stats[entity] || []) {
            const time = startOfDay(typeof stat.start === "number" ? stat.start : Date.parse(stat.start));
            const value = stat[line.stat || "mean"];
            if (value !== null && value !== undefined) highest.set(time, Math.max(highest.get(time) ?? -Infinity, value));
          }
        }
        highest.forEach((value, time) => add(time, value));
      }
      return points.sort((a, b) => a.time - b.time);
    });
    return { start, end, lines };
  }

  render() {
    const root = this.shadowRoot;
    if (!root || !this.config) return;
    const width = Math.max(this.clientWidth - 32, 200);
    let body;
    if (this.error) body = `<div class="note">${this.error}</div>`;
    else if (!this.rows || !this.stats) body = `<div class="note">Loading…</div>`;
    else {
      const { start, end, lines } = this.points();
      body = lines.some((line) => line.length)
        ? this.chart(width, start, end, lines)
        : `<div class="note">No data in this range yet</div>`;
    }
    const { title, series } = this.config;
    const legend =
      series.length > 1
        ? `<div class="legend">${series
            .map((line) => `<span><i style="background:${line.color}"></i>${line.name}${line.unit ? `, ${line.unit}` : ""}</span>`)
            .join("")}</div>`
        : "";
    root.innerHTML = `<style>
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

  chart(width, start, end, lines) {
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
    for (let i = 0; i <= ticks; i++) {
      const time = start + Math.round(((end - start) / DAY_MS) * (i / ticks)) * DAY_MS;
      svg += `<text x="${x(time)}" y="${HEIGHT - 6}" text-anchor="middle">${formatDay(time)}</text>`;
    }

    lines.forEach((line, index) => {
      const { color, name, unit = "", axis = "left" } = this.config.series[index];
      // A missing day breaks the line
      let path = "";
      line.forEach((point, i) => {
        const joined = i > 0 && point.time - line[i - 1].time <= DAY_MS * 1.5;
        path += `${joined ? "L" : "M"}${x(point.time)},${y(point.value, axis)}`;
      });
      svg += `<path d="${path}" fill="none" stroke="${color}" stroke-width="2" stroke-linejoin="round"/>`;
      for (const point of line) {
        svg += `<circle cx="${x(point.time)}" cy="${y(point.value, axis)}" r="3" fill="${color}">
          <title>${formatDay(point.time)} · ${name} ${formatValue(point.value)} ${unit}</title></circle>`;
      }
    });
    return `<svg width="${width}" height="${HEIGHT}" viewBox="0 0 ${width} ${HEIGHT}">${svg}</svg>`;
  }
}

if (!customElements.get("frog-daily-chart")) customElements.define("frog-daily-chart", FrogDailyChart);
