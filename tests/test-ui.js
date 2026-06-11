/* GAGE UI DOM test — executes the page like a browser would.
   Run from the repo root:  npm install jsdom && node tests/test-ui.js

   Catches the bug class that static analysis cannot: stale element references
   that only explode at runtime. Covers boot, the matrix-as-assignment grid,
   live cell states, and the seat inspector navigation. */
"use strict";
const { JSDOM } = require("jsdom");
const fs = require("fs");
const path = require("path");
const { execSync } = require("child_process");

const ROOT = path.resolve(__dirname, "..");
const html = fs.readFileSync(path.join(ROOT, "ui", "index.html"), "utf8");
// the stub serves the REAL registry, dumped live from the python package
const registry = JSON.parse(execSync(
  'python3 -c "import sys, json; sys.path.insert(0, \'.\'); from gage import server; print(json.dumps(server.get_registry()))"',
  { cwd: ROOT }).toString());

const consoleErrors = [];
let failed = 0;
function check(name, ok, detail) {
  console.log((ok ? "  PASS  " : "  FAIL  ") + name + (detail ? " — " + detail : ""));
  if (!ok) failed++;
}

const dom = new JSDOM(html, {
  url: "http://127.0.0.1:8000/",
  runScripts: "dangerously",
  pretendToBeVisual: true,
  beforeParse(window) {
    window.fetch = function (p) {
      const body = p.indexOf("/api/registry") !== -1 ? JSON.stringify(registry) : "{}";
      return Promise.resolve({ ok: true, status: 200, statusText: "OK",
                               text: () => Promise.resolve(body) });
    };
    window.Element.prototype.scrollIntoView = function () {};
    const origErr = window.console.error.bind(window.console);
    window.console.error = function () {
      consoleErrors.push(Array.from(arguments).map(String).join(" "));
      origErr.apply(null, arguments);
    };
  },
});

