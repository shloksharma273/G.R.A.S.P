/* Rendering a generation result — shared by the video page and the repo page.
 *
 * Both pages start a job, poll it, and show the same thing at the end: the
 * verdict, the counts, the issues the gate raised, the plan the rulebook implies,
 * and the markdown. Only the input differs, so only the input lives in the page.
 *
 * Nothing here decides anything. The verdict, the issues and the plan all arrive
 * already settled by the validation gate; this turns them into elements.
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

const readable = (name) => String(name).replace(/_/g, " ").toLowerCase();
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/* Generation runs for a minute or two. Poll gently — this is a single-user dev
   server and a tight loop would buy nothing. */
async function pollJob(id, onStage) {
  let lastStage = "";
  for (let attempt = 0; attempt < 600; attempt++) {
    const job = await (await fetch(`/api/generate/${id}`)).json();
    if (job.stage && job.stage !== lastStage) {
      lastStage = job.stage;
      onStage(job.stage, job);
    }
    if (job.state !== "running") return job;
    await sleep(1500);
  }
  return { state: "error", error: "timed out waiting for the generation to finish" };
}

/* A running job: the stage it has reached and how long it has been going. */
function runningCard() {
  const card = el(`<div class="card"><div class="stage">
    <span class="spin"></span><span class="what">starting</span>
    <span class="elapsed">0s</span>
  </div></div>`);
  const started = Date.now();
  const elapsed = card.querySelector(".elapsed");
  const ticker = setInterval(() => {
    elapsed.textContent = `${Math.round((Date.now() - started) / 1000)}s`;
  }, 1000);
  return {
    card,
    stage: (text) => (card.querySelector(".what").textContent = text),
    stop: () => clearInterval(ticker),
  };
}

function errorCard(message) {
  return el(`<div class="card error"><div class="card-body">${esc(message)}</div></div>`);
}

const VERDICT = {
  accept: { cls: "accept", label: "accept" },
  flag_for_review: { cls: "flag", label: "flag for review" },
  reject: { cls: "reject", label: "reject" },
};

function resultCard(result) {
  const v = VERDICT[result.verdict] || VERDICT.reject;
  const book = (result.extraction && result.extraction.intermediate) || null;
  const validation = result.validation || {};

  const card = el(`<div class="card gen-${v.cls}">
    <div class="card-head">
      <span class="goal">${esc(book ? book.skill : "no rulebook")}</span>
      <span class="verdict ${v.cls}">${v.label}</span>
      <span class="meta"></span>
    </div>
  </div>`);

  const meta = card.querySelector(".meta");
  if (result.video && result.video.words) {
    meta.appendChild(el(`<span class="pill">${result.video.words} words in</span>`));
  }
  if (result.extraction && result.extraction.from_cache) {
    meta.appendChild(
      el(`<span class="pill" title="Same source text as a previous run, so no model call was made.">cached</span>`)
    );
  }

  if (book) card.appendChild(statsBlock(book, validation, result));
  if (book) {
    const calls = callsBlock(book);
    if (calls) card.appendChild(calls);
  }
  card.appendChild(issuesBlock(validation, result));
  if (validation.plan && validation.plan.length) card.appendChild(planBlock(validation.plan));
  if (result.markdown) card.appendChild(markdownBlock(result));

  return card;
}

/* How each step is actually invoked.
 *
 * Only rendered when the rulebook has handles at all - a rulebook from a video
 * or a manual describes what a person does and has none. When it does have them,
 * this is the thing the whole code register exists to produce, so it gets a table
 * rather than only appearing inside the markdown. A step with no handle is shown
 * too, and marked: that is the step a plan would not be able to run.
 */
function callsBlock(book) {
  const withHandle = book.primitives.filter((p) => p.interface);
  if (!withHandle.length) return null;

  const wrap = el(`<div>
    <div class="md-head"><span class="t">how each step is invoked —
      ${withHandle.length} of ${book.primitives.length} steps</span></div>
    <div class="calls"></div>
  </div>`);
  const box = wrap.querySelector(".calls");

  for (const primitive of book.primitives) {
    const handle = primitive.interface;
    box.appendChild(
      el(`<div class="call">
        <span class="kind">${esc(handle ? handle.kind : "—")}</span>
        <span>
          <span class="step">${esc(primitive.name)}</span><br />
          ${
            handle
              ? `<span class="handle">${esc(handle.name)}</span>` +
                (handle.type ? ` <span class="type">${esc(handle.type)}</span>` : "")
              : `<span class="none">no interface — a plan cannot run this step</span>`
          }
        </span>
      </div>`)
    );
  }
  return wrap;
}

