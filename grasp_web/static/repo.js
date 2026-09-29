/* The repository page: browse a docs repo, pick the pages, generate.
 *
 * The extra step over the video page is the picking, and it is the point. A guide
 * holds hundreds of pages and a rulebook describes one procedure, so the page
 * lists what the repo has, ranked by how procedure-shaped it looks, and a person
 * chooses. The ranking is a hint and is labelled as one — it reorders the list, it
 * never ticks anything.
 *
 * Result rendering is shared with the video page and lives in rulebook.js.
 */

const form = $("form");
const repoInput = $("repo");
const browse = $("browse");
const go = $("go");
const filter = $("filter");
const stream = $("stream");

const EXAMPLES = [
  { label: "PX4 user guide", url: "PX4/PX4-user_guide/tree/main/en", mode: "docs" },
  { label: "ArduPilot wiki", url: "ArduPilot/ardupilot_wiki", mode: "docs" },
  { label: "UR ROS 2 driver", url: "UniversalRobots/Universal_Robots_ROS2_Driver", mode: "code" },
  { label: "ROS 2 docs", url: "ros2/ros2_documentation/tree/rolling/source", mode: "docs" },
];

/* Which register to read the repo in. It changes three things at once: which
   files are listed, how they are ranked, and which extraction prompt runs. */
const mode = () => document.querySelector('input[name="mode"]:checked').value;

//: What the server last listed, and what is ticked in it.
let listing = null;
let chosen = new Set();
let busy = false;

// -------------------------------------------------------------- boot --------

async function boot() {
  const box = $("examples");
  for (const example of EXAMPLES) {
    const chip = el(`<button class="chip" type="button">${esc(example.label)}</button>`);
    chip.addEventListener("click", () => {
      repoInput.value = example.url;
      const radio = document.querySelector(`input[name="mode"][value="${example.mode}"]`);
      if (radio) {
        radio.checked = true;
        syncModes();
      }
      loadTree();
    });
    box.appendChild(chip);
  }

  try {
    const health = await (await fetch("/api/generate/health")).json();
    renderPills(health);
    if (!health.available) {
      browse.disabled = true;
      stream.appendChild(
        el(`<div class="card gen-reject"><div class="card-body">
          <p>${esc(health.reason || "the generator is not configured")}</p>
          ${health.hint ? `<p style="color:var(--text-faint)">${esc(health.hint)}</p>` : ""}
        </div></div>`)
      );
    }
  } catch (error) {
    $("pills").innerHTML = `<span class="pill warn"><b>offline</b></span>`;
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
    `<span class="pill" title="Preconditions are read off the documentation rather than inferred from narration.">source <b>documentation</b></span>`,
  ].join("");
}

// ------------------------------------------------------------- browsing -----

form.addEventListener("submit", (event) => {
  event.preventDefault();
  loadTree();
});
browse.addEventListener("click", () => loadTree());
repoInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    loadTree();
  }
});

async function loadTree() {
  const url = repoInput.value.trim();
  if (!url || busy) return;

  busy = true;
  browse.disabled = true;
  browse.textContent = "reading…";

  try {
    const response = await fetch("/api/repo/tree", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, mode: mode() }),
    });
    const payload = await response.json();
    if (payload.error) throw new Error(payload.error + (payload.hint ? ` — ${payload.hint}` : ""));

    listing = payload;
    chosen = new Set();
    renderPicker();
  } catch (error) {
    $("picker").hidden = true;
    const turn = el(
      `<div class="turn"><div class="asked"><span class="caret">&gt;</span><span class="text">${esc(url)}</span></div></div>`
    );
    turn.appendChild(errorCard(String(error.message || error)));
    stream.appendChild(turn);
  }

  busy = false;
  browse.disabled = false;
  browse.textContent = "Browse";
}

function renderPicker() {
  $("picker").hidden = false;
  $("picker-title").textContent = `${listing.repo} @ ${listing.ref}`;

  const notes = [
    `${listing.files.length} documentation page(s), most procedure-shaped first.`,
  ];
  if (listing.truncated) {
    notes.push(
      `The list is capped, so it may not be everything — point at a subdirectory (…/tree/${listing.ref}/docs) to narrow it.`
    );
  }
  notes.push(
    listing.mode === "code"
      ? `Pick up to ${listing.max_selected}: the files that declare the calls for one task.`
      : `Pick up to ${listing.max_selected}: a rulebook describes one procedure.`
  );
  $("picker-note").textContent = notes.join(" ");

  filter.value = "";
  renderFiles("");
  updatePicked();
  filter.focus();
}

filter.addEventListener("input", () => renderFiles(filter.value.trim().toLowerCase()));

/* Re-reading is the only honest response to a mode change: the two modes list
   different files, so the current selection may not even exist in the other. */
function syncModes() {
  for (const label of document.querySelectorAll(".mode")) {
    label.classList.toggle("on", label.querySelector("input").checked);
  }
}
for (const radio of document.querySelectorAll('input[name="mode"]')) {
  radio.addEventListener("change", () => {
    syncModes();
    if (repoInput.value.trim()) loadTree();
  });
}

