/* The projects page: discover, build, view, ask.
 *
 * The page is a state machine with one card per project, and the state comes
 * from the database rather than from anything this page remembers:
 *
 *     corpus  -> go and build the knowledge graph in AutoGraph
 *     kg      -> Build PlanGraph  (the one action here that writes)
 *     built   -> View in ArangoDB | Ask
 *
 * A build is confirmed before it starts and names what it will replace, because
 * Station 5's scoped write deletes a skill's existing subgraph before rewriting
 * it. The confirmation is built from the listing the server just returned, so it
 * describes what is actually there.
 */

const $ = (id) => document.getElementById(id);
const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
const el = (html) => {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

let busy = false;

// -------------------------------------------------------------- boot --------

async function boot() {
  try {
    const payload = await (await fetch("/api/projects")).json();
    if (payload.error) throw new Error(payload.error + (payload.hint ? ` — ${payload.hint}` : ""));
    renderPills(payload);
    renderProjects(payload.projects);
  } catch (error) {
    $("pills").innerHTML = `<span class="pill warn"><b>offline</b></span>`;
    $("projects").innerHTML = "";
    $("projects").appendChild(
      el(`<div class="card error"><div class="card-body">${esc(error.message || error)}</div></div>`)
    );
  }
}

function renderPills(payload) {
  $("pills").innerHTML = [
    `<span class="pill">database <b>${esc(payload.database)}</b></span>`,
    `<span class="pill">projects <b>${payload.projects.length}</b></span>`,
    `<span class="pill${payload.writes ? "" : " warn"}" title="${
      payload.writes
        ? "Station 5 has credentials to write a PlanGraph with."
        : "No write credentials: projects can be listed and planned, but not built."
    }">build <b>${payload.writes ? "enabled" : "read-only"}</b></span>`,
  ].join("");
}

// ----------------------------------------------------------- the cards ------

const STAGE = {
  empty: { cls: "empty", label: "nothing built" },
  corpus: { cls: "corpus", label: "corpus graph" },
  kg: { cls: "kg", label: "knowledge graph" },
  plangraph: { cls: "plan", label: "PlanGraph" },
};

function renderProjects(projects) {
  const box = $("projects");
  box.innerHTML = "";
  if (!projects.length) {
    box.appendChild(
      el(`<div class="card"><div class="card-body">
        <p>No projects in this database yet. Build a corpus graph and a knowledge
        graph in AutoGraph, and they will appear here.</p></div></div>`)
    );
    return;
  }
  for (const project of projects) box.appendChild(projectCard(project));
}

function projectCard(project) {
  const stage = STAGE[project.stage] || STAGE.empty;
  const card = el(`<div class="card project ${stage.cls}" data-project="${esc(project.name)}">
    <div class="card-head">
      <span class="goal">${esc(project.name)}</span>
      <span class="verdict ${stage.cls}">${stage.label}</span>
      <span class="meta"></span>
    </div>
    <div class="steps-row"></div>
    <div class="project-body"></div>
  </div>`);

  const meta = card.querySelector(".meta");
  if (project.entities) {
    meta.appendChild(
      el(`<span class="pill">${project.entities} entities · ${project.relations} relations</span>`)
    );
  }
  if (project.skills) {
    meta.appendChild(
      el(`<span class="pill">${project.skills} skills · ${project.plan_edges} edges</span>`)
    );
  }

  card.querySelector(".steps-row").appendChild(stepsRow(project));
  card.querySelector(".project-body").appendChild(actions(project));
  return card;
}

/* The three stages, so a card says where it is without reading the prose. */
function stepsRow(project) {
  const row = el(`<div class="stages"></div>`);
  const done = [project.has_corpus, project.has_kg, project.stage === "plangraph"];
  ["corpus graph", "knowledge graph", "PlanGraph"].forEach((label, index) => {
    row.appendChild(
      el(`<span class="stage-dot ${done[index] ? "on" : "off"}">
        <i>${done[index] ? "●" : "○"}</i>${esc(label)}</span>`)
    );
  });
  return row;
}

function actions(project) {
  const box = el(`<div class="actions"></div>`);
  const graphs = project.graphs || {};

  if (project.plannable) {
    const ask = el(`<a class="go small" href="/?project=${encodeURIComponent(project.name)}">Ask for a plan</a>`);
    box.appendChild(ask);
  }
  if (graphs.plangraph) {
    box.appendChild(link(graphs.plangraph, "View PlanGraph in ArangoDB"));
  }
  if (graphs.kg) box.appendChild(link(graphs.kg, "View knowledge graph"));
  else if (graphs.corpus) box.appendChild(link(graphs.corpus, "View corpus graph"));

  if (project.building) {
    box.appendChild(el(`<span class="note-inline">a build is already running…</span>`));
  } else if (project.buildable) {
    const rebuild = project.stage === "plangraph";
    const button = el(
      `<button class="${rebuild ? "mini" : "go small"}" type="button">${
        rebuild ? "Rebuild PlanGraph" : "Build PlanGraph"
      }</button>`
    );
    button.addEventListener("click", () => askToBuild(project));
    box.appendChild(button);
  } else if (project.blocked_reason && !project.plannable) {
    // Only when it is actually in the way. A project built by another route has
    // no knowledge graph and does not need one, so saying "no knowledge graph
    // yet" over a working PlanGraph would report a problem that is not one.
    box.appendChild(el(`<div class="blocked">${esc(project.blocked_reason)}</div>`));
  }

  if (project.scopes && project.scopes.length) {
    box.appendChild(
      el(`<div class="scopes">plans: ${project.scopes.map((s) => `<code>${esc(s)}</code>`).join(" ")}</div>`)
    );
  }
  return box;
}

function link(href, text) {
  /* rel=noopener because these open ArangoDB's own UI in another tab. */
  return el(`<a class="mini" href="${esc(href)}" target="_blank" rel="noopener noreferrer">${esc(text)} ↗</a>`);
}

// ------------------------------------------------------------- confirm ------

let pending = null;

function askToBuild(project) {
  pending = project;
  const rebuild = project.stage === "plangraph";
  const collections = ["Skills", "Primitives", "Objects", "States", "PlanEdges"]
    .map((c) => `<code>${esc(project.name)}_${c}</code>`)
    .join(" ");

  $("confirm-title").textContent = rebuild ? "Rebuild the PlanGraph?" : "Build the PlanGraph?";
  $("confirm-go").textContent = rebuild ? "Rebuild" : "Build";
  $("confirm-body").innerHTML = `
    <p>This reads <code>${esc(project.name)}_Relations</code> and writes into ${collections}.
       It is the only action in this UI that writes to the database.</p>
    ${
      rebuild && project.scopes.length
        ? `<p class="warn-line">Rebuilding <b>deletes and recreates</b> the existing subgraph for
             ${project.scopes.map((s) => `<code>${esc(s)}</code>`).join(", ")}. Anything else in the
             database is untouched.</p>`
        : ""
    }
    <p class="faint">${esc(project.relations)} relationships will be read. The ambiguous ones go to a
       model, so this can take a minute.</p>`;
  $("confirm").hidden = false;
  $("confirm-go").focus();
}

function closeConfirm() {
  $("confirm").hidden = true;
  pending = null;
}

$("confirm-cancel").addEventListener("click", closeConfirm);
$("confirm").addEventListener("click", (event) => {
  if (event.target === $("confirm")) closeConfirm();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !$("confirm").hidden) closeConfirm();
});
$("confirm-go").addEventListener("click", () => {
  const project = pending;
  closeConfirm();
  if (project) runBuild(project);
});

