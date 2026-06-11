/**
 * voice.js — Social Spark Studio · Voice Conversation Studio
 *
 * State machine:
 *   idle       → user has not started interview
 *   ready      → interview started, waiting for user input
 *   recording  → MediaRecorder is capturing audio
 *   processing → audio/text sent to backend, waiting for response
 *
 * Recording button states (4):
 *   state-start      "Start Recording"
 *   state-listening  "Listening… Click to Stop"
 *   state-processing "Processing…"
 *   state-again      "Record Another Answer"
 */

// ── State ────────────────────────────────────────────────────────────

const STATE = { IDLE: 'idle', READY: 'ready', RECORDING: 'recording', PROCESSING: 'processing' };

let appState      = STATE.IDLE;
let sessionId     = null;
let mediaRecorder = null;
let audioChunks   = [];
let voiceAvailable = false;

// ── Init ─────────────────────────────────────────────────────────────

function initVoiceStudio() {
  console.log('[voice] initVoiceStudio called');

  // Read voice availability from the server-rendered status
  if (window.VOICE_STATUS) {
    console.log('[voice] VOICE_STATUS from server:', window.VOICE_STATUS);
    voiceAvailable = window.VOICE_STATUS.voice_ready === true;
    console.log('[voice] voiceAvailable set to:', voiceAvailable);
    console.log('[voice] voice_ready:', voiceAvailable,
      '| stt:', window.VOICE_STATUS.stt_available,
      '| tts:', window.VOICE_STATUS.tts_available);
  }

  if (!voiceAvailable) {
    showElement('text-only-banner');
    setRecBtn('state-start', 'mic-off', 'Microphone unavailable');
    document.getElementById('rec-btn').disabled = true;
    document.getElementById('rec-btn').style.opacity = '0.45';
    document.getElementById('rec-btn').style.cursor = 'not-allowed';
    setHint('Voice not configured. Use the text box below to answer each question.');
    console.log('[voice] Text-only mode active (GOOGLE_CLOUD_PROJECT not set)');
  }

  // Restore session from localStorage
  const saved = localStorage.getItem('sss_voice_session_id');
  if (saved) {
    sessionId = saved;
    setStatus('Previous session found. Click Start Interview to resume.');
    console.log('[voice] Restored session_id from localStorage:', sessionId);
  }
}

// ── Start interview ───────────────────────────────────────────────────

async function startInterview() {
  console.log('[voice] startInterview() called, session_id:', sessionId);

  const startBtn = document.getElementById('start-btn');
  startBtn.disabled = true;
  setLoading(true);
  setStatus('Connecting to Spark…');
  clearError();

  try {
    const body = JSON.stringify({ session_id: sessionId || null });
    console.log('[voice] POST /voice/start →', body);

    const res  = await fetch('/voice/start', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body,
    });
    const data = await res.json();
    console.log('[voice] /voice/start response:', data);

    if (!data.success) {
      showError('Could not start interview: ' + (data.error_message || 'Unknown error'));
      startBtn.disabled = false;
      setLoading(false);
      return;
    }

    sessionId = data.session_id;
    localStorage.setItem('sss_voice_session_id', sessionId);
    console.log('[voice] Session ID set:', sessionId);

    appState = STATE.READY;
    hideElement('start-area');   // hide the Start Interview button row
    handleTurnResponse(data, /* isStart= */ true);

  } catch (err) {
    showError('Network error starting interview: ' + err.message);
    startBtn.disabled = false;
    console.error('[voice] startInterview error:', err);
  }

  setLoading(false);
}

// ── Recording button click ────────────────────────────────────────────

function handleRecordClick() {
  console.log('[voice] handleRecordClick fired | voiceAvailable:', voiceAvailable, '| appState:', appState);
  if (!voiceAvailable) return;

  if (appState === STATE.IDLE) {
    setStatus('Click Start Interview first.');
    return;
  }
  if (appState === STATE.RECORDING) {
    console.log('[voice] handleRecordClick → stopping recording');
    stopRecording();
  } else if (appState === STATE.READY) {
    console.log('[voice] handleRecordClick → starting recording');
    startRecording();
  }
  // STATE.PROCESSING: button is disabled, click does nothing
}

