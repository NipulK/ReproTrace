const byId = id => document.getElementById(id);
const fields = ['expected_behavior','actual_behavior','reproduction_steps','environment','missing_information','notes'];
let current = null, revision = 0, aiConfig = null, analyzing = false;
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
  const token=++revision;
  const report=await request('/reports/'+id);
  if(token!==revision) return;
  byId('suggestions').replaceChildren(); byId('analysis-status').textContent='';
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
    if(current.id===id) {current=result;byId('review-status').textContent='Review saved '+new Date(result.reviewed_at).toLocaleString();}
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
      ? `OpenAI · ${aiConfig.model}`
      : 'OpenAI is not configured. Add OPENAI_API_KEY to your local .env and restart the server.';
    byId('analyze').disabled=!aiConfig.configured || analyzing;
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
  analyzing=true;byId('analyze').disabled=true;byId('intake-error').hidden=true;
  byId('analysis-status').textContent='Analyzing the saved report with OpenAI… This can take up to 90 seconds.';
  try {
    const analysis=await request('/reports/'+id+'/analyze',{method:'POST'});
    if(token===revision && current.id===id)renderAnalysis(analysis);
  } catch(error) {
    if(token===revision) {errorMessage(error);byId('analysis-status').textContent='Analysis failed. Your existing review is unchanged.';}
  } finally {analyzing=false;byId('analyze').disabled=!aiConfig?.configured;}
});
