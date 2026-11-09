const byId = id => document.getElementById(id);
const fields = ['expected_behavior','actual_behavior','reproduction_steps','environment','missing_information','notes'];
let current = null, revision = 0;
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
(async()=>{try{await savedReports();const match=location.hash.match(/^#report=([a-f0-9-]{36})$/);if(match)await openReport(match[1]);}catch(error){errorMessage(error);}})();