// ── MediaRecorder start ───────────────────────────────────────────────

async function startRecording() {
  console.log('[voice] startRecording() called');
  clearError();

  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    audioChunks  = [];

    // Prefer webm/opus (all modern browsers); fall back gracefully
    const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
      ? 'audio/webm;codecs=opus'
      : '';
    mediaRecorder = mimeType
      ? new MediaRecorder(stream, { mimeType })
      : new MediaRecorder(stream);

    mediaRecorder.ondataavailable = (e) => {
      console.log('[voice] ondataavailable fired — chunk size:', e.data ? e.data.size : 0, 'bytes');
      if (e.data && e.data.size > 0) {
        audioChunks.push(e.data);
      }
    };

    mediaRecorder.onstop = () => {
      console.log('[voice] onstop fired — total chunks collected:', audioChunks.length);
      stream.getTracks().forEach(t => t.stop());
      try {
        onRecordingStopped();
      } catch (err) {
        console.error('[voice] ERROR inside onRecordingStopped:', err);
        showError('Recording processing error: ' + err.message);
        resetToReady();
      }
    };

    mediaRecorder.onerror = (e) => {
      console.error('[voice] MediaRecorder error event:', e.error || e);
    };

    mediaRecorder.start(250);  // collect chunks every 250 ms
    appState = STATE.RECORDING;

    setRecBtn('state-listening', 'mic-off', 'Listening… Click to Stop');
    setHint('Speaking… Click the button again when you are done.');
    setMicIndicator('recording');
    setStatus('Listening…');
    console.log('[voice] Recording started, mimeType:', mediaRecorder.mimeType);

  } catch (err) {
    showError('Microphone error: ' + err.message + '. Try typing your answer below.');
    console.error('[voice] startRecording error:', err);
  }
}

// ── MediaRecorder stop ────────────────────────────────────────────────

function stopRecording() {
  console.log('[voice] stopRecording() called — recorder state:',
    mediaRecorder ? mediaRecorder.state : '(no mediaRecorder)');
  if (mediaRecorder && mediaRecorder.state !== 'inactive') {
    mediaRecorder.stop();
    console.log('[voice] mediaRecorder.stop() called — waiting for onstop');
  } else {
    // SILENT PATH: stop() skipped → onstop never fires → no blob, no fetch
    console.warn('[voice] mediaRecorder.stop() SKIPPED — recorder is',
      mediaRecorder ? 'already inactive' : 'null',
      '— onstop will NOT fire, no audio will be sent');
  }
  appState = STATE.PROCESSING;
  setRecBtn('state-processing', 'loader', 'Processing…');
  setHint('Sending your answer to Spark…');
  setMicIndicator('processing');
  setStatus('Processing your answer…');
}

function onRecordingStopped() {
  const blob = new Blob(audioChunks, { type: mediaRecorder.mimeType || 'audio/webm' });
  console.log('[voice] Audio blob created — size:', blob.size, 'bytes, type:', blob.type,
    '— from', audioChunks.length, 'chunks');

  if (blob.size < 1000) {
    // SILENT PATH: blob too small → no fetch is made
    showError('No audio was captured. Please try again, or type your answer below.');
    resetToReady();
    console.warn('[voice] Audio blob too small:', blob.size,
      'bytes (< 1000 threshold) — fetch SKIPPED, no request will appear in Network tab');
    return;
  }

  console.log('[voice] Blob OK — calling sendAudioTurn()');
  sendAudioTurn(blob);
}

// ── Send audio turn ────────────────────────────────────────────────────

