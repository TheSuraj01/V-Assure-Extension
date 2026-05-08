// State
let allSteps = [];
let filtered = [];
let activeFilter = 'all';
let isRecording = false;
let selectedStep = null;

// Send message to active tab's content script
function msgTab(type, data = {}) {
  return new Promise((resolve) => {
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      if (!tabs[0]) return resolve(null);

      const tabId = tabs[0].id;
      chrome.tabs.sendMessage(tabId, { type, ...data }, (res) => {
        if (chrome.runtime.lastError) {
          chrome.scripting.executeScript({
            target: { tabId },
            files: ['content.js']
          }).then(() => {
            chrome.tabs.sendMessage(tabId, { type, ...data }, (res2) => {
              if (chrome.runtime.lastError) resolve(null);
              else resolve(res2);
            });
          }).catch(() => resolve(null));
        } else {
          resolve(res);
        }
      });
    });
  });
}

// Sync state from content script
async function syncState() {
  const res = await msgTab('GET_STATE');
  if (!res) return;
  allSteps = res.steps || [];
  isRecording = res.isRecording || false;
  renderAll();
}

// Render

function renderAll() {
  updateControls();
  updateStatus();
  renderList();
}

function updateControls() {
  document.getElementById('startBtn').disabled = isRecording;
  document.getElementById('stopBtn').disabled = !isRecording;
  document.getElementById('generateBtn').disabled = allSteps.length === 0;
  document.getElementById('recDot').className = 'rec-dot' + (isRecording ? ' active' : '');
}

function updateStatus() {
  const badge = document.getElementById('statusBadge');
  const text = document.getElementById('statusText');
  const count = document.getElementById('stepCount');

  if (isRecording) {
    badge.className = 'status-badge recording';
    badge.textContent = 'REC';
    text.textContent = 'Recording — interact with Veeva Vault';
  } else if (allSteps.length > 0) {
    badge.className = 'status-badge stopped';
    badge.textContent = 'DONE';
    text.textContent = 'Recording stopped';
  } else {
    badge.className = 'status-badge idle';
    badge.textContent = 'IDLE';
    text.textContent = 'Start recording to capture interactions';
  }

  count.textContent = `${allSteps.length} step${allSteps.length !== 1 ? 's' : ''}`;
}

function getCategory(step) { return step.action; }

function renderList() {
  const listEl = document.getElementById('list');

  filtered = activeFilter === 'all'
    ? [...allSteps]
    : allSteps.filter(s => getCategory(s) === activeFilter);

  if (filtered.length === 0) {
    listEl.innerHTML = `
      <div class="empty">
        <div class="icon">${allSteps.length === 0 ? '⬤' : '🔍'}</div>
        <p>${allSteps.length === 0
        ? 'Press <strong>Start</strong> then interact<br>with any element on Veeva Vault.'
        : 'No steps match this filter.'
      }</p>
      </div>`;
    return;
  }

  listEl.innerHTML = filtered.map((step, i) => {
    const el = step.element;
    let label = el.label || el.text || el.placeholder || el.type || '?';
    if (step.userStep) label = `[${step.userStep}] ` + label;
    const typedValue = el.inputValue || el.selectedValue || '';
    const sub = el.label && el.placeholder ? `Placeholder: ${el.placeholder}`
      : step.identifiers?.id ? `id="${step.identifiers.id}"`
        : step.identifiers?.ariaLabel ? `aria-label="${step.identifiers.ariaLabel}"`
          : el.text && el.text !== label ? el.text.substring(0, 60)
            : '';

    return `
      <div class="step-row" data-i="${i}">
        <div class="step-num">${step.step}</div>
        <div class="step-info">
          <div class="step-top">
            <span class="action-pill ${step.action}">${step.action}</span>
            <span class="type-tag">${el.tag || el.type}</span>
            <span class="step-time">${new Date(step.timestamp).toLocaleTimeString()}</span>
          </div>
          <div class="step-label">${escHtml(label)}</div>
          ${typedValue ? `<div class="step-value">→ "${escHtml(typedValue)}"</div>` : ''}
          ${sub ? `<div class="step-sub">${escHtml(sub)}</div>` : ''}
        </div>
      </div>`;
  }).join('');

  listEl.querySelectorAll('.step-row').forEach(row => {
    row.addEventListener('click', () => {
      openDetail(filtered[parseInt(row.dataset.i)]);
    });
  });
}

