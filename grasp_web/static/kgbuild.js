/* Rulebooks -> AutoGraph -> PlanGraph, from the browser.
 *
 * Shared by the video page, the repository page and the projects page. Two
 * pieces:
 *
 *   splitResultCard  a split generation: the reference rulebook, then one row per
 *                    task rulebook with its verdict, each expandable to the full
 *                    gate result, and a build panel under them
 *   buildPanel       project + module, a Check (dry run), then Build PlanGraph,
 *                    polled stage by stage while it runs
 *
 * The rulebooks are named, never sent: the server reads them from the finished
 * generation job (or from disk), so what reaches AutoGraph is exactly what the
 * gate graded. Relies on $, el and esc from the page's own script.
 */

const KG_STATUS = {
  pending: { mark: "○", cls: "pending", label: "waiting" },
  running: { mark: "", cls: "running", label: "running" },
  done: { mark: "✓", cls: "done", label: "done" },
  skipped: { mark: "·", cls: "skipped", label: "already done" },
  planned: { mark: "◌", cls: "planned", label: "would run" },
  failed: { mark: "✗", cls: "failed", label: "failed" },
};

const kgSleep = (ms) => new Promise((r) => setTimeout(r, ms));

let kgHealth = null;
async function kgHealthCheck() {
  if (kgHealth) return kgHealth;
  try {
    kgHealth = await (await fetch("/api/kg/health")).json();
  } catch (error) {
    kgHealth = { available: false, reason: "the server did not answer" };
  }
  return kgHealth;
}

// ---------------------------------------------------- a split generation ---

function splitResultCard(result, jobId) {
  const wrap = el(`<div class="split"></div>`);
  const reference = result.reference || {};
  const refBook = (reference.extraction && reference.extraction.intermediate) || null;
  const tasks = result.tasks || [];

  const head = el(`<div class="card gen-${tasks.length ? "accept" : "reject"}">
    <div class="card-head">
      <span class="goal">${esc(refBook ? refBook.skill : "no reference rulebook")}</span>
      <span class="verdict ${tasks.length ? "accept" : "reject"}">${tasks.length} task rulebook(s)</span>
      <span class="meta"></span>
    </div>
    <div class="card-body split-note"></div>
  </div>`);
  const note = head.querySelector(".split-note");
  if (refBook) {
    note.appendChild(
      el(`<p>One reference rulebook of <b>${refBook.primitives.length}</b> steps, cut into
        <b>${tasks.length}</b> task rulebook(s) — each a goal step plus every step its
        preconditions need. <b>${result.accepted || 0}</b> passed the gate.</p>`)
    );
  }
  if (result.method === "rules") {
    note.appendChild(
      el(`<p class="warn-line">Tasks were chosen by rule, one per final step:
        ${esc(result.fallback_reason || "the model's task list was not usable")}.</p>`)
    );
  }
  if (result.reason) note.appendChild(el(`<p class="warn-line">${esc(result.reason)}</p>`));
  wrap.appendChild(head);

  if (!tasks.length) {
    wrap.appendChild(resultCard(reference));
    return wrap;
  }

  const list = el(`<div class="card tasks">
    <div class="md-head"><span class="t">task rulebooks — ticked ones are built</span>
      <span class="acts"><button class="mini" type="button" data-act="ref">reference rulebook</button></span></div>
    <div class="task-rows"></div>
  </div>`);
  const rows = list.querySelector(".task-rows");
  const chosen = new Set();

  for (const task of tasks) {
    const book = (task.extraction && task.extraction.intermediate) || { skill: "?", primitives: [] };
    const v = VERDICT[task.verdict] || VERDICT.reject;
    const assumes = (result.assumes || {})[book.skill] || [];
    const row = el(`<div class="task-row">
      <label class="task-line">
        <input type="checkbox" ${task.verdict === "reject" ? "disabled" : ""} />
        <span class="task-name">${esc(book.skill)}</span>
        <span class="verdict ${v.cls}">${v.label}</span>
        <span class="task-steps">${book.primitives.map((p) => esc(p.name)).join(" → ")}</span>
        ${assumes.length ? `<span class="task-assumes">assumes ${assumes.map(esc).join(", ")}</span>` : ""}
      </label>
      <button class="mini" type="button">details</button>
      <div class="task-detail" hidden></div>
    </div>`);
    const tick = row.querySelector("input");
    // An accept is built by default; a flag is the human's call; a reject cannot be.
    tick.checked = task.verdict === "accept";
    if (tick.checked) chosen.add(task.filename);
    tick.addEventListener("change", () => {
      if (tick.checked) chosen.add(task.filename);
      else chosen.delete(task.filename);
      panel.refresh();
    });
    const detail = row.querySelector(".task-detail");
    row.querySelector("button").addEventListener("click", () => {
      if (!detail.firstChild) detail.appendChild(resultCard(task));
      detail.hidden = !detail.hidden;
    });
    rows.appendChild(row);
  }

  const refBox = el(`<div class="task-detail" hidden></div>`);
  list.appendChild(refBox);
  list.querySelector('[data-act="ref"]').addEventListener("click", () => {
    if (!refBox.firstChild) refBox.appendChild(resultCard(reference));
    refBox.hidden = !refBox.hidden;
  });
  wrap.appendChild(list);

  const panel = buildPanel({
    job: jobId,
    files: () => [...chosen],
    suggest: refBook ? refBook.skill : "",
  });
  wrap.appendChild(panel.element);
  return wrap;
}