async function sendAudioTurn(audioBlob) {
  const formData = new FormData();
  formData.append('session_id', sessionId);
  formData.append('audio', audioBlob, 'recording.webm');
  formData.append('sample_rate', '16000');
  formData.append('encoding', 'WEBM_OPUS');

  console.log('[voice] About to fetch POST /voice/turn/audio — blob size:', audioBlob.size,
    '| session_id:', sessionId);

  try {
    const res  = await fetch('/voice/turn/audio', { method: 'POST', body: formData });
    console.log('[voice] fetch returned — HTTP status:', res.status);
    const data = await res.json();
    console.log('[voice] /voice/turn/audio response received:', {
      success:      data.success,
      transcript:   data.transcript?.slice(0, 80),
      agent_text:   data.agent_text?.slice(0, 80),
      audio_avail:  data.audio_available,
      block:        data.current_block,
      confirmed:    data.blocks_confirmed,
      awaiting:     data.awaiting_confirmation,
      complete:     data.interview_complete,
    });

    if (!data.success) {
      const msg = data.error_message || 'Unknown error';
      if (msg.toLowerCase().includes('transcri') || msg.toLowerCase().includes('speech')) {
        showError('Voice transcription failed. You can continue by typing your answer below.');
      } else {
        showError('Error: ' + msg);
      }
      resetToReady();
      return;
    }

    handleTurnResponse(data);

  } catch (err) {
    showError('Network error sending audio: ' + err.message);
    resetToReady();
    console.error('[voice] sendAudioTurn error:', err);
  }
}

// ── Send text turn ─────────────────────────────────────────────────────

async function submitText(event) {
  event.preventDefault();
  clearError();

  if (appState === STATE.IDLE) {
    setStatus('Click Start Interview first.');
    return;
  }
  if (appState === STATE.PROCESSING) return;

  const input = document.getElementById('text-input');
  const text  = input.value.trim();
  if (!text) return;

  input.value = '';
  appState    = STATE.PROCESSING;
  setRecBtn('state-processing', 'loader', 'Processing…');
  setLoading(true);
  setStatus('Sending your answer…');
  console.log('[voice] POST /voice/turn/text — text:', text.slice(0, 80));

  try {
    const res  = await fetch('/voice/turn/text', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ session_id: sessionId, text }),
    });
    const data = await res.json();
    console.log('[voice] /voice/turn/text response received:', {
      success:    data.success,
      agent_text: data.agent_text?.slice(0, 80),
      block:      data.current_block,
      confirmed:  data.blocks_confirmed,
      awaiting:   data.awaiting_confirmation,
    });

    if (!data.success) {
      showError('Error: ' + (data.error_message || 'Unknown error'));
      resetToReady();
      setLoading(false);
      return;
    }

    handleTurnResponse(data);

  } catch (err) {
    showError('Network error: ' + err.message);
    resetToReady();
    console.error('[voice] submitText error:', err);
  }

  setLoading(false);
}

// ── Handle turn response ───────────────────────────────────────────────

function handleTurnResponse(data, isStart = false) {
  clearError();

  // ── 4. Founder transcript ─────────────────────────
  if (data.transcript) {
    console.log('[voice] Transcript received:', data.transcript.slice(0, 100));
    showFounderTranscript(data.transcript);
    addHistoryItem('founder', data.transcript);
  }

  // ── 5. Agent response ─────────────────────────────
  if (data.agent_text) {
    console.log('[voice] Agent response received:', data.agent_text.slice(0, 100));
    showAgentQuestion(data.agent_text);
    if (!isStart) {
      addHistoryItem('agent', data.agent_text);
    }
  }

  // ── 6. Audio playback ─────────────────────────────
  if (data.audio_available && data.audio_base64) {
    console.log('[voice] Audio response received — playing');
    playAudio(data.audio_base64);
  } else {
    console.log('[voice] No audio response (audio_available:', data.audio_available, ')');
    hideElement('audio-player');
  }

  // ── Block progress update ──────────────────────────
  updateBlockProgress(
    data.blocks_confirmed  || 0,
    data.current_block     || 1,
    data.current_block_name || '',
  );

  // ── Confirm button ─────────────────────────────────
  if (data.awaiting_confirmation) {
    console.log('[voice] Awaiting block confirmation');
    showElement('confirm-block-area');
    document.getElementById('block-summary-badge').textContent = 'Review';
  } else {
    hideElement('confirm-block-area');
  }

  // ── Interview complete ─────────────────────────────
  if (data.interview_complete) {
    console.log('[voice] Interview complete!');
    showElement('complete-banner');
    document.getElementById('block-summary-badge').textContent = 'Done';
    setRecBtn('state-processing', 'check-circle-2', 'Interview complete');
    document.getElementById('rec-btn').disabled = true;
    setStatus('Interview complete! MessageDNA saved.');
    appState = STATE.IDLE;
    return;
  }

  // ── Reset button for next answer ──────────────────
  resetToReady();

  // Status line
  const blockName = data.current_block_name || `Block ${data.current_block}`;
  setStatus(`${blockName} — ready for your next answer`);
}

