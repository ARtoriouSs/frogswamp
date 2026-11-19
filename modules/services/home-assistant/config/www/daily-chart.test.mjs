// Tests for daily-chart.js: node --test daily-chart.test.mjs. The browser file runs in a vm with small DOM stubs
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync(new URL("./daily-chart.js", import.meta.url), "utf8");

const load = () => {
  class HTMLElement {
    constructor() {
      this.clientWidth = 600;
    }
    attachShadow() {
      const root = { innerHTML: "", querySelector: () => null };
      this.shadowRoot = root;
      return root;
    }
  }
  const context = {
    HTMLElement,
    console,
    Date,
    Math,
    Promise,
    Map,
    Set,
    JSON,
    Number,
    String,
    Array,
    Object,
    Error,
    setTimeout: () => 0,
    setInterval: () => 0,
    clearInterval: () => {},
    CustomEvent: class {},
    ResizeObserver: class {
      observe() {}
      disconnect() {}
    },
    customElements: { whenDefined: () => new Promise(() => {}), get: () => null, define: () => {} },
    fetch: () => Promise.reject(new Error("no fetch in tests")),
  };
  context.window = { ...context, addEventListener() {}, removeEventListener() {}, dispatchEvent() {} };
  vm.createContext(context);
  vm.runInContext(
    `${source}\nglobalThis.t = { activityIntervals, healthValue, heartLine, burnedLine, exerciseLine, period, startOfDay, addDays, FrogDailyChart, FrogActivityTimeline, isoDate };`,
    context
  );
  return context.t;
};

const t = load();
// Values from the vm come from another realm, deepEqual wants plain ones
const plain = (value) => JSON.parse(JSON.stringify(value));
const at = (day, hour, minute = 0) => new Date(2026, 10, day, hour, minute).getTime();
const activity = { watch: "sensor.watch", sleep: "sensor.hc", health: true };
const hc = (from, to) => ({ s: "x", a: { start: from, end: to }, lu: from / 1000 });

test("sleep source order per day", () => {
  const history = {
    "sensor.watch": [
      { s: "asleep", lu: at(15, 1) / 1000 },
      { s: "passive", lu: at(15, 6) / 1000 },
    ],
    "sensor.hc": [hc(at(14, 1), at(14, 7)), hc(at(15, 1), at(15, 8)), hc(at(16, 1), at(16, 9))],
  };
  const google = { "2026-11-16": { sleep: [{ from: at(16, 2), to: at(16, 5), stage: "deep" }], workouts: [] } };
  const { sleep } = t.activityIntervals(history, activity, google);
  const byDay = (day) => sleep.filter((i) => i.from >= at(day, 0) && i.from < at(day + 1, 0));
  assert.equal(byDay(14)[0].source, "Health Connect");
  assert.equal(byDay(15)[0].source, "watch");
  assert.deepEqual(
    plain(byDay(16).map((i) => [i.source, i.stage])),
    [["Google", "deep"]]
  );
});

test("classic night drawn as asleep", () => {
  const google = { "2026-11-16": { sleep: [{ from: at(16, 0), to: at(16, 7), stage: "asleep" }], workouts: [] } };
  const { sleep } = t.activityIntervals({}, activity, google);
  assert.deepEqual(
    plain(sleep.map((i) => i.stage)),
    ["asleep"]
  );
});

test("health series daily value", () => {
  const days = { "2026-11-16": { sleep: { deep_min: 90 }, resting_hr: 62 } };
  assert.equal(t.healthValue(days, "2026-11-16", "sleep.deep_min"), 90);
  assert.equal(t.healthValue(days, "2026-11-16", "resting_hr"), 62);
  assert.equal(t.healthValue(days, "2026-11-16", "azm.total"), null);
  assert.equal(t.healthValue(days, "2026-11-17", "resting_hr"), null);
});

test("heart rate day line", () => {
  const line = t.heartLine({ heart_rate: [[at(16, 10), 70], [at(16, 10, 1), 72]] });
  assert.deepEqual(
    plain(line.map((p) => [p.time, p.value])),
    [[at(16, 10), 70], [at(16, 10, 1), 72]]
  );
});

test("burned day running total", () => {
  const line = t.burnedLine({ burned: [[at(16, 0), 80], [at(16, 1), 90]] }, at(16, 0), at(16, 1, 30));
  assert.deepEqual(
    plain(line.map((p) => [p.time, p.value])),
    [[at(16, 0), 0], [at(16, 1), 80], [at(16, 1, 30), 170]]
  );
});

test("exercise day line from workouts", () => {
  const workout = { start: at(16, 10), end: at(16, 10, 30), type: "WALKING" };
  const line = t.exerciseLine({ workouts: [workout] }, at(16, 0), at(16, 12));
  assert.deepEqual(
    plain(line.map((p) => [p.time, p.value])),
    [[at(16, 0), 0], [at(16, 10), 0], [at(16, 10, 30), 30], [at(16, 12), 30]]
  );
});

test("health_note when days.json missing", () => {
  const card = new t.FrogDailyChart();
  card.setConfig({ series: [{ health: "resting_hr", name: "Resting" }], health_note: "No Google data yet" });
  card.period = t.period("Week", 1);
  card.data = { rows: [], stats: {}, activity: { sleep: [], exercise: [] }, health: null };
  card.render();
  assert.match(card.shadowRoot.innerHTML, /No Google data yet/);
});

test("hover box shows workout details", () => {
  const card = new t.FrogActivityTimeline();
  card.setConfig({ activity });
  card.period = t.period("Day", 0);
  const start = t.startOfDay(Date.now()) + 10 * 3600000;
  const workout = { start, end: start + 1800000, type: "WALKING", name: "Walk", active_min: 28, kcal: 179, distance_m: 1369.2, avg_hr: 109, zone_min: { light: 23, moderate: 2 } };
  card.data = { sleep: [], exercise: [{ from: start, to: start + 1800000, workout }] };
  card.plot = { x: () => 0 };
  const html = card.hoverAt(start + 60000).html;
  for (const part of ["Walk", "28 min", "179 kcal", "1.37 km", "109 bpm", "light 23", "moderate 2"]) assert.ok(html.includes(part), `${part} in ${html}`);
});