setTimeout(() => {
  const w = dom.window, d = w.document;
  const click = (node) => node.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));

  // 1) boot: registry applied, error channels labeled correctly
  const badge = d.getElementById("providerBadge").textContent;
  check("registry applied (badge)", badge.indexOf("mock") !== -1 || badge.indexOf("providers:") !== -1,
        "badge=" + JSON.stringify(badge));
  check("no 'registry unavailable' mislabel", badge.indexOf("unavailable") === -1);
  check("no 'UI init error'", badge.indexOf("init error") === -1);
  check("provider select: all 5 vendors + mock", d.getElementById("providerSel").options.length === 6);
  check("policy select populated", d.getElementById("policySel").options.length === 3);
  check("persona checklist populated", d.querySelectorAll("#personaPick input").length === 6);
  check("model roster field removed", !d.getElementById("modelInput"));
  check("bench controls present", !!d.getElementById("benchInput") && !!d.getElementById("benchAddBtn"));
  check("bench seeded with default models", Array.isArray(w.S.bench) && w.S.bench.length >= 1,
        "bench=" + JSON.stringify(w.S.bench));

  // 2) round-robin assignment IS the grid: one on-cell per charter row
  try {
    w.S.manifest = { id: "rfc-test", type: "rfc" };
    d.getElementById("panel-chamber").classList.remove("hidden");
    w.S.bench = []; // clear the seeded defaults — this section drives the bench by hand
    d.getElementById("benchInput").value = "model-a, model-b";
    click(d.getElementById("benchAddBtn"));
    const rows = d.querySelectorAll(".mgrid .m-row").length;
    const cols = d.querySelectorAll(".mgrid .m-col").length;
    const on = d.querySelectorAll(".mcell.on").length;
    const ghosts = d.querySelectorAll(".mcell.ghost").length;
    check("grid: 6 charters x 2 bench models", rows === 6 && cols === 2, "rows=" + rows + " cols=" + cols);
    check("round-robin: one seat per charter", on === 6 && ghosts === 6, "on=" + on + " ghost=" + ghosts);

    // reassign via cell click: contrarian defaults to model-b (index 3 % 2)
    const before = w.chamberSeatSpec().filter((s) => s.persona === "contrarian")[0].model;
    click(d.querySelector('[data-cell="contrarian|model-a"]'));
    const after = w.chamberSeatSpec().filter((s) => s.persona === "contrarian")[0].model;
    check("cell click reassigns the charter's model", before === "model-b" && after === "model-a",
          before + " -> " + after);

    // explicit model input per seat, synchronized with the grid
    const rowInputs = d.querySelectorAll('.mgrid .m-assign .s-model-inp[data-persona]');
    check("model input on every charter row", rowInputs.length === 6, "inputs=" + rowInputs.length);
    const inp = d.querySelector('.s-model-inp[data-persona="contrarian"]');
    check("input reflects the grid assignment", inp && inp.value === "model-a", inp && inp.value);
    inp.value = "model-b";
    inp.dispatchEvent(new w.Event("change", { bubbles: true }));
    const viaInp = w.chamberSeatSpec().filter((s) => s.persona === "contrarian")[0].model;
    check("input reassigns the seat", viaInp === "model-b", "model=" + viaInp);
    check("grid highlight follows the input",
          !!d.querySelector('[data-cell="contrarian|model-b"].on'));

    // any seat can hold a model that is NOT on the bench
    const inp2 = d.querySelector('.s-model-inp[data-persona="contrarian"]');
    inp2.value = "model-c";
    inp2.dispatchEvent(new w.Event("change", { bubbles: true }));
    const offBench = w.chamberSeatSpec().filter((s) => s.persona === "contrarian")[0].model;
    check("seat accepts an off-bench model", offBench === "model-c", "model=" + offBench);
    check("off-bench model becomes a grid column",
          !!d.querySelector('[data-cell="contrarian|model-c"].on'));

    // removing a column clears the assignments that pointed at it
    click(d.querySelector('[data-removemodel="model-c"]'));
    const reverted = w.chamberSeatSpec().filter((s) => s.persona === "contrarian")[0].model;
    check("removing a bench column reverts its seats", reverted === "model-b", "model=" + reverted);
    check("judge input present", d.querySelectorAll("[data-judge]").length === 1);
  } catch (e) { check("round-robin grid", false, e.message); }

  // 3) matrix: full cross product, cells seat/vacate, judge input
  try {
    d.querySelector('input[name=layoutMode][value="matrix"]').checked = true;
    w.S.bench = [];
    w.S.seatModel = {};
    d.getElementById("benchInput").value = "anthropic:claude-x, openai:gpt-y, gemini:gem-z, xai:grok-w";
    click(d.getElementById("benchAddBtn"));
    const on = d.querySelectorAll(".mcell.on").length;
    check("matrix: full cross product seated", on === 24, "on=" + on);
    click(d.querySelector('[data-cell="contrarian|openai:gpt-y"]'));
    const spec = w.chamberSeatSpec();
    const vacated = d.querySelectorAll(".mcell.off").length;
    check("vacating a cell prunes the matrix", spec.length === 23 && vacated === 1,
          "seats=" + spec.length + " off=" + vacated);
    click(d.querySelector('[data-cell="contrarian|openai:gpt-y"]'));
    check("re-seating restores it", w.chamberSeatSpec().length === 24);
    check("judge input rendered", d.querySelectorAll("[data-judge]").length === 1);
    const seatedCounts = d.querySelectorAll(".mgrid td.m-assign");
    check("matrix rows show seated counts", seatedCounts.length === 6 &&
          seatedCounts[0].textContent.indexOf("4/4") !== -1,
          seatedCounts.length + " rows, first=" + (seatedCounts[0] || {}).textContent);
    const legend = d.getElementById("modelLegend");
    check("legend lists all four families", !!legend &&
          ["claude-x", "gpt-y", "gem-z", "grok-w"].every((m) => legend.textContent.indexOf(m) !== -1));
  } catch (e) { check("matrix grid", false, e.message); }

  // 4) navigation: click a live cell -> that seat's full evaluation
  try {
    d.querySelector('input[name=layoutMode][value="round_robin"]').checked = true;
    w.S.run = {
      status: "awaiting_authorization", stage: "authorize",
      stage_seats: { critique: ["seat-1"], crossexam: ["seat-1"] },
      plan: { layout: "round_robin", chairman_model: "model-a",
              seats: [{ id: "seat-1", persona: "contrarian", model: "model-a", provider_mode: "mock" }] },
      critiques: [{ seat_id: "seat-1", persona: "contrarian", model: "model-a",
        narrative: "test narrative", instance_checks: [],
        scores: [{ dimension: "completeness", score: 3, anchor_cited: "anchor" }],
        findings: [{ id: "seat-1-f1", dimension: "completeness", severity: "major",
                     location: "§1", kind: "omission", statement: "THE-FINDING-STATEMENT",
                     evidence: "", recommendation: "" }] }],
      adjudications: [
        { adjudicator_seat: "seat-2", target_seat: "seat-1", finding_id: "seat-1-f1",
          verdict: "confirm", reasoning: "re-derived" },
        { adjudicator_seat: "seat-1", target_seat: "seat-2", finding_id: "seat-2-f1",
          verdict: "reject", reasoning: "does not reproduce" }],
      rankings: [], report: null, stages: {},
    };
    w.renderChamber();
    const cell = d.querySelector('[data-seat="seat-1"]');
    check("live cell carries findings payload", !!cell && cell.textContent.indexOf("1f") !== -1,
          cell ? cell.textContent.trim() : "no cell");
    click(cell);
    const insp = d.getElementById("seatInspector");
    check("inspector opens from the cell", !insp.classList.contains("hidden"));
    check("inspector shows the seat's finding", insp.textContent.indexOf("THE-FINDING-STATEMENT") !== -1);
    check("inspector shows the charter", insp.textContent.indexOf("Charter:") !== -1);
    check("inspector shows peer verdicts received", insp.textContent.indexOf("peer verdicts") !== -1);
    check("inspector shows cross-exam given", insp.textContent.indexOf("Cross-exam this seat performed (1)") !== -1);
    click(insp.querySelector("[data-close]"));
    check("inspector closes", insp.classList.contains("hidden"));
    w.S.run = null; w.S.inspectSeat = null;
    w.renderChamber();
  } catch (e) { check("seat inspector", false, e.message); }

  // 5) the error funnel itself: a swallowed chamber error must hit the console
  try {
    const realRender = w.renderChamber;
    w.renderChamber = function () { throw new Error("synthetic-chamber-boom"); };
    w.renderChamberSafe();
    w.renderChamber = realRender;
    check("chamber errors reach console.error",
          consoleErrors.some((m) => m.indexOf("synthetic-chamber-boom") !== -1));
  } catch (e) { check("error funnel", false, e.message); }

  // 6) no unexpected console errors from the whole exercise
  const unexpected = consoleErrors.filter((m) => m.indexOf("synthetic-chamber-boom") === -1);
  check("no unexpected console errors", unexpected.length === 0, unexpected.slice(0, 2).join(" | "));

  console.log(failed ? "\nDOM TEST FAILED (" + failed + ")" : "\nDOM TEST PASSED");
  process.exit(failed ? 1 : 0);
}, 400);