// ------------------------------------------------------------- building -----

async function runBuild(project) {
  if (busy) return;
  busy = true;

  const started = Date.now();
  const turn = el(`<div class="turn">
    <div class="asked"><span class="caret">&gt;</span><span class="text">build ${esc(project.name)}</span></div>
    <div class="card"><div class="stage">
      <span class="spin"></span><span class="what">starting the bridge</span>
      <span class="elapsed">0s</span>
    </div>
    <div class="stations"></div></div>
  </div>`);
  $("stream").appendChild(turn);
  turn.scrollIntoView({ behavior: "smooth", block: "start" });

  const card = turn.querySelector(".card");
  const what = turn.querySelector(".what");
  const elapsed = turn.querySelector(".elapsed");
  const stations = turn.querySelector(".stations");
  const ticker = setInterval(() => {
    elapsed.textContent = `${Math.round((Date.now() - started) / 1000)}s`;
  }, 1000);

  try {
    const response = await fetch("/api/projects/build", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ project: project.name }),
    });
    const job = await response.json();
    if (job.error) throw new Error(job.error + (job.hint ? ` — ${job.hint}` : ""));

    const done = await poll(job.id, (state) => {
      what.textContent = state.stage;
      renderStations(stations, (state.detail && state.detail.stations) || []);
    });
    clearInterval(ticker);

    if (done.state === "error") {
      card.replaceWith(errorCard(done.error));
    } else {
      card.replaceWith(buildResult(done.result, (done.detail || {}).stations || []));
      await boot(); // the project's stage has changed; re-read it rather than guess
    }
  } catch (error) {
    clearInterval(ticker);
    card.replaceWith(errorCard(String(error.message || error)));
  }

  busy = false;
}