// Detail Panel

function openDetail(step) {
  selectedStep = step;
  const el = step.element;
  let label = el.label || el.text || el.type;
  if (step.userStep) label = `[${step.userStep}] ` + label;
  document.getElementById('panelTitle').textContent = `#${step.step} — ${label}`;

  const kb = stepToKB(step);

  document.getElementById('panelBody').innerHTML = `
    <div class="block-label" style="margin-bottom:5px;">Raw Capture</div>
    <div class="json-block">${highlight(step)}</div>
    <div class="block-label" style="margin-bottom:5px;margin-top:12px;">KB Entry Preview</div>
    <div class="json-block">${highlight(kb)}</div>
  `;

  document.getElementById('detailPanel').classList.add('open');
}

document.getElementById('backBtn').addEventListener('click', () => {
  document.getElementById('detailPanel').classList.remove('open');
  selectedStep = null;
});

document.getElementById('copyRawEntry').addEventListener('click', () => {
  if (selectedStep) copyText(JSON.stringify(selectedStep, null, 2));
});

document.getElementById('copyKbEntry').addEventListener('click', () => {
  if (selectedStep) copyText(JSON.stringify(stepToKB(selectedStep), null, 2));
});

// KB Converter

function stepToKB(step) {
  const el = step.element;
  const label = el.label || el.text || el.placeholder || el.type || 'element';

  const input = { action: step.action, label };

  if (step.action === 'enter') {
    input.value = el.inputValue || '<<value>>';
    if (el.placeholder) input.placeholder = el.placeholder;
  }
  if (step.action === 'select') {
    input.selectedText = el.selectedValue || el.inputValue || '<<value>>';
    if (step.dropdownParent) input.dropdownLabel = step.dropdownParent;
  }
  if (step.action === 'screenshot') input.label = 'Screenshot';
  if (step.identifiers?.id) input.elementId = step.identifiers.id;
  if (step.identifiers?.ariaLabel) input.ariaLabel = step.identifiers.ariaLabel;
  if (step.userStep) input.userStep = step.userStep;

  let output = '';
  if (step.action === 'click') {
    output = `Click on the '${label}' ${el.type}.`;
  } else if (step.action === 'enter') {
    output = `Enter '${el.inputValue || '<<value>>'}' in the ${label} ${el.placeholder ? `(placeholder: "${el.placeholder}") ` : ''}field.`;
  } else if (step.action === 'select') {
    output = `Select '${el.selectedValue || el.inputValue || '<<value>>'}' from the ${step.dropdownParent || label} dropdown list.`;
  } else if (step.action === 'screenshot') {
    output = `Take screenshot of the page.`;
  } else if (step.action === 'step_marker') {
    output = `Proceed to ${el.label}.`;
  }

  const slug = label.toLowerCase().replace(/[^a-z0-9\s]/g, '').trim()
    .replace(/\s+/g, '_').substring(0, 40);

  return { name: `${slug}_veeva`, input, output };
}

function allStepsToKB() {
  return allSteps.map(stepToKB);
}

// JSON Syntax Highlight

function highlight(obj) {
  return JSON.stringify(obj, null, 2)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/("[\w@\-\s]+")\s*:/g, '<span class="jk">$1</span>:')
    .replace(/:\s*("(?:[^"\\]|\\.)*")/g, (m, s) => m.replace(s, `<span class="js">${s}</span>`))
    .replace(/:\s*(\d+\.?\d*)/g, (m, n) => m.replace(n, `<span class="jn">${n}</span>`))
    .replace(/:\s*(true|false|null)/g, (m, b) => m.replace(b, `<span class="jb">${b}</span>`));
}