// ── Confirm block ──────────────────────────────────────────────────────

async function confirmBlock() {
  if (!sessionId) return;
  console.log('[voice] confirmBlock() — sending confirmation to agent');
  clearError();

  const btn = document.getElementById('confirm-btn');
  btn.disabled = true;

  setLoading(true);
  setStatus('Confirming block…');

  try {
    const res  = await fetch('/voice/turn/text', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({
        session_id: sessionId,
        text: 'Yes, that sounds right. Please continue to the next block.',
      }),
    });
    const data = await res.json();
    console.log('[voice] confirmBlock response:', {
      success:   data.success,
      confirmed: data.blocks_confirmed,
      complete:  data.interview_complete,
    });

    hideElement('confirm-block-area');
    btn.disabled = false;

    if (data.success) {
      handleTurnResponse(data);
    } else {
      showError('Confirmation failed: ' + (data.error_message || 'Unknown error'));
      resetToReady();
    }

  } catch (err) {
    showError('Network error during confirmation: ' + err.message);
    btn.disabled = false;
    resetToReady();
    console.error('[voice] confirmBlock error:', err);
  }

  setLoading(false);
}

// ── Audio playback ────────────────────────────────────────────────────

function playAudio(base64Mp3) {
  const player = document.getElementById('audio-player');
  player.src   = 'data:audio/mp3;base64,' + base64Mp3;
  showElement('audio-player');

  player.play().catch(err => {
    // Autoplay blocked (common in browsers without prior interaction)
    console.warn('[voice] Audio autoplay blocked:', err.message, '— showing player controls');
    // Player is already visible with controls — user can click play manually
    setStatus('Audio ready — click play in the audio bar below the question.');
  });
}

// ── DOM helpers ───────────────────────────────────────────────────────

function showAgentQuestion(text) {
  document.getElementById('agent-question-text').textContent = text;
}

function showFounderTranscript(text) {
  document.getElementById('founder-transcript-text').textContent = text;
  showElement('founder-transcript-area');
}

function showAiResponse(text) {
  document.getElementById('ai-response-text').textContent = text;
  showElement('ai-response-area');
}

function addHistoryItem(who, text) {
  const list = document.getElementById('history-list');

  // Remove placeholder
  const placeholder = list.querySelector('.italic');
  if (placeholder) placeholder.remove();

  const div = document.createElement('div');
  div.className = `rounded-lg p-3 ${who === 'founder' ? 'turn-founder bg-primary-soft' : 'turn-agent bg-secondary'}`;
  div.innerHTML = `
    <p class="text-10 font-bold uppercase tracking-wide mb-1 ${
      who === 'founder' ? 'text-accent-foreground' : 'text-muted-foreground'
    }">${who === 'founder' ? 'You' : 'Social Spark Studio'}</p>
    <p class="text-sm text-foreground leading-relaxed">${escapeHtml(text)}</p>
  `;
  list.appendChild(div);
  list.scrollTop = list.scrollHeight;
}

