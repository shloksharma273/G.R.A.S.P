/* The page's whole job is to ask /api/plan and render what comes back.
 *
 * It makes no planning decisions: the goal, the step order, the confidence and
 * the clarification all arrive already decided. Keeping it that way is what
 * stops the browser and the CLI from drifting apart in what they answer.
 */

const $ = (id) => document.getElementById(id);

const stream = $("stream");
const composer = $("composer");
const input = $("command");
const send = $("send");
const llmToggle = $("llm");

let busy = false;

/* Everything user-supplied goes through here. Skill names and step wording come
 * from a knowledge graph and a language model, so neither is trusted markup. */
const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );

const el = (html) => {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content.firstElementChild;
};

// -------------------------------------------------------------- boot --------

async function boot() {
  try {
    const health = await (await fetch("/api/health")).json();
    renderPills(health);
    llmToggle.checked = health.phrasing === "model";
    llmToggle.disabled = !health.model;
    if (!health.model) {
      llmToggle.parentElement.title =
        "No LLM key configured, so step wording is templated. The order is unchanged.";
    }
  } catch (error) {
    $("pills").innerHTML = `<span class="pill warn"><b>offline</b></span>`;
  }

  try {
    const { skills } = await (await fetch("/api/skills")).json();
    renderSuggestions(skills);
  } catch (error) {
    $("suggestions").innerHTML = "";
  }
}

function renderPills(health) {
  const vector = health.vector_index;
  $("pills").innerHTML = [
    `<span class="pill">db <b>${esc(health.database)}</b></span>`,
    `<span class="pill">graph <b>${esc(health.graph)}</b></span>`,
    `<span class="pill">skills <b>${health.skills}</b></span>`,
    `<span class="pill${vector ? "" : " warn"}" title="${
      vector
        ? "Vector search over the Skills index."
        : "No vector index yet, so goal resolution scores the command against the Skills in the graph by TF-IDF. The threshold and tie-margin semantics are unchanged."
    }">retrieval <b>${esc(retrievalLabel(health.retrieval))}</b></span>`,
  ].join("");
}

function renderSuggestions(skills) {
  const box = $("suggestions");
  if (!skills.length) {
    box.innerHTML = `<span class="chip">This PlanGraph holds no skills yet — run the bridge first.</span>`;
    return;
  }
  box.innerHTML = "";
  for (const skill of skills) {
    const chip = el(
      `<button class="chip" type="button">${esc(readable(skill.name))}<span class="n">${skill.steps} steps</span></button>`
    );
    chip.addEventListener("click", () => {
      input.value = readable(skill.name);
      input.focus();
      ask(input.value);
    });
    box.appendChild(chip);
  }
}

const readable = (name) => String(name).replace(/_/g, " ").toLowerCase();

/* Display names for the retrieval method. The API keeps reporting the real
 * method - and plan.json's meta.match_method still records it - so what a plan
 * claims about itself stays accurate; this only changes the header label. */
const RETRIEVAL_LABEL = { lexical: "graphical", vector: "vector" };
const retrievalLabel = (method) => RETRIEVAL_LABEL[method] || method;

// -------------------------------------------------------------- asking ------

composer.addEventListener("submit", (event) => {
  event.preventDefault();
  ask(input.value);
});

async function ask(command) {
  command = (command || "").trim();
  if (!command || busy) return;

  busy = true;
  send.disabled = true;
  $("welcome").style.display = "none";

  const turn = el(`<div class="turn">
    <div class="asked"><span class="caret">&gt;</span><span class="text">${esc(command)}</span></div>
    <div class="card thinking">walking the graph<span class="dots"></span></div>
  </div>`);
  stream.appendChild(turn);
  turn.scrollIntoView({ behavior: "smooth", block: "start" });
  input.value = "";

  let payload;
  try {
    const response = await fetch("/api/plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ command, use_llm: llmToggle.checked }),
    });
    payload = await response.json();
  } catch (error) {
    payload = { kind: "error", message: `could not reach the planner (${error})` };
  }

  turn.querySelector(".card").replaceWith(render(payload));
  busy = false;
  send.disabled = false;
  input.focus();
}

// ------------------------------------------------------------ rendering -----

function render(payload) {
  if (payload.kind === "plan") return renderPlan(payload);
  if (payload.kind === "clarification") return renderClarification(payload);
  return el(
    `<div class="card error"><div class="card-body">${esc(payload.message || "something went wrong")}</div></div>`
  );
}

function renderPlan(plan) {
  const meta = plan.meta || {};
  const ordering = meta.ordering || {};
  const derived = ordering.from_state_chain ?? 0;

  const pills = [
    `<span class="pill">match <b>${(meta.match_confidence ?? 0).toFixed(2)}</b></span>`,
    `<span class="pill">${plan.steps.length} steps</span>`,
    `<span class="pill" title="Constraints from precedes edges and from the produces/requires state chain.">ordering <b>${ordering.constraints ?? 0}</b></span>`,
    `<span class="pill" title="${
      meta.composer === "llm"
        ? "The model wrote the wording only. The order came from the graph."
        : "Templated wording. The order is identical to the model-written version."
    }">${meta.composer === "llm" ? "model wording" : "templated"}</span>`,
  ].join("");

  const card = el(`<div class="card">
    <div class="card-head">
      <span class="goal">${esc(plan.goal)}</span>
      <span class="meta">${pills}</span>
    </div>
    <ol class="steps"></ol>
  </div>`);

  const list = card.querySelector("ol");
  for (const step of plan.steps) {
    const tags = [
      ...step.requires.map((s) => `<span class="tag req"><i>requires</i>${esc(readable(s))}</span>`),
      ...step.produces.map((s) => `<span class="tag prod"><i>produces</i>${esc(readable(s))}</span>`),
      ...step.uses.map((s) => `<span class="tag uses"><i>uses</i>${esc(readable(s))}</span>`),
    ].join("");

    list.appendChild(
      el(`<li>
        <div class="num">${step.order}</div>
        <div class="desc">
          <div class="action">${esc(step.action)}</div>
          ${esc(step.description)}
        </div>
        ${tags ? `<div class="tags">${tags}</div>` : ""}
      </li>`)
    );
  }

  if (meta.warning) {
    card.appendChild(
      el(`<div class="card-body"><p>${esc(meta.warning)}</p></div>`)
    );
  }
  return card;
}

function renderClarification(payload) {
  const card = el(`<div class="card clarify">
    <div class="card-head"><span class="goal">needs clarifying</span></div>
    <div class="card-body">
      <p>${esc(payload.reason)}</p>
      <div class="candidates"></div>
    </div>
  </div>`);

  const box = card.querySelector(".candidates");
  for (const candidate of payload.candidates || []) {
    const chip = el(
      `<button class="chip" type="button"><span class="score">${candidate.score.toFixed(2)}</span>${esc(readable(candidate.skill))}</button>`
    );
    chip.addEventListener("click", () => ask(readable(candidate.skill)));
    box.appendChild(chip);
  }
  if (!box.children.length) box.remove();
  return card;
}

boot();
input.focus();
