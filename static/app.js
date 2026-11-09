const $ = id => document.getElementById(id);
const titles = {
  'werkzeug-2842': 'None conversion fallback', 'werkzeug-2834': 'Double-slash routing',
  'werkzeug-2980': 'Invalid cache directive', 'click-2500': 'Empty default in help',
  'click-2294': 'Reusable argument decorator'
};
let cases = [], selected = null, timer = null, selectionVersion = 0;
async function api(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let body; try { body = await response.json(); } catch { body = {}; }
    throw new Error(typeof body.detail === 'string' ? body.detail : `Request failed (${response.status}).`);
  }
  return response.json();
}
function showError(error) { $('error').textContent = error.message; $('error').hidden = false; }
function node(tag, text, className) {
  const item = document.createElement(tag); item.textContent = text;
  if (className) item.className = className;
  return item;
}
function versions() {
  const item = cases.find(c => c.id === $('case').value);
  $('versions').textContent = item ? `${item.package} ${item.buggy} → ${item.fixed}` : '';
}
async function loadHistory() {
  const runs = await api('/runs?limit=20');
  $('history').replaceChildren();
  if (!runs.length) $('history').append(node('p', 'No experiments yet. Start your first run above.', 'hint'));
  for (const run of runs) {
    const button = node('button', '', 'history-item' + (selected === run.id ? ' selected' : ''));
    button.type = 'button';
    button.append(node('strong', titles[run.case_id] || run.case_id),
      node('small', `${run.status} · ${run.release} · ${new Date(run.created_at).toLocaleString()}`));
    button.addEventListener('click', () => select(run.id));
    $('history').append(button);
  }
  return runs;
}
async function loadDetail(id, generation) {
  const [run, result, files] = await Promise.all([
    api(`/runs/${id}`), api(`/runs/${id}/results`), api(`/runs/${id}/artifacts`)
  ]);
  if (generation !== selectionVersion) return;
  $('empty').hidden = true; $('detail').hidden = false;
  $('run-title').textContent = titles[run.case_id] || run.case_id;
  $('run-id').textContent = run.id;
  $('run-time').textContent = `Created ${new Date(run.created_at).toLocaleString()}`;
  $('status').textContent = run.status; $('status').dataset.status = run.status;
  $('progress').textContent = run.error || ({queued:'Waiting for the local worker…', running:'Preparing environments and executing tests…', completed:'Execution finished. Inspect each observation below.', interrupted:'This experiment was interrupted. Submit a new run to retry.'}[run.status] || run.status);
  $('results').replaceChildren();
  for (const attempt of result.results) {
    const card = node('article', '', 'result');
    card.append(node('h4', `${attempt.package} ${attempt.version} · ${attempt.release_role}`),
      node('div', attempt.outcome.replaceAll('_', ' '), 'outcome'),
      node('p', attempt.matches_expectation ? 'Matches this fixture’s expected outcome.' : 'Does not match this fixture’s expected outcome. Inspect the logs.'),
      node('p', `Recorded ${new Date(attempt.finished_at).toLocaleString()}`));
    const stages = node('div', '', 'stages');
    for (const stage of attempt.stages) stages.append(node('span', `${stage.name}: ${stage.status} · ${stage.duration_seconds}s`));
    card.append(stages); $('results').append(card);
  }
  $('artifacts').replaceChildren();
  for (const file of files) {
    const link = node('a', '↓ ' + file.path);
    link.href = file.url; link.setAttribute('download', ''); $('artifacts').append(link);
  }
  if (!files.length) $('artifacts').append(node('p', 'Evidence files will appear when execution starts.', 'hint'));
  if (['queued', 'running'].includes(run.status)) {
    timer = setTimeout(async () => {
      try { await loadHistory(); await loadDetail(id, generation); } catch (error) { showError(error); }
    }, 2000);
  } else { await loadHistory(); }
}
async function select(id) {
  clearTimeout(timer); selected = id; const generation = ++selectionVersion;
  window.history.replaceState(null, '', '#run=' + id);
  $('error').hidden = true;
  try { await loadHistory(); await loadDetail(id, generation); } catch (error) { showError(error); }
}
$('case').addEventListener('change', versions);
$('refresh').addEventListener('click', async () => {
  try { if (selected) await select(selected); else await loadHistory(); } catch (error) { showError(error); }
});
$('run-form').addEventListener('submit', async event => {
  event.preventDefault(); $('submit').disabled = true; $('error').hidden = true;
  try {
    const run = await api('/runs', {method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({case:$('case').value, release:$('release').value, timeout:Number($('timeout').value)})});
    await select(run.id);
  } catch (error) { showError(error); } finally { $('submit').disabled = false; }
});
(async () => {
  try {
    cases = await api('/cases'); $('case').replaceChildren();
    for (const item of cases) {
      const option = node('option', `${item.package} · ${titles[item.id] || item.id}`);
      option.value = item.id; $('case').append(option);
    }
    $('case').disabled = false; $('submit').disabled = false; versions();
    const runs = await loadHistory();
    const hash = location.hash.match(/^#run=([a-f0-9-]{36})$/);
    if (hash) await select(hash[1]); else if (runs.length) await select(runs[0].id);
  } catch (error) { showError(error); }
})();
