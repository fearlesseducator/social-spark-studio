/**
 * pipeline.js — Social Spark Studio
 *
 * Shared fetch + polling utilities for all pipeline action pages.
 * Included by youtube.html, moments.html, posts.html, export.html.
 *
 * Public API:
 *   pipelinePost(endpoint, body)          — POST, return JSON
 *   pipelineJob(endpoint, body, hooks)    — POST → poll until done/failed
 *   pipeStatus(id, html)                  — show a status message
 *   pipeClear(id)                         — hide + clear a status element
 *   pipeShow(id) / pipeHide(id)           — show/hide any element
 *   pipeBtn(id, disabled, label)          — toggle button state + optional label
 *   escHtml(str)                          — XSS-safe string
 */

// ── Core fetch ────────────────────────────────────────────────────────

/**
 * POST to a pipeline endpoint and return parsed JSON.
 * Works for both synchronous endpoints (transcript, export) and
 * job-start endpoints (moments, captions, images).
 */
async function pipelinePost(endpoint, body = null) {
  const opts = { method: 'POST' };
  if (body !== null) {
    opts.headers = { 'Content-Type': 'application/json' };
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(endpoint, opts);
  try {
    return await res.json();
  } catch {
    return { success: false, detail: `HTTP ${res.status} — ${res.statusText}` };
  }
}

// ── Background job polling ────────────────────────────────────────────

/**
 * Start a background job and poll every 3 s until done or failed.
 *
 * hooks:
 *   onStart(data)   — called after successful job start
 *   onRunning(data) — called each poll tick while pending/running
 *   onDone(data)    — called once when status === "done"
 *   onFailed(msg)   — called with a plain-text error string
 */
async function pipelineJob(endpoint, body, hooks = {}) {
  const { onStart, onRunning, onDone, onFailed } = hooks;
  let timerId = null;

  const fail = (msg) => {
    if (timerId) clearInterval(timerId);
    console.error('[pipeline]', endpoint, '—', msg);
    onFailed && onFailed(msg);
  };

  // ── Start the job ───────────────────────────────────────────────
  let startData;
  try {
    startData = await pipelinePost(endpoint, body);
  } catch (err) {
    return fail('Network error starting job: ' + err.message);
  }

  // FastAPI HTTPException comes back as { detail: "..." } without success
  if (!startData.success) {
    return fail(startData.detail || startData.error_message || 'Job failed to start.');
  }
  if (!startData.poll_url) {
    return fail('Job started but no poll_url was returned.');
  }

  console.log('[pipeline] job started:', startData.job_id, '→ polling', startData.poll_url);
  onStart && onStart(startData);

  // ── Poll ────────────────────────────────────────────────────────
  timerId = setInterval(async () => {
    let data;
    try {
      const r = await fetch(startData.poll_url);
      data = await r.json();
    } catch (err) {
      return fail('Network error while polling: ' + err.message);
    }

    console.log('[pipeline] poll →', data.status, data.phase || '');

    if (data.status === 'done') {
      clearInterval(timerId);
      onDone && onDone(data);
    } else if (data.status === 'failed') {
      clearInterval(timerId);
      fail(data.error || 'Job failed (no details returned).');
    } else {
      // pending or running
      onRunning && onRunning(data);
    }
  }, 3000);
}

// ── UI helpers ─────────────────────────────────────────────────────────

/** Reveal an element and set its inner HTML. */
function pipeStatus(id, html) {
  const el = document.getElementById(id);
  if (!el) return;
  el.innerHTML = html;
  el.classList.remove('hidden');
}

/** Hide and clear a status element. */
function pipeClear(id) {
  const el = document.getElementById(id);
  if (!el) return;
  el.classList.add('hidden');
  el.innerHTML = '';
}

function pipeShow(id) { document.getElementById(id)?.classList.remove('hidden'); }
function pipeHide(id) { document.getElementById(id)?.classList.add('hidden'); }

/**
 * Enable/disable a button and optionally change its visible label.
 * label targets the first <span> inside the button if present, else
 * the button's own textContent.
 */
function pipeBtn(id, disabled, label = null) {
  const el = document.getElementById(id);
  if (!el) return;
  el.disabled = disabled;
  if (label === null) return;
  const span = el.querySelector('span');
  if (span) span.textContent = label;
  else if (!el.querySelector('svg, i')) el.textContent = label;
}

/** Escape a string for safe HTML insertion. */
function escHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/** Build the standard "success" HTML snippet. */
function pipeSuccessHtml(msg) {
  return `<span class="inline-flex items-center gap-1.5 text-success-foreground font-medium">
    <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4 shrink-0" viewBox="0 0 24 24" fill="none"
         stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <path d="M20 6 9 17l-5-5"/>
    </svg>${escHtml(msg)}</span>`;
}

/** Build the standard "error" HTML snippet. */
function pipeErrorHtml(msg) {
  return `<span class="inline-flex items-center gap-1.5 text-destructive font-medium">
    <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4 shrink-0" viewBox="0 0 24 24" fill="none"
         stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/>
      <path d="M12 9v4"/><path d="M12 17h.01"/>
    </svg>${escHtml(msg)}</span>`;
}

/** Build the standard "running" HTML snippet with animated dots. */
function pipeRunningHtml(msg) {
  return `<span class="text-muted-foreground">${escHtml(msg)}</span>
    <span class="inline-block animate-pulse ml-1">…</span>`;
}