async function poll(id, onState) {
  let seen = "";
  for (let attempt = 0; attempt < 900; attempt++) {
    const job = await (await fetch(`/api/projects/build/${id}`)).json();
    if (job.stage !== seen) {
      seen = job.stage;
      onState(job);
    } else {
      onState(job);
    }
    if (job.state !== "running") return job;
    await sleep(1500);
  }
  return { state: "error", error: "timed out waiting for the build to finish" };
}

function renderStations(box, stations) {
  box.innerHTML = "";
  for (const station of stations) {
    box.appendChild(
      el(`<div class="station">
        <span class="tick">✓</span>
        <span class="s-name">${esc(station.stage)}</span>
        <span class="s-detail">${esc(station.detail)}</span>
      </div>`)
    );
  }
}

function errorCard(message) {
  return el(`<div class="card error"><div class="card-body">${esc(message)}</div></div>`);
}

function buildResult(result, stations) {
  const ok = result.ok;
  const card = el(`<div class="card gen-${ok ? "accept" : "reject"}">
    <div class="card-head">
      <span class="goal">${esc(result.project)}</span>
      <span class="verdict ${ok ? "accept" : "reject"}">${ok ? "built" : "not built"}</span>
      <span class="meta"></span>
    </div>
    <div class="stations"></div>
  </div>`);

  renderStations(card.querySelector(".stations"), stations.length ? stations : result.stations);

  if (!ok) {
    card.appendChild(
      el(`<div class="issues"><div class="issue fatal">
        <div class="lvl">why</div><div class="detail">${esc(result.failed || "the build produced nothing")}</div>
      </div></div>`)
    );
    return card;
  }

  const written = result.written || {};
  const stats = el(`<div class="stats"></div>`);
  stats.appendChild(
    el(`<div class="stat hero"><div class="v">${result.scopes.length}</div><div class="k">skill scopes</div></div>`)
  );
  for (const [collection, count] of Object.entries(written)) {
    stats.appendChild(
      el(`<div class="stat"><div class="v">${count}</div><div class="k">${esc(
        collection.replace(`${result.project}_`, "")
      )}</div></div>`)
    );
  }
  card.appendChild(stats);

  // What the model actually cost. Reported separately from what the lexical
  // pre-pass settled, because on some builds the model is never called at all.
  const llm = result.llm || {};
  if (llm.settled) {
    card.appendChild(
      el(`<div class="clean">${llm.lexical} relationship(s) settled by the lexical pre-pass${
        llm.by_model
          ? `, ${llm.by_model} by ${esc(llm.model)} in ${llm.requests} request(s)`
          : " — the model was not called"
      }.${result.parked ? ` ${result.parked} parked.` : ""}</div>`)
    );
  }

  if (result.replaced && result.replaced.length) {
    card.appendChild(
      el(`<div class="issues"><div class="issue warn"><div class="lvl">note</div>
        <div class="detail">replaced the existing subgraph for
          ${result.replaced.map((s) => `<code>${esc(s)}</code>`).join(", ")}</div></div></div>`)
    );
  }

  const acts = el(`<div class="md-head"><span class="t">${esc(result.graph)}</span>
    <span class="acts"></span></div>`);
  if (result.graph_url) {
    acts.querySelector(".acts").appendChild(
      el(`<a class="mini" href="${esc(result.graph_url)}" target="_blank" rel="noopener noreferrer">View in ArangoDB ↗</a>`)
    );
  }
  acts.querySelector(".acts").appendChild(
    el(`<a class="mini" href="/?project=${encodeURIComponent(result.project)}">Ask for a plan</a>`)
  );
  card.appendChild(acts);
  return card;
}

boot();