function statsBlock(book, validation, result) {
  const requires = book.primitives.reduce((n, p) => n + p.requires.length, 0);
  const produces = book.primitives.reduce((n, p) => n + p.produces.length, 0);

  /* The gloss on `requires` is the difference between the two sources, and it
     inverts: narration almost never states a precondition, documentation mostly
     does. Saying "inferred" over a manual would misreport exactly the thing the
     gate exists to be careful about. */
  const manual = result && result.source_kind === "manual";
  const note = manual ? "mostly stated" : "mostly inferred";

  return el(`<div class="stats">
    <div class="stat hero"><div class="v">${book.primitives.length}</div><div class="k">primitives</div></div>
    <div class="stat"><div class="v">${book.states.length}</div><div class="k">states</div></div>
    <div class="stat"><div class="v">${book.objects.length}</div><div class="k">objects</div></div>
    <div class="stat"><div class="v">${requires}</div><div class="k">requires</div>
      <div class="note">${note}</div></div>
    <div class="stat"><div class="v">${produces}</div><div class="k">produces</div></div>
    <div class="stat"><div class="v">${validation.ordering_constraints ?? 0}</div><div class="k">orderings</div>
      <div class="note">derived</div></div>
  </div>`);
}

function issuesBlock(validation, result) {
  const issues = validation.issues || [];
  if (!issues.length) {
    return el(
      `<div class="clean">No issues: a DAG, no orphan preconditions, a producible goal, and a valid topological order.</div>`
    );
  }
  const box = el(`<div class="issues"></div>`);
  for (const issue of issues) {
    box.appendChild(
      el(`<div class="issue ${issue.fatal ? "fatal" : "warn"}">
        <div class="lvl">${issue.fatal ? "fatal" : "flag"}</div>
        <div><code>${esc(issue.code)}</code>
          <div class="detail">${esc(issue.detail)}</div></div>
      </div>`)
    );
  }
  if (result.verdict === "reject" && result.reason) {
    box.appendChild(
      el(`<div class="issue fatal"><div class="lvl">why</div><div class="detail">${esc(result.reason)}</div></div>`)
    );
  }
  return box;
}

function planBlock(plan) {
  const wrap = el(`<div>
    <div class="md-head"><span class="t">the plan this rulebook implies — ${plan.length} steps</span></div>
    <ol class="steps"></ol>
  </div>`);
  const list = wrap.querySelector("ol");
  plan.forEach((action, index) => {
    list.appendChild(
      el(`<li><div class="num">${index + 1}</div>
          <div class="desc"><div class="action">${esc(action)}</div></div></li>`)
    );
  });
  return wrap;
}

function markdownBlock(result) {
  const wrap = el(`<div>
    <div class="md-head">
      <span class="t">rulebook markdown</span>
      <span class="acts">
        <button class="mini" type="button" data-act="copy">copy</button>
        <a class="mini" data-act="download">download</a>
      </span>
    </div>
    <pre class="md"></pre>
  </div>`);

  wrap.querySelector("pre").innerHTML = highlight(result.markdown);

  const copy = wrap.querySelector('[data-act="copy"]');
  copy.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(result.markdown);
      copy.textContent = "copied";
      setTimeout(() => (copy.textContent = "copy"), 1400);
    } catch (error) {
      copy.textContent = "copy failed";
    }
  });

  /* Downloaded from a blob rather than written server-side: the page should not
     need a write endpoint to hand you a file. */
  const link = wrap.querySelector('[data-act="download"]');
  link.href = URL.createObjectURL(new Blob([result.markdown], { type: "text/markdown" }));
  link.download = result.filename || "rulebook.md";
  return wrap;
}

/* Just enough to make the structure legible: headings and the bold names. */
function highlight(markdown) {
  return esc(markdown)
    .replace(/^(#{1,3} .*)$/gm, '<span class="h">$1</span>')
    .replace(/\*\*(.+?)\*\*/g, '<span class="b">**$1**</span>');
}
