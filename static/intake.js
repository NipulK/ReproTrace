const byId = id => document.getElementById(id);
const fields = ['expected_behavior','actual_behavior','reproduction_steps','environment','missing_information','notes'];
let current = null, revision = 0, aiConfig = null, analyzing = false, generating = false, executionTimer = null;
async function request(url, options) {
  const response = await fetch(url, options);
  const body = await response.json();
  if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'Check the report URL and required fields.');
  return body;
}
function errorMessage(error) { byId('intake-error').textContent=error.message; byId('intake-error').hidden=false; }
function sourceChanged() {
  const paste=byId('source').value==='paste';
  byId('paste-input').hidden=!paste; byId('github-input').hidden=paste;
  byId('issue-url').required=!paste; byId('report-title').required=paste; byId('report-body').required=paste;
}
async function savedReports() {
  const reports=await request('/reports');
  byId('report-list').replaceChildren();
  for(const report of reports) {
    const button=document.createElement('button'); button.type='button'; button.className='history-item';
    const title=document.createElement('strong'); title.textContent=report.title;
    const meta=document.createElement('small'); meta.textContent=report.repository+' · '+(report.reviewed_at?'Reviewed':'Awaiting review');
    button.append(title,meta); button.addEventListener('click',()=>openReport(report.id).catch(errorMessage));
    byId('report-list').append(button);
  }
  if(!reports.length) byId('report-list').textContent='No saved reports yet.';
}
async function openReport(id) {
  clearTimeout(executionTimer);
  const token=++revision;
  const report=await request('/reports/'+id);
  if(token!==revision) return;
  byId('suggestions').replaceChildren(); byId('analysis-status').textContent='';
  byId('test-drafts').replaceChildren(); byId('test-status').textContent='';
  current=report; window.history.replaceState(null,'','#report='+id);
  byId('report-empty').hidden=true; byId('report-detail').hidden=false;
  byId('snapshot-title').textContent=report.snapshot.title;
  byId('snapshot-meta').textContent=report.snapshot.repository+' · Captured '+new Date(report.captured_at).toLocaleString();
  byId('snapshot-body').textContent=report.snapshot.body;
  byId('snapshot-hash').textContent='Snapshot SHA-256: '+report.snapshot_sha256;
  const link=byId('snapshot-link'); link.hidden=!report.snapshot.issue_url;
  if(report.snapshot.issue_url) link.href=report.snapshot.issue_url; else link.removeAttribute('href');
  for(const field of fields) byId(field).value=report.review[field];
  byId('review-status').textContent=report.reviewed_at?'Review saved '+new Date(report.reviewed_at).toLocaleString():'Review not saved yet.';
  await loadAnalyses(id,token);
  await loadDrafts(id,token);
  updateGenerateButton();
}
byId('source').addEventListener('change',sourceChanged);
byId('intake-form').addEventListener('submit',async event=>{
  event.preventDefault(); byId('capture').disabled=true; byId('intake-error').hidden=true;
  byId('capture-status').textContent='Capturing report…';
  const payload=byId('source').value==='github'?{source:'github',issue_url:byId('issue-url').value.trim()}:
    {source:'paste',repository:byId('repository').value,title:byId('report-title').value,body:byId('report-body').value};
  try {
    const report=await request('/reports',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    await savedReports(); await openReport(report.id); byId('capture-status').textContent='Snapshot saved. Review the original report on the right.';
  } catch(error) {errorMessage(error);byId('capture-status').textContent='Report was not saved.';}
  finally {byId('capture').disabled=false;}
});
byId('review-form').addEventListener('submit',async event=>{
  event.preventDefault(); if(!current)return;
  const id=current.id; byId('save-review').disabled=true; byId('intake-error').hidden=true;
  try {
    const review=Object.fromEntries(fields.map(field=>[field,byId(field).value]));
    const result=await request('/reports/'+id+'/review',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(review)});
    if(current.id===id) {current=result;byId('review-status').textContent='Review saved '+new Date(result.reviewed_at).toLocaleString();updateGenerateButton();await loadDrafts(id,revision);}
    await savedReports();
  } catch(error) {errorMessage(error);} finally {byId('save-review').disabled=false;}
});
sourceChanged();
loadConfig();
(async()=>{try{await savedReports();const match=location.hash.match(/^#report=([a-f0-9-]{36})$/);if(match)await openReport(match[1]);}catch(error){errorMessage(error);}})();

async function loadConfig() {
  try {
    aiConfig=await request('/ai/config');
    byId('ai-config').textContent=aiConfig.configured
      ? `${aiConfig.provider === 'gemini' ? 'Gemini' : 'OpenAI'} · ${aiConfig.model}`
      : `${aiConfig.provider} is not configured. Add ${aiConfig.key_variable} to your local .env and restart the server.`;
    byId('analyze').disabled=!aiConfig.configured || analyzing || generating;
    byId('ai-disclosure').textContent=`Analyze sends this saved report’s title and body to ${aiConfig.provider === 'gemini' ? 'Google Gemini' : 'OpenAI'}. Review notes are excluded. Suggestions require review; exact quotes do not guarantee correct interpretation.`;
    updateGenerateButton();
  } catch(error) {byId('ai-config').textContent='Could not read AI configuration.';}
}
function element(tag,text,className) {
  const item=document.createElement(tag);item.textContent=text;
  if(className)item.className=className;return item;
}
function applySuggestion(field,value) {
  byId(field).value=value;
  byId('review-status').textContent='AI suggestion applied to the draft. Review it, then click Save review.';
}
function renderAnalysis(analysis) {
  byId('suggestions').replaceChildren();
  if(!current || analysis.snapshot_sha256!==current.snapshot_sha256) {
    byId('analysis-status').textContent='Analysis belongs to a different snapshot.';return;
  }
  const payload=analysis.payload;
  byId('analysis-status').textContent=`Suggestions from ${payload.returned_model || payload.model} · ${new Date(analysis.created_at).toLocaleString()} · ${analysis.duration_seconds}s`;
  for(const field of ['expected_behavior','actual_behavior','reproduction_steps','environment']) {
    const suggestion=payload.suggestions[field];
    const card=element('article','','suggestion');
    card.append(element('h4',field.replaceAll('_',' ')));
    if(suggestion.value===null) {
      card.append(element('p','Unknown — the report does not provide supported details.','hint'));
    } else {
      card.append(element('p',suggestion.value));
      for(const evidence of suggestion.evidence) {
        card.append(element('small',`Source: ${evidence.source} · exact quote checked`),element('blockquote',evidence.quote));
      }
      const button=element('button','Apply to '+field.replaceAll('_',' '),'apply-button');
      button.type='button';
      button.addEventListener('click',()=>{
        const old=byId(field).value;
        if(old.trim() && old!==suggestion.value && button.dataset.replace!=='yes') {
          button.textContent='Replace existing draft text? Click again to replace';
          button.dataset.replace='yes';return;
        }
        applySuggestion(field,suggestion.value);button.textContent='Applied to draft';button.dataset.replace='';
      });
      card.append(button);
    }
    byId('suggestions').append(card);
  }
  const questions=payload.suggestions.missing_information;
  if(questions.length) {
    const card=element('article','','suggestion');card.append(element('h4','Suggested clarification questions'));
    const list=element('ul','');for(const question of questions)list.append(element('li',question));card.append(list);
    const button=element('button','Append questions to missing information','apply-button');button.type='button';
    button.addEventListener('click',()=>{
      const old=byId('missing_information').value;
      const fresh=questions.filter(question=>!old.includes(question));
      applySuggestion('missing_information',[old,...fresh].filter(Boolean).join('\n'));
      button.textContent='Questions added to draft';
    });card.append(button);byId('suggestions').append(card);
  }
  if(payload.suggestions.warnings.length) {
    const card=element('article','','suggestion');card.append(element('h4','Model-noted limitations'));
    for(const warning of payload.suggestions.warnings)card.append(element('p',warning));
    byId('suggestions').append(card);
  }
}
async function loadAnalyses(id,token) {
  const analyses=await request('/reports/'+id+'/analyses');
  if(token!==revision)return;
  const latest=analyses.find(analysis=>analysis.status==='completed');
  if(latest)renderAnalysis(latest);
  if(analyses[0]?.status==='failed') {
    byId('analysis-status').textContent='Latest attempt failed: '+analyses[0].payload.error+(latest?' Earlier suggestions are shown below.':'');
  }
}
byId('analyze').addEventListener('click',async()=>{
  if(!current || analyzing)return;
  const id=current.id,token=revision;
  analyzing=true;byId('analyze').disabled=true;updateGenerateButton();byId('intake-error').hidden=true;
  byId('analysis-status').textContent=`Analyzing the saved report with ${aiConfig.provider}… This can take up to 90 seconds.`;
  try {
    const analysis=await request('/reports/'+id+'/analyze',{method:'POST'});
    if(token===revision && current.id===id)renderAnalysis(analysis);
  } catch(error) {
    if(token===revision) {errorMessage(error);byId('analysis-status').textContent='Analysis failed. Your existing review is unchanged.';}
  } finally {analyzing=false;byId('analyze').disabled=!aiConfig?.configured;updateGenerateButton();}
});

function updateGenerateButton() {
  const ready=current?.reviewed_at && current.review.expected_behavior.trim() && current.review.actual_behavior.trim();
  byId('generate-test').disabled=!aiConfig?.configured || !ready || analyzing || generating;
}
function renderDraft(item) {
  const container=byId('test-drafts');container.replaceChildren();
  const draft=item.payload.draft;
  byId('test-status').textContent=(item.review_is_current?'Based on your saved review.':'Outdated: your saved review has changed. Generate a new draft.')+
    ` ${item.payload.returned_model || item.payload.model} · ${new Date(item.created_at).toLocaleString()} · Draft generated; execution history below.`;
  for(const key of ['assertion_rationale','dependencies','setup_notes','assumptions','missing_information']) {
    if(!draft[key].length)continue;
    const card=element('article','','suggestion');card.append(element('h4',key.replaceAll('_',' ')));
    for(const text of draft[key])card.append(element('p',text));container.append(card);
  }
  if(draft.code) {
    const label=element('label','Generated pytest draft');label.htmlFor='generated-code';
    const code=document.createElement('textarea');code.id='generated-code';code.readOnly=true;code.rows=18;code.value=draft.code;
    const download=element('a','Download test source','apply-button');download.href='/tests/'+item.id+'/file';download.setAttribute('download','');
    container.prepend(label,code);container.append(download);
    configureExecution(item,container,revision).catch(errorMessage);
  } else {
    container.prepend(element('p','More information is needed. Update and save the review, then generate again.'));
  }
}
async function loadDrafts(id,token) {
  const drafts=await request('/reports/'+id+'/tests');
  if(token!==revision)return;
  const completed=drafts.find(item=>item.status==='completed');
  if(completed)renderDraft(completed);
  if(drafts[0]?.status==='failed')byId('test-status').textContent='Latest generation failed: '+drafts[0].payload.error+(completed?' Earlier draft shown below.':'');
}
byId('generate-test').addEventListener('click',async()=>{
  if(!current || generating)return;
  if(fields.some(field=>byId(field).value!==current.review[field])) {
    byId('test-status').textContent='You have unsaved review edits. Click Save review before generating.';return;
  }
  const id=current.id,token=revision;generating=true;updateGenerateButton();byId('analyze').disabled=true;
  byId('intake-error').hidden=true;byId('test-status').textContent='Generating a draft from the saved review…';
  try {
    const item=await request('/reports/'+id+'/tests',{method:'POST'});
    if(token===revision)renderDraft(item);
  } catch(error) {if(token===revision){errorMessage(error);byId('test-status').textContent='Generation failed. Your report and review are unchanged.';}}
  finally {generating=false;byId('analyze').disabled=!aiConfig?.configured || analyzing;updateGenerateButton();}
});

async function configureExecution(item,container,token) {
  clearTimeout(executionTimer);
  const panel=element('section','','ai-panel');
  panel.append(element('h3','Execute against two releases','small-heading'),
    element('p','Runs this generated code in separate Docker containers. No network, credentials, or project files are exposed. Inspect the code first. Version pairs are curated; AI dependency suggestions are not installed.','hint'));
  const label=element('label','Pinned version profile');label.htmlFor='execution-profile';
  const select=document.createElement('select');select.id='execution-profile';
  const button=element('button','Run buggy + fixed','primary');button.type='button';button.disabled=true;
  const status=element('p','Checking Docker…','hint');status.setAttribute('role','status');
  const history=element('div',''),output=element('div',''),historyButtons=new Map();
  panel.append(label,select,button,status,history,output);container.append(panel);
  const [profiles,runtime,runs]=await Promise.all([request('/cases'),request('/execution/config'),request('/tests/'+item.id+'/runs')]);
  if(token!==revision)return;
  for(const profile of profiles.filter(p=>current.snapshot.repository.endsWith('/'+p.package.toLowerCase()))) {
    const option=element('option',`${profile.id}: ${profile.buggy} → ${profile.fixed}`);option.value=profile.id;select.append(option);
  }
  const inferred=current.snapshot.repository.split('/')[1]+'-'+current.snapshot.issue_number;
  if(profiles.some(p=>p.id===inferred))select.value=inferred;
  button.disabled=!runtime.available || !item.review_is_current;
  status.textContent=item.review_is_current?runtime.detail:'Review changed. Generate a new draft before running.';
  async function watch(id) {
    clearTimeout(executionTimer);
    try {
      const run=await request('/test-runs/'+id);
      if(token!==revision)return;
      status.textContent=`${run.status} · ${run.progress}`;
      if(historyButtons.has(id))historyButtons.get(id).textContent=`${run.status} · ${new Date(run.created_at).toLocaleString()}`;
      output.replaceChildren();
      if(run.payload.test_sha256)output.append(element('p','Test SHA-256: '+run.payload.test_sha256,'mono'));
      if(run.payload.regression_signal)output.append(element('p','Regression signal: buggy test failed; fixed release passed. Inspect the assertion and logs before confirming the bug.'));
      else if(run.status==='completed')output.append(element('p','No clear fail/pass regression signal. Inspect the results below.'));
      for(const attempt of run.payload.results || []) {
        const card=element('article','','suggestion');
        card.append(element('h4',`${attempt.role} · ${attempt.package} ${attempt.version}`),
          element('p',`${attempt.outcome} · exit ${attempt.exit_code ?? 'unknown'} · ${attempt.duration_seconds}s`));
        if(attempt.counts)card.append(element('p',JSON.stringify(attempt.counts)));
        if(attempt.error)card.append(element('p',attempt.error));
        for(const [name,text] of Object.entries(attempt.logs || {})) {
          const details=element('details','');details.append(element('summary',name),element('pre',text || '(empty)','source-text'));card.append(details);
        }
        output.append(card);
      }
      if(run.payload.error)output.append(element('p',run.payload.error));
      if(['queued','running'].includes(run.status)) {
        button.disabled=true;executionTimer=setTimeout(()=>watch(id),2000);
      } else button.disabled=!runtime.available || !item.review_is_current;
    }catch(error){if(token===revision){errorMessage(error);button.disabled=!runtime.available || !item.review_is_current;}}
  }
  for(const run of runs) {
    const link=element('button',`${run.status} · ${new Date(run.created_at).toLocaleString()}`,'history-item');link.type='button';
    link.addEventListener('click',()=>watch(run.id));historyButtons.set(run.id,link);history.append(link);
  }
  if(runs.length)await watch(runs[0].id);
  button.addEventListener('click',async()=>{
    button.disabled=true;byId('intake-error').hidden=true;
    try {
      if(fields.some(field=>byId(field).value!==current.review[field]))throw new Error('Save review edits and generate a fresh draft before running.');
      const run=await request('/tests/'+item.id+'/runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({profile:select.value})});
      if(token===revision){
        const link=element('button','queued · Just submitted','history-item');link.type='button';
        link.addEventListener('click',()=>watch(run.id));historyButtons.set(run.id,link);history.prepend(link);
        await watch(run.id);
      }
    }catch(error){errorMessage(error);button.disabled=!runtime.available || !item.review_is_current;}
  });
}
