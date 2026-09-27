/* The video generator page: start a job, poll it, render the verdict.
 *
 * Generation is slow — captions, then a minute or two of reconstruction — so the
 * POST only starts the work and this polls for the stage. The result rendering is
 * shared with the repository page and lives in rulebook.js.
 */

const form = $("form");
const urlInput = $("url");
const transcript = $("transcript");
const go = $("go");
const stream = $("stream");

let busy = false;

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

  const running = runningCard();
  const turn = el(`<div class="turn">
    <div class="asked"><span class="caret">&gt;</span><span class="text">${esc(url || "pasted transcript")}</span></div>
  </div>`);
  turn.appendChild(running.card);
  stream.appendChild(turn);

  try {
    const response = await fetch("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, transcript: pasted }),
    });
    const job = await response.json();
    if (job.error) throw new Error(job.error + (job.hint ? ` — ${job.hint}` : ""));

    const done = await pollJob(job.id, (stage) => running.stage(stage));
    running.stop();
    running.card.replaceWith(
      done.state === "error" ? errorCard(done.error) : resultCard(done.result)
    );
  } catch (error) {
    running.stop();
    running.card.replaceWith(errorCard(String(error.message || error)));
  }

  busy = false;
  go.disabled = false;
}

function showSaved(book) {
  const turn = el(`<div class="turn">
    <div class="asked"><span class="caret">&gt;</span><span class="text">${esc(book.filename)}</span></div>
  </div>`);
  turn.appendChild(markdownBlock({ markdown: book.markdown, filename: book.filename }));
  stream.appendChild(turn);
  turn.scrollIntoView({ behavior: "smooth", block: "start" });
}

boot();
urlInput.focus();