// Controls

document.getElementById('startBtn').addEventListener('click', async () => {
  await msgTab('START');
  isRecording = true;
  renderAll();
  startPolling();
});

document.getElementById('stopBtn').addEventListener('click', async () => {
  await msgTab('STOP');
  isRecording = false;
  stopPolling();
  await syncState();
});

document.getElementById('restartBtn').addEventListener('click', async () => {
  if (allSteps.length > 0 && !confirm('Restart will clear all captured steps. Download first?')) return;
  await msgTab('RESTART');
  isRecording = false;
  allSteps = [];
  stopPolling();
  renderAll();
});

// Polling while recording
let pollInterval;
function startPolling() {
  pollInterval = setInterval(async () => {
    const res = await msgTab('GET_STATE');
    if (!res) return;
    allSteps = res.steps || [];
    isRecording = res.isRecording || false;
    renderAll();
    if (!isRecording) stopPolling();
  }, 800);
}
function stopPolling() { clearInterval(pollInterval); }

// Filter chips

document.querySelectorAll('.chip').forEach(chip => {
  chip.addEventListener('click', () => {
    document.querySelectorAll('.chip').forEach(c => c.classList.remove('active'));
    chip.classList.add('active');
    activeFilter = chip.dataset.f;
    renderList();
  });
});

// Export

document.getElementById('exportRawBtn').addEventListener('click', () => {
  download(`veeva-raw-${Date.now()}.json`, JSON.stringify(allSteps, null, 2));
});

document.getElementById('exportKbBtn').addEventListener('click', () => {
  download(`veeva-kb-${Date.now()}.json`, JSON.stringify(allStepsToKB(), null, 2));
});

document.getElementById('clearBtn').addEventListener('click', async () => {
  if (!confirm('Clear all captured steps?')) return;
  await msgTab('RESTART');
  allSteps = [];
  isRecording = false;
  renderAll();
});

function download(filename, text) {
  const a = document.createElement('a');
  a.href = 'data:application/json;charset=utf-8,' + encodeURIComponent(text);
  a.download = filename;
  a.click();
}

// Generate Prompts — Backend Integration
const DEFAULT_BACKEND = 'http://127.0.0.1:8000'; // ENV
let generatedSessionId = null;
let generatedSteps = [];

document.getElementById('generateBtn').addEventListener('click', () => {
  document.getElementById('genOverlay').classList.add('open');
  checkBackendHealth();
});

document.getElementById('genBackBtn').addEventListener('click', () => {
  document.getElementById('genOverlay').classList.remove('open');
});

async function checkBackendHealth() {
  const healthEl = document.getElementById('healthStatus');
  try {
    const res = await fetch(`${DEFAULT_BACKEND}/health`, { signal: AbortSignal.timeout(3000) });
    if (res.ok) {
      const data = await res.json();
      healthEl.innerHTML = `<span style="color:var(--green)">● Backend online</span> · ${data.kb_entries_total} KB entries loaded`;
    } else {
      healthEl.innerHTML = `<span style="color:var(--red)">● Backend error (${res.status})</span>`;
    }
  } catch {
    healthEl.innerHTML = `<span style="color:var(--amber)">● Backend offline — check ${DEFAULT_BACKEND}</span>`;
  }
}