function renderFiles(needle) {
  const box = $("files");
  box.innerHTML = "";
  const rows = listing.files.filter((f) => !needle || f.path.toLowerCase().includes(needle));

  if (!rows.length) {
    box.appendChild(el(`<div class="empty">no page matches “${esc(needle)}”</div>`));
    return;
  }

  for (const file of rows.slice(0, 250)) {
    const id = `f-${file.path.replace(/[^a-z0-9]/gi, "-")}`;
    const row = el(`<label class="file" for="${id}">
      <input type="checkbox" id="${id}" />
      <span class="p"><span class="d">${esc(file.dir)}/</span>${esc(file.name)}</span>
      <span class="s">${Math.max(1, Math.round(file.size / 1024))}k</span>
    </label>`);

    const tick = row.querySelector("input");
    tick.checked = chosen.has(file.path);
    row.classList.toggle("on", tick.checked);
    tick.addEventListener("change", () => {
      if (tick.checked) chosen.add(file.path);
      else chosen.delete(file.path);
      row.classList.toggle("on", tick.checked);
      updatePicked();
    });

    if (file.score > 0) {
      row.querySelector(".s").before(
        el(`<span class="likely" title="${
          listing.mode === "code"
            ? "This path looks like it declares an interface — a ranking hint only, not a judgement about the file."
            : "This path reads like a procedure — a ranking hint only, not a judgement about the page."
        }">likely</span>`)
      );
    }
    box.appendChild(row);
  }

  if (rows.length > 250) {
    box.appendChild(
      el(`<div class="empty">${rows.length - 250} more — narrow the filter to see them</div>`)
    );
  }
}

function updatePicked() {
  const n = chosen.size;
  const limit = listing ? listing.max_selected : 12;
  $("picked").textContent = `${n} chosen`;
  $("picked").classList.toggle("over", n > limit);
  go.disabled = n === 0 || n > limit || busy;
}

// -------------------------------------------------------------- running -----

go.addEventListener("click", () => run());

async function run() {
  if (busy || !chosen.size || !listing) return;

  busy = true;
  go.disabled = true;

  const paths = [...chosen];
  const running = runningCard();
  const turn = el(`<div class="turn">
    <div class="asked"><span class="caret">&gt;</span>
      <span class="text">${esc(listing.repo)} — ${paths.length} page(s)</span></div>
    <div class="chosen-paths">${paths.map((p) => `<code>${esc(p)}</code>`).join("")}</div>
  </div>`);
  turn.appendChild(running.card);
  stream.appendChild(turn);
  turn.scrollIntoView({ behavior: "smooth", block: "start" });

  try {
    const response = await fetch("/api/repo/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url: listing.url, ref: listing.ref, paths, mode: listing.mode, split: $("split").checked,
      }),
    });
    const job = await response.json();
    if (job.error) throw new Error(job.error + (job.hint ? ` — ${job.hint}` : ""));

    const done = await pollJob(job.id, (stage) => running.stage(stage));
    running.stop();

    if (done.state === "error") {
      running.card.replaceWith(errorCard(done.error));
    } else {
      running.card.replaceWith(finishedCard(done));
      turn.appendChild(coverageNotes(done.detail || {}));
    }
  } catch (error) {
    running.stop();
    running.card.replaceWith(errorCard(String(error.message || error)));
  }

  busy = false;
  updatePicked();
}

/* A finished generation: a split one as its task list, a single one as its gate
   result - either way followed by the panel that builds it into a PlanGraph. */
function finishedCard(done) {
  if (done.result && done.result.kind === "split") return splitResultCard(done.result, done.id);
  const box = el(`<div class="split"></div>`);
  box.appendChild(resultCard(done.result));
  const panel = singleBuildPanel(done.result, done.id);
  if (panel) box.appendChild(panel);
  return box;
}

/* What became of the selection.
 *
 * Neither of these is a defect in the rulebook, so neither is a gate issue — but
 * both are things the person who ticked the boxes has to be told. A rulebook that
 * covers one of the four pages you chose is a fine rulebook and the wrong answer
 * to what you asked for, and the page should say so rather than report "4 pages"
 * over a single procedure's worth of steps.
 */
function coverageNotes(detail) {
  const box = el(`<div class="issues"></div>`);
  const paths = (list) => list.map((p) => `<code>${esc(p)}</code>`).join(", ");

  if ((detail.skipped || []).length) {
    box.appendChild(
      el(`<div class="issue warn"><div class="lvl">note</div>
        <div class="detail">not read — the document had already reached its size limit:
          ${paths(detail.skipped)}</div></div>`)
    );
  }
  if ((detail.unused || []).length) {
    box.appendChild(
      el(`<div class="issue warn"><div class="lvl">note</div>
        <div class="detail">no step was drawn from ${paths(detail.unused)} — the rulebook
          covers a narrower procedure than the pages you chose. Generate those pages
          separately for a rulebook of their own.</div></div>`)
    );
  }
  return box;
}

boot();
repoInput.focus();
