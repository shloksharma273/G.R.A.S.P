/* The generator page: start a job, poll it, render the verdict.
 *
 * Generation is slow — captions, then a minute or two of reconstruction — so the
 * POST only starts the work and this polls for the stage. Nothing here judges the
 * result; the verdict, the issues and the implied plan all arrive already decided
 * by the validation gate.
 */

const $ = (id) => document.getElementById(id);

const form = $("form");
const urlInput = $("url");
const transcript = $("transcript");
const go = $("go");
const stream = $("stream");

let busy = false;

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

// -------------------------------------------------------------- boot --------

async function boot() {
  try {
    const health = await (await fetch("/api/generate/health")).json();
    renderPills(health);
    if (!health.available) {
      go.disabled = true;
      stream.appendChild(
        el(`<div class="card gen-reject"><div class="card-body">
          <p>${esc(health.reason || "the generator is not configured")}</p>
          ${health.hint ? `<p style="color:var(--text-faint)">${esc(health.hint)}</p>` : ""}
        </div></div>`)
      );
    } else if (!health.youtube) {
      // Fetching captions is the only part that needs the optional dependency.
      urlInput.placeholder = "youtube-transcript-api not installed — paste a transcript below";
      showPaste(true);
    }
  } catch (error) {
    $("pills").innerHTML = `<span class="pill warn"><b>offline</b></span>`;
  }

  try {
    const { rulebooks } = await (await fetch("/api/generate/library")).json();
    renderLibrary(rulebooks);
  } catch (error) {
    /* the library is a convenience; its absence is not worth reporting */
  }
}

function renderPills(health) {
  if (!health.available) {
    $("pills").innerHTML = `<span class="pill warn">generator <b>unavailable</b></span>`;
    return;
  }
  $("pills").innerHTML = [
    `<span class="pill">model <b>${esc(health.model)}</b></span>`,
    `<span class="pill" title="Which verdicts are written to ${esc(health.output_dir)}. Only an accept is ever ingested, whatever this says.">strictness <b>${esc(health.strictness)}</b></span>`,
    `<span class="pill${health.youtube ? "" : " warn"}" title="${
      health.youtube
        ? "Captions can be fetched from a link."
        : "youtube-transcript-api is not installed, so paste a transcript instead. It is an optional dependency."
    }">captions <b>${health.youtube ? "live" : "paste only"}</b></span>`,
  ].join("");
}

function renderLibrary(rulebooks) {
  if (!rulebooks || !rulebooks.length) return;
  $("library-wrap").hidden = false;
  const box = $("library");
  box.innerHTML = "";
  for (const book of rulebooks) {
    const chip = el(
      `<button class="chip" type="button">${esc(readable(book.name))}<span class="n">${book.steps} steps</span></button>`
    );
    chip.addEventListener("click", () => showSaved(book));
    box.appendChild(chip);
  }
}

// ------------------------------------------------------------ the paste -----

function showPaste(open) {
  transcript.hidden = !open;
  $("toggle-paste").textContent = open
    ? "▾ no captions? paste a transcript instead"
    : "▸ no captions? paste a transcript instead";
}
$("toggle-paste").addEventListener("click", () => showPaste(transcript.hidden));

// -------------------------------------------------------------- running -----

form.addEventListener("submit", (event) => {
  event.preventDefault();
  run();
});

async function run() {
  if (busy) return;
  const url = urlInput.value.trim();
  const pasted = transcript.value.trim();
  if (!url && !pasted) {
    urlInput.focus();
    return;
  }

  busy = true;
  go.disabled = true;
  $("welcome").querySelector("h1").scrollIntoView({ behavior: "smooth", block: "start" });

  const started = Date.now();
  const turn = el(`<div class="turn">
    <div class="asked"><span class="caret">&gt;</span><span class="text">${esc(url || "pasted transcript")}</span></div>
    <div class="card"><div class="stage">
      <span class="spin"></span><span class="what">starting</span>
      <span class="elapsed">0s</span>
    </div></div>
  </div>`);
  stream.appendChild(turn);

  const what = turn.querySelector(".what");
  const elapsed = turn.querySelector(".elapsed");
  const ticker = setInterval(() => {
    elapsed.textContent = `${Math.round((Date.now() - started) / 1000)}s`;
  }, 1000);

  try {
    const response = await fetch("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, transcript: pasted }),
    });
    const job = await response.json();
    if (job.error) throw new Error(job.error + (job.hint ? ` — ${job.hint}` : ""));

    const done = await poll(job.id, (stage) => (what.textContent = stage));
    clearInterval(ticker);

    if (done.state === "error") {
      turn.querySelector(".card").replaceWith(errorCard(done.error));
    } else {
      turn.querySelector(".card").replaceWith(resultCard(done.result));
    }
  } catch (error) {
    clearInterval(ticker);
    turn.querySelector(".card").replaceWith(errorCard(String(error.message || error)));
  }

  busy = false;
  go.disabled = false;
}

async function poll(id, onStage) {
  /* Generation runs for a minute or two. Poll gently — this is a single-user
     dev server and a tight loop would buy nothing. */
  let lastStage = "";
  for (let attempt = 0; attempt < 600; attempt++) {
    const job = await (await fetch(`/api/generate/${id}`)).json();
    if (job.stage && job.stage !== lastStage) {
      lastStage = job.stage;
      onStage(job.stage);
    }
    if (job.state !== "running") return job;
    await sleep(1500);
  }
  return { state: "error", error: "timed out waiting for the generation to finish" };
}

// ------------------------------------------------------------ rendering -----

function errorCard(message) {
  return el(
    `<div class="card error"><div class="card-body">${esc(message)}</div></div>`
  );
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
      el(`<span class="pill" title="Same transcript as a previous run, so no model call was made.">cached</span>`)
    );
  }

  if (book) card.appendChild(statsBlock(book, validation));
  card.appendChild(issuesBlock(validation, result));
  if (validation.plan && validation.plan.length) card.appendChild(planBlock(validation.plan));
  if (result.markdown) card.appendChild(markdownBlock(result));

  return card;
}

function statsBlock(book, validation) {
  const requires = book.primitives.reduce((n, p) => n + p.requires.length, 0);
  const produces = book.primitives.reduce((n, p) => n + p.produces.length, 0);

  return el(`<div class="stats">
    <div class="stat hero"><div class="v">${book.primitives.length}</div><div class="k">primitives</div></div>
    <div class="stat"><div class="v">${book.states.length}</div><div class="k">states</div></div>
    <div class="stat"><div class="v">${book.objects.length}</div><div class="k">objects</div></div>
    <div class="stat"><div class="v">${requires}</div><div class="k">requires</div>
      <div class="note">mostly inferred</div></div>
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

function showSaved(book) {
  const turn = el(`<div class="turn">
    <div class="asked"><span class="caret">&gt;</span><span class="text">${esc(book.filename)}</span></div>
  </div>`);
  turn.appendChild(
    markdownBlock({ markdown: book.markdown, filename: book.filename })
  );
  stream.appendChild(turn);
  turn.scrollIntoView({ behavior: "smooth", block: "start" });
}

boot();
urlInput.focus();