document.getElementById('runGenBtn').addEventListener('click', async () => {
  const selectedModelVal = document.getElementById('modelSelect').value;
  const [provider, model] = selectedModelVal.split('|');
  const useStream = document.getElementById('useStream').checked;
  const useRag = document.getElementById('useRag').checked;
  const sessionName = document.getElementById('sessionName').value.trim();
  const outputEl = document.getElementById('genOutput');
  const runBtn = document.getElementById('runGenBtn');
  const dlBtn = document.getElementById('downloadOutputBtn');

  if (allSteps.length === 0) {
    outputEl.textContent = '❌ No steps captured yet.';
    return;
  }

  const kb = allStepsToKB();
  generatedSteps = [];
  generatedSessionId = null;
  outputEl.textContent = '';
  dlBtn.style.display = 'none';
  runBtn.disabled = true;

  const payload = {
    entries: kb,
    model,
    provider,
    temperature: 0.15,
    use_rag: useRag,
    session_name: sessionName || undefined
  };

  try {
    if (useStream) {
      outputEl.innerHTML = `<span style="color:var(--muted2)">Streaming ${kb.length} steps</span>\n\n`;

      const res = await fetch(`${DEFAULT_BACKEND}/generate/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      if (!res.ok) {
        const bodyText = await res.text().catch(() => res.statusText);
        console.error('generate/stream non-ok response', res.status, bodyText);
        throw new Error(bodyText || res.statusText);
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let lastPrintedUserStep = null;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        const lines = buffer.split('\n');
        buffer = lines.pop();

        for (const line of lines) {
          if (!line.startsWith('data:')) continue;
          const json = line.slice(5).trim();
          if (!json) continue;
          try {
            const evt = JSON.parse(json);
            if (evt.done) {
              outputEl.textContent += `\n✅ Done — ${generatedSteps.length} steps generated`;
              dlBtn.style.display = 'flex';
              break;
            }
            generatedSteps.push(evt);

            if (evt.action === 'step_marker') continue;

            if (evt.userStep && evt.userStep !== lastPrintedUserStep) {
              if (lastPrintedUserStep !== null) outputEl.textContent += '\n';
              outputEl.textContent += `${evt.userStep}:\n`;
              lastPrintedUserStep = evt.userStep;
            }

            const line_ = `${evt.enhanced_output}`;
            outputEl.textContent += line_ + '\n';
          } catch { }
        }
      }

    } else {
      outputEl.innerHTML = `<span style="color:var(--muted2)">Processing ${kb.length} steps via backend</span><span class="loading-dots"></span>`;

      const res = await fetch(`${DEFAULT_BACKEND}/generate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      if (!res.ok) {
        const bodyText = await res.text().catch(() => res.statusText);
        console.error('generate non-ok response', res.status, bodyText);
        throw new Error(bodyText || res.statusText);
      }

      const data = await res.json();
      generatedSessionId = data.session_id;
      generatedSteps = data.steps || [];

      outputEl.textContent = data.full_script;
      outputEl.textContent += `\n\n✅ Session: ${data.session_id} | Model: ${data.model_used}`;
      dlBtn.style.display = 'flex';
    }

  } catch (err) {
    console.error('Generation error (popup)', err);
    const details = err && (err.stack || err.message) ? `\n\n${err.stack || err.message}` : '';
    outputEl.textContent = `❌ Error: ${err.message || 'network error'}${details}\n\nMake sure the backend is running at ${DEFAULT_BACKEND}`;
  } finally {
    runBtn.disabled = false;
  }
});

document.getElementById('downloadOutputBtn').addEventListener('click', async () => {
  if (generatedSessionId) {
    window.open(`${DEFAULT_BACKEND}/download/${generatedSessionId}`, '_blank');
  } else {
    const text = document.getElementById('genOutput').textContent;
    const a = document.createElement('a');
    a.href = 'data:text/plain;charset=utf-8,' + encodeURIComponent(text);
    a.download = `veeva_steps_${Date.now()}.txt`;
    a.click();
  }
});

document.getElementById('copyOutputBtn').addEventListener('click', () => {
  copyText(document.getElementById('genOutput').textContent);
});

// Copy Helpers

function copyText(text) {
  navigator.clipboard.writeText(text).then(() => showToast());
}

function showToast(msg = 'Copied!') {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 1600);
}

function escHtml(str) {
  return String(str || '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

document.getElementById('modelSelect').addEventListener('change', () => {
  chrome.storage.local.set({ genModel: document.getElementById('modelSelect').value });
});

chrome.storage.local.get(['genModel'], (r) => {
  if (r.genModel && Array.from(document.getElementById('modelSelect').options).some(o => o.value === r.genModel)) {
    document.getElementById('modelSelect').value = r.genModel;
  }
});

syncState();