/* A single (unsplit) rulebook can be built too, on its own. */
function singleBuildPanel(result, jobId) {
  if (!result || !result.markdown || result.verdict === "reject") return null;
  return buildPanel({ job: jobId, files: () => [result.filename] }).element;
}

// ------------------------------------------------------- the build panel ---

/* `source` is `{job, files}` for a generation, or `{files}` alone for rulebooks on
   disk. `files` is a function so the ticks can change after the panel exists. */
function buildPanel({ job = "", files, suggest = "" }) {
  const element = el(`<div class="card kg">
    <div class="card-head">
      <span class="goal">Build the PlanGraph</span>
      <span class="meta"><span class="pill kg-count"></span></span>
    </div>
    <div class="card-body">
      <p class="faint">Uploads the ticked rulebooks into <b>one module</b> of an AutoGraph
        project, builds the corpus graph, the strategies with the
        <code>SKILL · PRIMITIVE · OBJECT · STATE</code> ontology and the knowledge graph,
        then the PlanGraph. <b>Check</b> reads the project and writes nothing.</p>
      <div class="kg-form">
        <label>project <input class="kg-project" placeholder="openAMR" /></label>
        <label>module <input class="kg-category" value="rulebooks" /></label>
        <label class="kg-opt"><input type="checkbox" class="kg-rebuild" /> rebuild the module</label>
        <label class="kg-opt"><input type="checkbox" class="kg-provision" /> create project / deploy service if missing</label>
        <label class="kg-fps" hidden title="The ArangoDB user the File Parsing Service resumes a corpus build as. The platform needs one to install AutoGraph; left empty, one with rw on the database is found.">
          FPS recovery user <input class="kg-fps-user" placeholder="found automatically" /></label>
      </div>
      <div class="kg-acts">
        <button class="mini kg-check" type="button">Check</button>
        <button class="go small kg-build" type="button" disabled>Build PlanGraph</button>
        <span class="kg-why faint"></span>
      </div>
    </div>
    <div class="kg-run" hidden></div>
  </div>`);

  const $$ = (cls) => element.querySelector(cls);
  const project = $$(".kg-project");
  const category = $$(".kg-category");
  const rebuild = $$(".kg-rebuild");
  const provision = $$(".kg-provision");
  const fpsUser = $$(".kg-fps-user");
  const check = $$(".kg-check");
  const build = $$(".kg-build");
  const why = $$(".kg-why");
  const runBox = $$(".kg-run");

  if (suggest) project.placeholder = suggest.replace(/^operate_/, "").replace(/_/g, "-");

  //: The inputs the last successful check was run against. Build is offered
  //: only for exactly those, so what is built is what was checked.
  let checked = "";
  let running = false;
  const key = () =>
    JSON.stringify([project.value.trim(), category.value.trim(), rebuild.checked, provision.checked,
      fpsUser.value.trim(), files()]);

  function refresh() {
    $$(".kg-fps").hidden = !provision.checked;
    const n = files().length;
    $$(".kg-count").textContent = `${n} rulebook(s)`;
    const ready = checked && checked === key();
    check.disabled = running || !n || !project.value.trim() || !category.value.trim();
    build.disabled = running || !ready;
    why.textContent = running
      ? ""
      : !n
      ? "tick at least one rulebook"
      : !project.value.trim()
      ? "name the project"
      : ready
      ? ""
      : "run a check first";
  }
  for (const input of [project, category, rebuild, provision, fpsUser]) {
    input.addEventListener("input", refresh);
    input.addEventListener("change", refresh);
  }

  kgHealthCheck().then((health) => {
    if (!health.available) {
      check.disabled = build.disabled = true;
      why.textContent = health.reason || "the AutoGraph pipeline is not configured";
      running = true; // keeps the buttons off
    }
  });

  async function start(write) {
    if (running) return;
    if (write && !confirmBuild()) return;
    running = true;
    refresh();
    runBox.hidden = false;
    runBox.innerHTML = "";

    const view = progressView(write);
    runBox.appendChild(view.element);

    try {
      const response = await fetch("/api/kg/build", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          job,
          files: files(),
          project: project.value.trim(),
          category: category.value.trim(),
          rebuild: rebuild.checked,
          provision: provision.checked,
          fps_user: provision.checked ? fpsUser.value.trim() : "",
          write,
        }),
      });
      const started = await response.json();
      if (started.error) throw new Error(started.error + (started.hint ? ` — ${started.hint}` : ""));

      const done = await pollRun(started.id, (state) => view.update(state));
      view.finish(done);
      if (!write && done.state === "done" && done.result && done.result.ok) checked = key();
      if (write) checked = "";
    } catch (error) {
      view.fail(String(error.message || error));
    }
    running = false;
    refresh();
  }

  function confirmBuild() {
    const lines = [
      `Build ${files().length} rulebook(s) into ${project.value.trim()} / ${category.value.trim()}?`,
      "",
      "This uploads files to File Manager, runs AutoGraph's corpus build, strategizer and importer (model tokens are spent), and writes the PlanGraph.",
    ];
    if (rebuild.checked) lines.push("", "REBUILD: the module's corpus, strategies, knowledge-graph partitions and files are deleted first.");
    if (provision.checked) lines.push("", "PROVISION: a missing project is created and an AutoGraph service deployed.");
    return window.confirm(lines.join("\n"));
  }

  check.addEventListener("click", () => start(false));
  build.addEventListener("click", () => start(true));
  refresh();
  return { element, refresh };
}

