// A line chart of daily rows from a Google Sheet published as CSV. The browser reads the sheet, HA keeps nothing.
// Loaded by frontend.extra_module_url in configuration.yaml.
//   type: custom:frog-sheet-chart
//   source: sensor.calories_sheet       (its url attribute is the CSV link, from secrets.yaml)
//   range: input_select.wellness_range  (Month / 3 months / Year)
//   date_column: 0
//   keep_column: 5                      (optional, only rows where it is TRUE are drawn)
//   series:
//     - column: 1
//       name: Calories
//       unit: kcal
//       color: "#f59e0b"
const DAY_MS = 24 * 60 * 60 * 1000;
const RANGE_DAYS = { Month: 30, "3 months": 91, Year: 365 };
const CACHE_MS = 10 * 60 * 1000;
const HEIGHT = 200;
const PAD = { left: 44, right: 12, top: 12, bottom: 24 };

// Shared by all cards, so a tab with several charts reads the sheet once
const cache = new Map();

const fetchRows = (url) => {
  const hit = cache.get(url);
  if (hit && Date.now() - hit.time < CACHE_MS) return hit.promise;
  const promise = fetch(url, { cache: "no-store" }).then((response) => {
    if (!response.ok) throw new Error(`Sheet answered ${response.status}`);
    return response.text().then(parseCsv);
  });
  promise.catch(() => cache.delete(url));
  cache.set(url, { promise, time: Date.now() });
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

const formatDay = (date) =>
  `${String(date.getDate()).padStart(2, "0")}.${String(date.getMonth() + 1).padStart(2, "0")}`;

class FrogSheetChart extends HTMLElement {
  setConfig(config) {
    if (!config.source || !Array.isArray(config.series)) throw new Error("source and series are required");
    this.config = { date_column: 0, ...config };
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this.rows = null;
    this.error = null;
  }

  set hass(hass) {
    this._hass = hass;
    const url = hass.states[this.config.source]?.attributes.url;
    const range = this.config.range ? hass.states[this.config.range]?.state : null;
    if (url && (url !== this.url || !this.rows || Date.now() - this.loaded > CACHE_MS)) this.load(url);
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

  load(url) {
    this.url = url;
    this.loaded = Date.now();
    fetchRows(url).then(
      (rows) => ((this.rows = rows), (this.error = null), this.render()),
      (error) => ((this.error = error.message), this.render())
    );
  }

  points() {
    const { date_column, keep_column, series } = this.config;
    const days = RANGE_DAYS[this.range] || RANGE_DAYS.Month;
    const today = new Date();
    const end = new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime();
    const start = end - (days - 1) * DAY_MS;
    const lines = series.map(() => []);
    for (const row of this.rows) {
      const date = parseDate(row[date_column]);
      if (!date || date.getTime() < start || date.getTime() > end) continue;
      if (keep_column !== undefined && (row[keep_column] || "").trim().toUpperCase() !== "TRUE") continue;
      series.forEach((line, index) => {
        const value = parseNumber(row[line.column]);
        if (value !== null) lines[index].push({ time: date.getTime(), value });
      });
    }
    lines.forEach((line) => line.sort((a, b) => a.time - b.time));
    return { start, end, lines };
  }

  render() {
    const root = this.shadowRoot;
    if (!root || !this.config) return;
    const width = Math.max(this.clientWidth - 32, 200);
    let body;
    if (this.error) body = `<div class="note">${this.error}</div>`;
    else if (!this.rows) body = `<div class="note">Loading…</div>`;
    else {
      const { start, end, lines } = this.points();
      body = lines.some((line) => line.length)
        ? this.chart(width, start, end, lines)
        : `<div class="note">No full days in this range yet</div>`;
    }
    const { title, series } = this.config;
    const legend =
      series.length > 1
        ? `<div class="legend">${series
            .map((line) => `<span><i style="background:${line.color}"></i>${line.name}</span>`)
            .join("")}</div>`
        : "";
    root.innerHTML = `<style>
      ha-card { padding: 12px 16px; }
      .title { font-size: 16px; font-weight: 500; margin-bottom: 8px; }
      .legend { display: flex; gap: 12px; font-size: 12px; color: var(--secondary-text-color); margin-bottom: 4px; }
      .legend i { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 4px; }
      .note { height: ${HEIGHT}px; display: flex; align-items: center; justify-content: center; color: var(--secondary-text-color); }
      svg { display: block; overflow: visible; }
      text { fill: var(--secondary-text-color); font-size: 11px; }
      .grid { stroke: var(--divider-color); }
    </style><ha-card>${title ? `<div class="title">${title}</div>` : ""}${legend}${body}</ha-card>`;
  }

  chart(width, start, end, lines) {
    const values = lines.flat().map((point) => point.value);
    let low = Math.min(...values);
    let high = Math.max(...values);
    const margin = (high - low) * 0.1 || Math.abs(high) * 0.1 || 1;
    low = Math.max(0, low - margin);
    high += margin;
    const plotWidth = width - PAD.left - PAD.right;
    const plotHeight = HEIGHT - PAD.top - PAD.bottom;
    const x = (time) => PAD.left + ((time - start) / (end - start || DAY_MS)) * plotWidth;
    const y = (value) => PAD.top + (1 - (value - low) / (high - low)) * plotHeight;

    let svg = "";
    for (let i = 0; i <= 4; i++) {
      const value = low + ((high - low) * i) / 4;
      svg += `<line class="grid" x1="${PAD.left}" x2="${width - PAD.right}" y1="${y(value)}" y2="${y(value)}"/>`;
      svg += `<text x="${PAD.left - 6}" y="${y(value) + 4}" text-anchor="end">${Math.round(value)}</text>`;
    }
    const ticks = Math.min(6, Math.floor(plotWidth / 70));
    for (let i = 0; i <= ticks; i++) {
      const time = start + Math.round(((end - start) / DAY_MS) * (i / ticks)) * DAY_MS;
      svg += `<text x="${x(time)}" y="${HEIGHT - 6}" text-anchor="middle">${formatDay(new Date(time))}</text>`;
    }

    lines.forEach((line, index) => {
      const { color, name, unit = "" } = this.config.series[index];
      // A day without a full record breaks the line
      let path = "";
      line.forEach((point, i) => {
        const joined = i > 0 && point.time - line[i - 1].time <= DAY_MS * 1.5;
        path += `${joined ? "L" : "M"}${x(point.time)},${y(point.value)}`;
      });
      svg += `<path d="${path}" fill="none" stroke="${color}" stroke-width="2" stroke-linejoin="round"/>`;
      for (const point of line) {
        svg += `<circle cx="${x(point.time)}" cy="${y(point.value)}" r="3" fill="${color}">
          <title>${formatDay(new Date(point.time))} · ${name} ${point.value} ${unit}</title></circle>`;
      }
    });
    return `<svg width="${width}" height="${HEIGHT}" viewBox="0 0 ${width} ${HEIGHT}">${svg}</svg>`;
  }
}

if (!customElements.get("frog-sheet-chart")) customElements.define("frog-sheet-chart", FrogSheetChart);