function updateBlockProgress(confirmed, currentBlock, currentBlockName) {
  document.getElementById('block-progress-text').textContent =
    `${confirmed} of 4 blocks confirmed`;

  document.getElementById('block-summary-badge').textContent =
    confirmed === 4 ? 'Done' : (currentBlockName || `Block ${currentBlock}`);

  for (let i = 1; i <= 4; i++) {
    const card   = document.getElementById(`block-${i}`);
    const num    = document.getElementById(`block-${i}-num`);
    const status = document.getElementById(`block-${i}-status`);

    if (i <= confirmed) {
      // Confirmed / complete
      card.className  = 'rounded-2xl border p-4 shadow-soft border-success-30 bg-success-5';
      num.className   = 'flex h-7 w-7 items-center justify-center rounded-full text-xs font-bold bg-success text-success-foreground';
      num.innerHTML   = '<i data-lucide="check" class="h-3.5 w-3.5"></i>';
      status.textContent = 'Done';
    } else if (i === currentBlock) {
      // Active
      card.className  = 'rounded-2xl border p-4 shadow-soft border-primary bg-primary-soft';
      num.className   = 'flex h-7 w-7 items-center justify-center rounded-full text-xs font-bold step-active';
      num.textContent = i;
      status.textContent = 'In progress';
    } else {
      // Todo
      card.className  = 'rounded-2xl border p-4 shadow-soft border-border bg-card';
      num.className   = 'flex h-7 w-7 items-center justify-center rounded-full text-xs font-bold step-todo';
      num.textContent = i;
      status.textContent = 'Up next';
    }
  }

  lucide.createIcons();
}

// ── Recording button state machine ────────────────────────────────────

function setRecBtn(stateClass, iconName, label) {
  const btn      = document.getElementById('rec-btn');
  const iconEl   = document.getElementById('rec-btn-icon');
  const labelEl  = document.getElementById('rec-btn-label');

  btn.className  = stateClass;
  iconEl.setAttribute('data-lucide', iconName);
  labelEl.textContent = label;
  lucide.createIcons();
}

function setMicIndicator(state) {
  const indicator = document.getElementById('mic-indicator');
  const icon      = document.getElementById('mic-indicator-icon');

  if (state === 'recording') {
    indicator.className = 'flex h-16 w-16 items-center justify-center rounded-full bg-destructive text-destructive-foreground';
    icon.setAttribute('data-lucide', 'mic');
    indicator.style.animation = 'pulse-rec 1.8s ease-in-out infinite';
  } else if (state === 'processing') {
    indicator.className = 'flex h-16 w-16 items-center justify-center rounded-full bg-muted text-muted-foreground';
    icon.setAttribute('data-lucide', 'loader');
    indicator.style.animation = '';
  } else {
    // idle / ready
    indicator.className = 'flex h-16 w-16 items-center justify-center rounded-full bg-primary-soft text-accent-foreground';
    icon.setAttribute('data-lucide', 'mic');
    indicator.style.animation = '';
  }
  lucide.createIcons();
}

function resetToReady() {
  appState = STATE.READY;

  if (voiceAvailable) {
    setRecBtn('state-again', 'mic', 'Record Another Answer');
    setHint('Click to record your next answer, or type it below.');
  } else {
    setRecBtn('state-start', 'mic-off', 'Microphone unavailable');
    setHint('Type your answer in the box below.');
  }

  setMicIndicator('idle');
  setLoading(false);
}

// ── Error handling ────────────────────────────────────────────────────

function showError(msg) {
  document.getElementById('error-text').textContent = msg;
  showElement('error-banner');
  console.warn('[voice] ERROR:', msg);
}

function clearError() {
  document.getElementById('error-text').textContent = '';
  hideElement('error-banner');
}

// ── Generic utilities ──────────────────────────────────────────────────

function showElement(id)  { document.getElementById(id)?.classList.remove('hidden'); }
function hideElement(id)  { document.getElementById(id)?.classList.add('hidden'); }

function setLoading(on) {
  document.getElementById('spinner').classList.toggle('hidden', !on);
}

function setStatus(msg) {
  document.getElementById('status-text').textContent = msg;
}

function setHint(msg) {
  document.getElementById('rec-hint').textContent = msg;
}

function escapeHtml(str) {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