async function pollRun(id, onState) {
  // A knowledge-graph build can take an hour. Poll steadily, without a cap
  // tighter than the server's own stage timeouts.
  for (;;) {
    let state;
    try {
      state = await (await fetch(`/api/kg/build/${id}`)).json();
    } catch (error) {
      await kgSleep(5000); // a dropped poll is not an answer; the run carries on
      continue;
    }
    if (state.error && !state.state) return { state: "error", error: state.error };
    onState(state);
    if (state.state !== "running") return state;
    await kgSleep(2500);
  }
}

/* The live view: one row per stage, the running one with its latest message. */
function progressView(write) {
  const element = el(`<div class="kg-progress">
    <div class="stage"><span class="spin"></span>
      <span class="what">${write ? "building" : "checking"}</span>
      <span class="elapsed">0s</span></div>
    <div class="kg-stages"></div>
    <details class="kg-log" hidden><summary>log</summary><pre></pre></details>
  </div>`);
  const what = element.querySelector(".what");
  const stages = element.querySelector(".kg-stages");
  const log = element.querySelector(".kg-log");
  const started = Date.now();
  const ticker = setInterval(() => {
    const s = Math.round((Date.now() - started) / 1000);
    element.querySelector(".elapsed").textContent = s < 120 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
  }, 1000);

  function render(detail) {
    stages.innerHTML = "";
    for (const row of detail.stages || []) {
      const s = KG_STATUS[row.status] || KG_STATUS.pending;
      const live = row.status === "running" && detail.message ? detail.message : "";
      stages.appendChild(
        el(`<div class="kg-stage ${s.cls}">
          <span class="kg-mark">${row.status === "running" ? '<span class="spin"></span>' : s.mark}</span>
          <span class="kg-name">${esc(row.stage)}</span>
          <span class="kg-detail">${esc(row.detail || live || (row.status === "pending" ? "" : s.label))}</span>
        </div>`)
      );
    }
    const lines = detail.log || [];
    if (lines.length) {
      log.hidden = false;
      log.querySelector("pre").textContent = lines.map((l) => `${l.stage}: ${l.message}`).join("\n");
    }
  }

  return {
    element,
    update(state) {
      const detail = state.detail || {};
      what.textContent = detail.current
        ? `${detail.current}${detail.message ? " — " + detail.message : ""}`
        : state.stage || (write ? "building" : "checking");
      render(detail);
    },
    finish(state) {
      clearInterval(ticker);
      element.querySelector(".stage").remove();
      if (state.state === "error") {
        element.prepend(errorCard(state.error));
        return;
      }
      render(state.detail || {});
      const result = state.result || {};
      element.prepend(outcome(result, write));
    },
    fail(message) {
      clearInterval(ticker);
      element.querySelector(".stage").remove();
      element.prepend(errorCard(message));
    },
  };
}

function outcome(result, write) {
  if (!result.ok) {
    return el(`<div class="issues"><div class="issue fatal">
      <div class="lvl">stopped</div>
      <div><code>${esc(result.failed_stage)}</code>
        <div class="detail">${esc(result.failed)}</div>
        ${result.hint ? `<div class="detail faint">likely fix: ${esc(result.hint)}</div>` : ""}</div>
    </div></div>`);
  }
  if (!write) {
    return el(`<div class="clean">Check complete — nothing was uploaded or written. Build runs
      the stages marked <b>would run</b>; the ones marked <b>already done</b> are skipped.</div>`);
  }
  const scopes = ((result.plangraph || {}).scopes || []).map((s) => `<code>${esc(s)}</code>`).join(" ");
  const box = el(`<div class="clean">PlanGraph built for <b>${esc(result.project)}</b>${
    scopes ? `: ${scopes}` : ""
  }. <a class="mini" href="/?project=${encodeURIComponent(result.project)}">Ask for a plan</a>
    <a class="mini" href="/projects">projects</a></div>`);
  return box;
}
