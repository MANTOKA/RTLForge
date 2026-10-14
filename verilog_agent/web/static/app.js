const app = document.querySelector('#app');
const crumb = document.querySelector('#breadcrumb');
const connection = document.querySelector('#connection');
let stream = null;
let caps = null;
let history = null;
let tablePage = 0;
let renderVersion = 0;
let pageController = null;

const esc = (value) => String(value ?? '').replace(/[&<>"']/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const show = (value, missing='当时未记录') => value === null || value === undefined || value === ''
  ? `<span class="muted">${missing}</span>` : esc(value);
const cell = (value) => value === null || value === undefined ? 'NOT_REPORTED' : esc(value);
const field = (f) => f?.availability === 'recorded' ? show(f.value) : '<span class="muted">当时未记录</span>';
const badge = (value) => {
  const v = String(value ?? 'UNKNOWN');
  const cls = /PASS|COMPLETED|RANK1|RESCUE/.test(v) ? 'pass' :
    /FAIL|ERROR|UNRESOLVED|INTERRUPTED/.test(v) ? 'fail' :
    /RUNNING|GENERATING|VERIFYING|EVALUATING/.test(v) ? 'run' :
    /UNKNOWN|PARTIAL|UNREADABLE/.test(v) ? 'missing' : 'warn';
  return `<span class="badge ${cls}">${esc(v)}</span>`;
};
const fmtTime = (ms) => typeof ms === 'number' ? `${(ms/1000).toFixed(1)}s` : '当时未记录';
function runDate(value) {
  const date=new Date(typeof value==='number'?value*1000:value);
  if(!Number.isFinite(date.getTime()))return null;
  const pad=n=>String(n).padStart(2,'0');
  return `${date.getFullYear()}-${pad(date.getMonth()+1)}-${pad(date.getDate())}`;
}
function historyTime(row) {
  const day=runDate(row.run_time);
  if(!day)return '<span class="muted">当时未记录</span>';
  const source=row.time_source==='event'?'首条任务事件':row.time_source==='report_created'?'报告创建时间':row.time_source==='job_started'?'网页任务启动时间':'文件日期（推定）';
  if(row.time_source==='file_date')return `${esc(day)} <span class="muted">文件日期</span>`;
  const date=new Date(row.run_time),pad=n=>String(n).padStart(2,'0');
  return `<time title="${esc(source)} · 本地时间">${esc(day)} ${pad(date.getHours())}:${pad(date.getMinutes())}</time>`;
}
async function api(path, options={}) {
  const request = options.method ? options : {...options, signal:pageController?.signal};
  const response = await fetch(path, request);
  let data;
  try { data = await response.json(); } catch { throw new Error(`HTTP ${response.status}`); }
  if (!response.ok) throw new Error(data?.error?.message || `HTTP ${response.status}`);
  return data;
}
function setConnection(text, good=true) {
  connection.textContent = text;
  connection.className = `connection ${good ? 'ok' : 'bad'}`;
}
function navigate(path) { if(location.pathname===path)return; historyPush(path); render(); }
function historyPush(path) { window.history.pushState({}, '', path); }
function closeStream() { if (stream) { stream.close(); stream = null; } }
function bindLinks() {
  document.querySelectorAll('.sidebar a').forEach(a => {
    a.classList.toggle('active', location.pathname.startsWith(a.getAttribute('href')));
  });
}
document.addEventListener('click',event=>{
  const link=event.target.closest('a[data-link]');
  if(!link)return;
  event.preventDefault();navigate(link.getAttribute('href'));
});
function bindStream(kind, id) {
  closeStream();
  const prefix = kind === 'task' ? 'tasks' : 'benchmarks';
  stream = new EventSource(`/api/${prefix}/${encodeURIComponent(id)}/stream`);
  let timer;
  const refresh = () => {
    clearTimeout(timer);
    timer=setTimeout(async()=>{
      if (location.pathname !== `/${prefix}/${id}`) return;
      const scroll = window.scrollY;
      try {
        if (kind === 'task') await taskDetail(id,false);
        else await benchmarkDetail(id,false);
        if (location.pathname === `/${prefix}/${id}`) window.scrollTo(0, scroll);
      } catch(error) { if(error.name!=='AbortError')setConnection('实时刷新失败',false); }
    },150);
  };
  for (const type of ['stage','result','finished']) stream.addEventListener(type, refresh);
  stream.onopen = () => setConnection('实时连接正常');
  stream.onerror = () => setConnection('实时连接中断，正在重连', false);
}
function heading(title, subtitle='') {
  crumb.textContent = title;
  return `<div class="eyebrow">VERILOGAGENT / LOCAL WORKBENCH</div><h1>${esc(title)}</h1>`+
    (subtitle ? `<p class="subtitle">${esc(subtitle)}</p>` : '');
}
function errorView(error) {
  return `${heading('无法加载')}<div class="notice error">${esc(error.message || error)}</div>`;
}
function formValue(form, key) { return form.elements.namedItem(key)?.value; }
function numeric(form, key) { return Number(formValue(form, key)); }
function optionValues(items, selected='') { return items.map(x =>
  `<option value="${esc(x)}" ${x === selected ? 'selected' : ''}>${esc(x)}</option>`).join(''); }
function labelled(name, label, content, cls='') {
  return `<label class="${cls}">${esc(label)}${content.replace(/<(input|select|textarea)(?=[\s>])/,
    `<$1 name="${esc(name)}"`)}</label>`;
}
function advanced(defaultRepairs=2) { const entries=Object.entries(caps?.profiles||{}), selectedName=entries.find(([,item])=>item.configured)?.[0]; return `
  ${labelled('provider_profile','模型配置文件',`<select>${entries.map(([name,item])=>`<option value="${esc(name)}" ${name===selectedName?'selected':''} ${item.configured?'':'disabled'}>${esc(name)} · ${esc(item.model||'未配置')}</option>`).join('')}</select>`)}
  ${labelled('model','模型',`<input value="${esc(caps?.provider?.model || '')}" required>`)}
  ${labelled('candidates','独立候选上限','<select><option>1</option><option selected>2</option><option>3</option></select>')}
  ${labelled('max_repairs','修复预算',`<select>${[0,1,2,3].map(n=>`<option ${n===defaultRepairs?'selected':''}>${n}</option>`).join('')}</select>`)}
  ${labelled('memory_mode','Memory 模式','<select><option value="off">off</option><option value="read_only">read_only</option><option value="read_write">read_write</option></select>')}
  ${labelled('docker_image','Docker 镜像','<input value="verilog-agent-sandbox:reflection">')}
  <div class="card wide small" id="profile-info">当前配置：${show(caps?.provider?.base_url||'默认 endpoint')} · temperature ${show(caps?.provider?.sampling?.temperature)} · top_p ${show(caps?.provider?.sampling?.top_p)} · seed ${show(caps?.provider?.sampling?.seed)} · ${show(caps?.provider?.sampling?.seed_status)}<br><span class="muted">配置文件决定 API 地址和密钥；模型名可在同一 Provider 下调整。</span></div>`; }
function bindProfile(form) {
  const profile=form.elements.namedItem('provider_profile');
  profile.onchange=()=>{
    const selected=caps.profiles[profile.value];
    form.elements.namedItem('model').value=selected.model||'';
    form.querySelector('#profile-info').innerHTML=`当前配置：${show(selected.base_url||'默认 endpoint')} · temperature ${show(selected.sampling?.temperature)} · top_p ${show(selected.sampling?.top_p)} · seed ${show(selected.sampling?.seed)} · ${show(selected.sampling?.seed_status)}<br><span class="muted">配置文件决定 API 地址和密钥；密钥不会传给浏览器。</span>`;
  };
  if(profile.value&&profile.value!=='.env')profile.onchange();
}
function caseSelect(dataset) {
  return optionValues(caps?.datasets?.[dataset]?.cases || []);
}
async function newTask() {
  caps = caps || await api('/api/capabilities');
  app.innerHTML = `${heading('新建单任务','从需求或现有基准题开始，运行后可在历史页继续查看。')}
    ${!Object.values(caps.profiles||{}).some(p=>p.configured) ? `<div class="notice error">Provider 未配置：${esc(caps.provider.error)}</div>` : ''}
    ${!caps.docker?.available?`<div class="notice error">Docker 不可用：${esc(caps.docker?.reason)}。当前只能选择自然语言“只生成”；基准单题、静态检查和功能验证不会启动。</div>`:''}
    <form id="task-form" class="stack">
      <section class="card"><h2>输入来源</h2><div class="form-grid">
        ${labelled('source','来源','<select><option value="natural">自然语言需求</option><option value="human_v2">Human v2 单题</option><option value="rtllm">RTLLM 单题</option></select>')}
        <div id="case-field" class="wide" hidden>${labelled('case','题目','<select></select>')}</div>
        <div id="spec-field" class="wide">${labelled('spec','RTL 需求','<textarea placeholder="例如：实现一个 8 位同步计数器，带高电平复位…" required></textarea>')}</div>
        ${labelled('level','运行级别','<select><option value="generate">只生成</option><option value="static" selected>静态检查</option><option value="verify">功能验证</option></select>')}
        <div id="testbench-field" hidden>${labelled('testbench_id','受控 testbench 文件名（data/benchmarks/user-testbenches）','<input placeholder="counter_tb.v">')}</div>
      </div><p class="hint" id="task-hint">自然语言任务若没有指定 testbench，只能得到生成或静态结果，不能声称功能 PASS。</p></section>
      <section class="card"><h2>运行参数</h2><div class="form-grid">${advanced()}</div></section>
      <div class="actions"><button class="button primary" type="submit" ${Object.values(caps.profiles||{}).some(p=>p.configured)?'':'disabled'}>启动单任务</button><span id="task-error" class="error-line" role="alert"></span></div>
    </form>`;
  const form = document.querySelector('#task-form');
  bindProfile(form);
  const sync = () => {
    const source = formValue(form,'source'), dataset = source === 'rtllm' ? 'rtllm' : 'human_v2';
    document.querySelector('#case-field').hidden = source === 'natural';
    document.querySelector('#spec-field').hidden = source !== 'natural';
    form.elements.namedItem('spec').required = source === 'natural';
    if (source !== 'natural') { form.elements.namedItem('case').innerHTML = caseSelect(dataset); form.elements.namedItem('level').value = 'verify'; }
    document.querySelector('#testbench-field').hidden = source !== 'natural' || formValue(form,'level') !== 'verify';
    form.querySelector('button[type=submit]').disabled = !caps.profiles?.[formValue(form,'provider_profile')]?.configured ||
      (!caps.docker?.available && (source !== 'natural' || formValue(form,'level') !== 'generate'));
  };
  form.elements.namedItem('source').addEventListener('change', sync);
  form.elements.namedItem('level').addEventListener('change', sync);
  form.elements.namedItem('provider_profile').addEventListener('change', sync);
  sync();
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const source = formValue(form,'source');
    const payload = {source, level:formValue(form,'level'), model:formValue(form,'model'),
      provider_profile:formValue(form,'provider_profile'),
      candidates:numeric(form,'candidates'), max_repairs:numeric(form,'max_repairs'),
      memory_mode:formValue(form,'memory_mode'), docker_image:formValue(form,'docker_image')};
    if (source === 'natural') {
      payload.spec = formValue(form,'spec');
      if (payload.level === 'verify') payload.testbench_id = formValue(form,'testbench_id');
    } else payload.case = formValue(form,'case');
    const button = form.querySelector('button[type=submit]'); button.disabled = true;
    try { const run = await api('/api/tasks',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}); navigate(`/tasks/${run.id}`); }
    catch (error) { document.querySelector('#task-error').textContent = error.message; button.disabled = false; }
  });
}

async function newBenchmark() {
  caps = caps || await api('/api/capabilities');
  app.innerHTML = `${heading('批量测试','Human v2 与 RTLLM；每题完成后更新报告，支持断点续跑。')}
    ${!Object.values(caps.profiles||{}).some(p=>p.configured) ? `<div class="notice error">Provider 未配置：${esc(caps.provider.error)}</div>` : ''}
    ${!caps.docker?.available?`<div class="notice error">Docker 不可用：${esc(caps.docker?.reason)}。批量测试需要先完成参考设计预检，当前不会启动。</div>`:''}
    <form id="batch-form" class="card stack"><div class="form-grid">
      ${labelled('dataset','数据集','<select><option value="human_v2">Human v2</option><option value="rtllm">RTLLM 2.1</option></select>')}
      ${labelled('limit','题目数量（留空=全量）','<input type="number" min="1" max="156" placeholder="例如 10">')}
      ${advanced()}
    </div><div class="notice info">协议：oracle-assisted closed-loop。Golden testbench 与 reference 仅由验证侧使用；结果不称为严格 Pass@1/2/3。</div>
    <div class="actions"><button class="button primary" type="submit" ${Object.values(caps.profiles||{}).some(p=>p.configured)&&caps.docker?.available?'':'disabled'}>启动批量测试</button><span id="batch-error" class="error-line" role="alert"></span></div></form>`;
  const form = document.querySelector('#batch-form');
  bindProfile(form);
  form.elements.namedItem('provider_profile').addEventListener('change',()=>{
    form.querySelector('button[type=submit]').disabled = !caps.profiles?.[formValue(form,'provider_profile')]?.configured || !caps.docker?.available;
  });
  form.addEventListener('submit', async event => {
    event.preventDefault(); const value = formValue(form,'limit');
    const payload = {dataset:formValue(form,'dataset'), model:formValue(form,'model'),
      provider_profile:formValue(form,'provider_profile'),
      candidates:numeric(form,'candidates'), max_repairs:numeric(form,'max_repairs'),
      memory_mode:formValue(form,'memory_mode'), docker_image:formValue(form,'docker_image')};
    if (value) payload.limit = Number(value);
    const button = form.querySelector('button[type=submit]'); button.disabled = true;
    try { const run = await api('/api/benchmarks',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}); navigate(`/benchmarks/${run.id}`); }
    catch(error) { document.querySelector('#batch-error').textContent = error.message; button.disabled = false; }
  });
}

function eventsPanel(events) {
  if (!events?.length) return '<div class="empty">阶段事件当时未记录，或 Planner 仍在运行。</div>';
  return `<div class="timeline">${events.map(event => `<div class="timeline-item"><span class="timeline-dot"></span><div><strong>${esc(event.event_type)}</strong> ${badge(event.stage)}<small>#${esc(event.sequence)} · ${show(event.timestamp)}</small>${event.payload?.reason ? `<small>${esc(event.payload.reason)}</small>` : ''}</div></div>`).join('')}</div>`;
}
function candidatesPanel(view) {
  if (!view.candidates?.length) return '<div class="empty">候选尚未生成或当时未记录。</div>';
  const attempts = view.verification?.attempts || [];
  return view.candidates.map(c => {
    const staticScore = c.static?.quality_score;
    const attempt = attempts.find(a=>a.candidate_id === c.candidate_id);
    return `<div class="candidate"><div class="candidate-head"><strong class="mono">${esc(c.candidate_id)}</strong>${c.selected?badge('最终选择'):''}${badge(c.parent_id?'修复候选':'独立候选')}${attempt?badge(attempt.status):''}</div>
      <div class="meta">来源：${c.parent_id?esc(c.parent_id):'独立生成'} · 触发：${show(c.generation_reason)} · 静态分 ${cell(staticScore)} · 功能验证 ${show(attempt?.status)} · 设计模式 ${show(c.design_pattern)}</div>
      ${c.artifact ? `<div class="actions" style="margin-top:12px"><a href="/api/artifacts/${esc(c.artifact.id)}" download>下载 ${esc(c.artifact.name)}</a></div>`:''}
      <details><summary>静态与诊断详情</summary><pre>${esc(JSON.stringify({static:c.static,verification:attempt},null,2))}</pre></details></div>`;
  }).join('');
}
function tokensPanel(view) {
  const calls=view.model_calls, usage=view.token_usage;
  if (calls === null || usage === null) return '<div class="notice">Token 当时未记录。</div>';
  const role = usage?.by_role || {};
  return `<div class="mini-grid">${[['调用次数',usage?.call_count],['输入 Token',usage?.input_tokens],['输出 Token',usage?.output_tokens],['缓存输入',usage?.cached_input_tokens]].map(([k,v])=>`<div class="card"><div class="stat-label">${k}</div><div class="stat">${cell(v)}</div></div>`).join('')}</div>
    <p class="hint">已报告 ${cell(usage?.reported_calls)}/${cell(usage?.call_count)} 次；缓存输入已包含于输入 Token。</p>
    <div class="table-wrap"><table><thead><tr><th>角色</th><th>调用</th><th>输入</th><th>输出</th><th>耗时</th></tr></thead><tbody>${Object.entries(role).map(([name,r])=>`<tr><td>${esc(name)}</td><td>${cell(r.call_count)}</td><td>${cell(r.input_tokens)}</td><td>${cell(r.output_tokens)}</td><td>${fmtTime(r.latency_ms)}</td></tr>`).join('')}</tbody></table></div>
    <details><summary>逐次调用与服务端回显</summary><pre>${esc(JSON.stringify(calls,null,2))}</pre></details>`;
}
function plannerPanel(view) {
  if (view.planner?.module_name?.availability !== 'recorded') {
    return `<div class="notice info">${view.run?.status === 'RUNNING' ? 'Planner 正在分析；完成后显示需求理解。' : 'Planner 内容当时未记录。'}</div>`;
  }
  const p=view.planner;
  const raw=String(p.raw_spec?.value||'').replace(/^Please act as a professional verilog designer\.\s*/i,'');
  const concise=raw.split(/[.!?。！？]/).filter(Boolean).slice(0,2).join('。').slice(0,220);
  return `<div class="grid"><div><h3>我理解的需求</h3><p>模块 ${field(p.module_name)}：${show(concise)}</p></div><div><h3>采用的假设</h3><p>${Array.isArray(p.assumptions?.value)?p.assumptions.value.map(esc).join('；'):field(p.assumptions)}</p></div><div><h3>如何判断完成</h3><p>${Array.isArray(p.acceptance_criteria?.value)?p.acceptance_criteria.value.map(esc).join('；'):field(p.acceptance_criteria)}</p></div><div><h3>需要注意</h3><p>${field(p.ambiguities)}</p></div></div>
    <details><summary>技术详情：端口、时钟、SpecIR 与任务规划</summary><pre>${esc(JSON.stringify({planner:p,task_tree:view.task_tree},null,2))}</pre></details>`;
}
async function taskDetail(id, shouldStream=true) {
  const view = await api(`/api/tasks/${encodeURIComponent(id)}`);
  const current = view.run?.status === 'RUNNING';
  app.innerHTML = `${heading('单任务详情',`${current?'实时运行':view.run?'网页任务':'历史回放'} · ${id}`)}
    <div class="summary-line">${badge(view.run?.status || view.status)}${badge(view.result_type)}${badge(view.completeness)}${badge(view.protocol)}</div>
    ${view.preflight?`<div class="notice error">${esc(view.preflight.message)}预检类别：${esc(view.preflight.category)}；Agent 调用：0。请先确认 Docker Desktop 已启动，且当前运行网页服务的进程有执行 docker.exe 的权限。</div>`:''}
    ${view.error?`<div class="notice error">${esc(view.error)}</div>`:''}
    ${view.run?.message&&!view.preflight?`<div class="notice">${esc(view.run.message)}</div>`:''}
    <div class="grid three"><div class="card"><div class="stat-label">候选策略</div><div class="stat small">${field(view.fields.candidate_policy)}</div></div>
      <div class="card"><div class="stat-label">最终候选</div><div class="stat small mono">${field(view.fields.selected_candidate_id)}</div></div>
      <div class="card"><div class="stat-label">采样 temperature / top_p / seed</div><div class="stat small">${view.fields.sampling?.availability==='recorded'?`${cell(view.fields.sampling.value.temperature)} / ${cell(view.fields.sampling.value.top_p)} / ${cell(view.fields.sampling.value.seed)} · ${show(view.fields.sampling.value.seed_status)}`:'当时未记录'}</div></div></div>
    <section class="section card"><h2>需求与系统结论</h2><div class="narrative"><strong>原始需求</strong><p>${field(view.fields.input)}</p></div><div class="divider"></div>${plannerPanel(view)}</section>
    <section class="section card"><div class="section-title"><h2>阶段时间线</h2><small>来自实际事件记录</small></div>${eventsPanel(view.events)}</section>
    <section class="section card"><h2>候选与修复路径</h2><p class="hint">策略：先生成一个；静态低于 75 可重新生成；功能失败先修复，再按需启用后续独立候选。整题修复预算不重置。</p>${candidatesPanel(view)}
      <details><summary>候选触发与修复详情</summary><pre>${esc(JSON.stringify({budget:view.fields.budget?.value,regenerations:view.fields.candidate_regenerations?.value,repair_attempts:view.repair_attempts,repair_fallback:view.repair_fallback},null,2))}</pre></details></section>
    <section class="section grid"><div class="card"><h2>功能验证</h2><dl class="kv"><dt>结果</dt><dd>${view.result_type==='UNVERIFIED'?'本运行未配置功能验证':show(view.verification?.passed===true?'PASS':view.verification?.passed===false?'FAIL':null)}</dd><dt>类别</dt><dd>${show(view.verification?.category)}</dd><dt>验证次数</dt><dd>${view.verification?.attempts?.length ?? '当时未记录'}</dd></dl><details><summary>验证诊断</summary><pre>${esc(JSON.stringify(view.verification?.attempts,null,2))}</pre></details></div>
      <div class="card"><h2>Memory</h2><dl class="kv"><dt>模式</dt><dd>${show(view.memory?.mode)}</dd><dt>检索条数</dt><dd>${view.memory?.retrieved?.length ?? '当时未记录'}</dd><dt>新增条数</dt><dd>${view.memory?.consolidated?.length ?? '当时未记录'}</dd></dl><p class="hint">检索到 Memory 不代表由它造成提升。</p></div></section>
    <section class="section card"><h2>Token 与耗时</h2>${tokensPanel(view)}</section>
    <section class="section card"><h2>简洁总结</h2><pre id="task-summary">${esc(view.summary_markdown)}</pre><div class="actions"><button id="copy-summary" class="button">复制 Markdown</button><a class="button ghost" href="/api/tasks/${esc(id)}/summary.md">下载 Markdown</a></div></section>`;
  document.querySelector('#copy-summary').addEventListener('click', () => navigator.clipboard.writeText(view.summary_markdown));
  if (current && shouldStream) bindStream('task',id);
  if (!current) closeStream();
}

function benchmarkRows(rows, filters) {
  const text=filters.text.toLowerCase();
  const filtered=rows.filter(r=>
    (filters.status==='all'||r.status===filters.status) &&
    (filters.rescue==='all'||(r.rescue||'UNKNOWN_LEGACY')===filters.rescue) &&
    (filters.failure==='all'||r.failure_category===filters.failure) &&
    (filters.memory==='all'||(filters.memory==='hit'?r.memory_hits>0:r.memory_hits===0)) &&
    (filters.repaired==='all'||(filters.repaired==='yes'?(r.repair_attempts||0)>0:r.repair_attempts===0)) &&
    (filters.tokens==='all'||(filters.tokens==='missing'?r.token_usage?.input_tokens==null:r.token_usage?.input_tokens!=null)) &&
    String(r.problem_id||'').toLowerCase().includes(text));
  return filtered.sort((a,b)=>filters.sort==='problem'?String(a.problem_id).localeCompare(String(b.problem_id)):
    filters.sort==='status'?String(a.status).localeCompare(String(b.status)):
    (b.duration_ms??-1)-(a.duration_ms??-1));
}
async function benchmarkDetail(id, shouldStream=true) {
  const view=await api(`/api/benchmarks/${encodeURIComponent(id)}`), rows=view.results||[];
  const metrics=view.metrics||{}, config=view.configuration||{}, usage=metrics.token_usage||{};
  const running=view.run?.status==='RUNNING';
  app.innerHTML=`${heading('批量测试详情',`${view.run?'实时运行':'历史报告'} · ${show(view.dataset)}`)}
    <div class="summary-line">${badge(view.run?.status||'HISTORICAL')}${badge(view.protocol||'oracle_assisted_closed_loop')} ${badge(config.memory_mode||'Memory 当时未记录')}</div>
    ${view.last_error?`<div class="notice error">停止原因：${esc(JSON.stringify(view.last_error))}</div>`:''}
    <div class="mini-grid">${[['已记录结果',`${rows.length} / ${config.requested_case_count??view.run?.limit??'?'}`],['PASS / FAIL / ERROR',`${metrics.status_counts?.PASS??0} / ${metrics.status_counts?.FAIL??0} / ${metrics.status_counts?.ERROR??0}`],['功能通过率（PASS/FAIL）',metrics.agent_pass_rate_evaluable==null?'当时未记录':`${(metrics.agent_pass_rate_evaluable*100).toFixed(1)}%`],['已知输入 / 输出 Token',`${cell(usage.input_tokens)} / ${cell(usage.output_tokens)}`]].map(([k,v])=>`<div class="card"><div class="stat-label">${esc(k)}</div><div class="stat">${esc(v)}</div></div>`).join('')}</div>
    <section class="section card"><h2>运行配置</h2><dl class="kv"><dt>模型</dt><dd>${show(config.model||view.run?.model)}</dd><dt>候选策略</dt><dd>${show(config.candidate_policy)}</dd><dt>候选 / 修复预算</dt><dd>${show(config.candidate_count??view.run?.candidates)} / ${show(config.max_repairs??view.run?.max_repairs)}</dd><dt>Prompt 版本</dt><dd>${show(config.prompt_version)}</dd><dt>采样</dt><dd>${typeof config.sampling?.temperature==='number'?esc(JSON.stringify(config.sampling)):'确切数值当时未记录'}</dd><dt>额度错误</dt><dd>${show(metrics.quota_errors)}</dd></dl><p class="hint">网页功能通过率只以 PASS/FAIL 为分母；历史 JSON 原统计可在接口的 stored_metrics 查看。</p>${view.run&&!running?'<button class="button ghost" id="resume-batch">按原配置续跑</button><span id="resume-error" class="error-line" role="alert"></span>':''}</section>
    <section class="section card"><div class="detail-head"><div><h2>逐题结果</h2><p class="hint">点选 task ID 回放相同的单任务详情。</p></div><a class="button ghost" href="/api/benchmarks/${esc(id)}/report.md">导出 Markdown 报告</a></div>
      <div class="toolbar"><input id="result-search" placeholder="搜索 problem_id" aria-label="搜索题目" style="max-width:260px"><select id="result-status" aria-label="按状态筛选" style="max-width:180px"><option value="all">所有状态</option><option>PASS</option><option>FAIL</option><option>ERROR</option><option>SKIPPED_REFERENCE</option></select>
      <select id="result-rescue" aria-label="按救援类型筛选" style="max-width:180px"><option value="all">所有救援类型</option><option>RANK1_PASS</option><option>FALLBACK_RESCUE</option><option>REPAIR_RESCUE</option><option>UNRESOLVED</option><option>INFRASTRUCTURE_ERROR</option><option>UNKNOWN_LEGACY</option></select>
      <select id="result-failure" aria-label="按失败类别筛选" style="max-width:170px"><option value="all">所有失败类别</option>${[...new Set(rows.map(r=>r.failure_category).filter(Boolean))].sort().map(v=>`<option>${esc(v)}</option>`).join('')}</select>
      <select id="result-memory" aria-label="按 Memory 命中筛选" style="max-width:150px"><option value="all">Memory 全部</option><option value="hit">命中</option><option value="miss">未命中</option></select>
      <select id="result-repaired" aria-label="按修复筛选" style="max-width:140px"><option value="all">修复全部</option><option value="yes">经过修复</option><option value="no">未修复</option></select>
      <select id="result-tokens" aria-label="按 Token 缺失筛选" style="max-width:150px"><option value="all">Token 全部</option><option value="missing">Token 缺失</option><option value="known">Token 已报告</option></select>
      <select id="result-sort" aria-label="排序" style="max-width:140px"><option value="problem">题号排序</option><option value="status">状态排序</option><option value="duration">耗时排序</option></select></div>
      <div id="result-table"></div></section>`;
  const draw=()=>{
    const filtered=benchmarkRows(rows,{text:document.querySelector('#result-search').value,
      status:document.querySelector('#result-status').value,rescue:document.querySelector('#result-rescue').value,
      failure:document.querySelector('#result-failure').value,memory:document.querySelector('#result-memory').value,
      repaired:document.querySelector('#result-repaired').value,tokens:document.querySelector('#result-tokens').value,
      sort:document.querySelector('#result-sort').value});
    const pages=Math.max(1,Math.ceil(filtered.length/30)); tablePage=Math.min(tablePage,pages-1);
    const slice=filtered.slice(tablePage*30,(tablePage+1)*30);
    document.querySelector('#result-table').innerHTML=`<div class="table-wrap"><table><thead><tr><th>Problem</th><th>Status</th><th>Rescue</th><th>选中候选 / 上限</th><th>Static</th><th>Repairs</th><th>Verification</th><th>Failure</th><th>Memory</th><th>Token</th><th>Duration</th><th>Task</th></tr></thead><tbody>${slice.map(r=>`<tr><td>${esc(r.problem_id)}</td><td>${badge(r.status)}</td><td>${badge(r.rescue||'UNKNOWN_LEGACY')}</td><td>${show(r.selected_candidate_id)} / ${show(r.candidate_count)}</td><td>${cell(r.static_score)}</td><td>${show(r.repair_attempts)}</td><td>${show(r.verification_cycles)}</td><td>${show(r.failure_category||r.error_kind)}</td><td>${cell(r.memory_hits)}</td><td>${r.token_usage?cell(r.token_usage.input_tokens):'NOT_REPORTED'}</td><td>${fmtTime(r.duration_ms)}</td><td>${r.task_id?`<a data-link href="/tasks/${esc(r.task_id)}">${esc(r.task_id)}</a>`:'当时未记录'}</td></tr>`).join('')}</tbody></table></div><div class="pager"><button id="prev" class="button ghost" ${tablePage===0?'disabled':''}>上一页</button><span>${tablePage+1} / ${pages}</span><button id="next" class="button ghost" ${tablePage>=pages-1?'disabled':''}>下一页</button></div>`;
    document.querySelector('#prev').onclick=()=>{tablePage--;draw();};
    document.querySelector('#next').onclick=()=>{tablePage++;draw();}; bindLinks();
  };
  document.querySelector('#result-search').oninput=()=>{tablePage=0;draw();};
  for(const id of ['status','rescue','failure','memory','repaired','tokens','sort'])
    document.querySelector(`#result-${id}`).onchange=()=>{tablePage=0;draw();};
  draw();
  if(view.run&&!running) document.querySelector('#resume-batch').onclick=async()=>{
    const button=document.querySelector('#resume-batch');button.disabled=true;
    const payload={resume_run_id:id,dataset:view.run.dataset,model:view.run.model,
      provider_profile:view.run.provider_profile,
      candidates:view.run.candidates,max_repairs:view.run.max_repairs,
      memory_mode:view.run.memory_mode,docker_image:view.run.docker_image};
    if(view.run.limit!=null)payload.limit=view.run.limit;
    try{const run=await api('/api/benchmarks',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});navigate(`/benchmarks/${run.id}`);}
    catch(error){document.querySelector('#resume-error').textContent=error.message;button.disabled=false;}
  };
  if(running&&shouldStream) bindStream('benchmark',id);
  if(!running)closeStream();
}

async function historyPage() {
  history=await api('/api/history');
  app.innerHTML=`${heading('历史运行','扫描现有 runs/，回放已保存证据。历史记录不会重新执行。')}
    <div class="mini-grid"><div class="card"><div class="stat-label">单任务</div><div class="stat">${history.tasks.length}</div></div><div class="card"><div class="stat-label">批量报告</div><div class="stat">${history.benchmarks.length}</div></div></div>
    <section class="section card"><div class="toolbar"><h2>任务与报告</h2><span class="spacer"></span><input id="history-search" style="max-width:230px" placeholder="搜索 ID / 数据集" aria-label="搜索历史"><select id="history-kind" aria-label="筛选类型"><option value="all">全部类型</option><option value="task">单任务</option><option value="benchmark">批量报告</option></select><select id="history-model" aria-label="筛选模型"><option value="all">全部模型</option>${[...new Set([...history.tasks,...history.benchmarks].map(r=>r.model).filter(Boolean))].sort().map(v=>`<option value="${esc(v)}">${esc(v)}</option>`).join('')}</select><select id="history-status" aria-label="筛选状态"><option value="all">全部状态</option>${[...new Set([...history.tasks,...history.benchmarks].map(r=>r.status).filter(Boolean))].sort().map(v=>`<option value="${esc(v)}">${esc(v)}</option>`).join('')}</select><select id="history-completeness" aria-label="筛选完整度"><option value="all">全部完整度</option><option>current</option><option>legacy</option><option>partial</option><option>unreadable</option></select><label>运行日期（起）<input id="history-date-from" type="date"></label><label>运行日期（止）<input id="history-date-to" type="date"></label></div><p class="hint">时间按浏览器本地时区显示；缺少运行时间的旧产物仅显示文件日期（推定）。</p><div id="history-table"></div></section>`;
  const draw=()=>{
    const query=document.querySelector('#history-search').value.toLowerCase();
    const kind=document.querySelector('#history-kind').value;
    const model=document.querySelector('#history-model').value;
    const status=document.querySelector('#history-status').value;
    const completeness=document.querySelector('#history-completeness').value;
    const from=document.querySelector('#history-date-from').value;
    const to=document.querySelector('#history-date-to').value;
    const rows=[...history.tasks,...history.benchmarks].filter(r=>(kind==='all'||r.kind===kind)&&
      (model==='all'||r.model===model)&&(status==='all'||r.status===status)&&
      (completeness==='all'||r.completeness===completeness)&&
      (!from||(runDate(r.run_time)&&runDate(r.run_time)>=from))&&
      (!to||(runDate(r.run_time)&&runDate(r.run_time)<=to))&&
      [r.id,r.dataset].some(v=>String(v||'').toLowerCase().includes(query)));
    const pages=Math.max(1,Math.ceil(rows.length/50));tablePage=Math.min(tablePage,pages-1);
    const selected=rows.slice(tablePage*50,(tablePage+1)*50);
    document.querySelector('#history-table').innerHTML=`<div class="table-wrap"><table><thead><tr><th>运行</th><th>运行时间</th><th>类型</th><th>数据集</th><th>模型</th><th>状态</th><th>完整度</th><th>范围</th></tr></thead><tbody>${selected.map(r=>`<tr><td><a data-link href="/${r.kind==='task'?'tasks':'benchmarks'}/${esc(r.id)}">${esc(r.id)}</a></td><td>${historyTime(r)}</td><td>${r.kind==='task'?'单任务':'批量'}</td><td>${show(r.dataset)}</td><td>${show(r.model)}</td><td>${badge(r.status)}</td><td>${badge(r.completeness)}</td><td>${r.completed!==undefined?`${r.completed} / ${r.total??'?'}`:'—'}</td></tr>`).join('')}</tbody></table></div><div class="pager"><button id="prev" class="button ghost" ${tablePage===0?'disabled':''}>上一页</button><span>${tablePage+1} / ${pages}</span><button id="next" class="button ghost" ${tablePage>=pages-1?'disabled':''}>下一页</button></div>`;
    document.querySelector('#prev').onclick=()=>{tablePage--;draw();};document.querySelector('#next').onclick=()=>{tablePage++;draw();};bindLinks();
  };
  document.querySelector('#history-search').oninput=()=>{tablePage=0;draw();};
  for(const id of ['kind','model','status','completeness','date-from','date-to'])
    document.querySelector(`#history-${id}`).onchange=()=>{tablePage=0;draw();};draw();
}

async function comparePage() {
  history=await api('/api/history');const options=history.benchmarks.filter(x=>x.completeness!=='unreadable');
  app.innerHTML=`${heading('结果对比','按 problem_id 配对；主结论仅基于双方共同完成且可评测的题目。')}
    <form id="compare-form" class="card"><div class="form-grid"><label>报告 A<select name="a_id">${options.map(x=>`<option value="${esc(x.id)}">${esc(x.dataset)} · ${esc(x.model)} · ${esc(x.id)}</option>`).join('')}</select></label><label>报告 B<select name="b_id">${options.map((x,i)=>`<option value="${esc(x.id)}" ${i===1?'selected':''}>${esc(x.dataset)} · ${esc(x.model)} · ${esc(x.id)}</option>`).join('')}</select></label><button class="button primary" type="submit">比较</button></div></form><div id="comparison" class="section"></div>`;
  document.querySelector('#compare-form').onsubmit=async event=>{
    event.preventDefault();const form=event.target;
    try{
      const data=await api('/api/comparisons',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({a_id:form.elements.a_id.value,b_id:form.elements.b_id.value})});
      document.querySelector('#comparison').innerHTML=`${data.provisional?'<div class="notice">至少一份报告未完成，此对比为临时结果。</div>':''}
        ${data.configuration_differences.length?`<div class="notice">配置差异：${data.configuration_differences.map(d=>esc(d.field)).join('、')}。数值不可直接归因为单一因素。</div>`:''}
        <div class="mini-grid" style="margin-top:16px">${[['共同可评测',data.scope.common_evaluable],['FAIL → PASS',data.counts.fail_to_pass],['PASS → FAIL',data.counts.pass_to_fail],['不变',data.counts.unchanged]].map(([k,v])=>`<div class="card"><div class="stat-label">${k}</div><div class="stat">${v}</div></div>`).join('')}</div>
        <section class="section card"><h2>题目变化</h2><p class="hint">A 已记录 ${data.scope.a_completed}；B 已记录 ${data.scope.b_completed}；仅 A ${data.scope.only_a.length}；仅 B ${data.scope.only_b.length}；共同异常 ${data.scope.excluded_common.length}。</p>
        <div id="comparison-table"></div></section>`;
      let page=0;
      const draw=()=>{
        const pages=Math.max(1,Math.ceil(data.changes.length/30));
        const selected=data.changes.slice(page*30,(page+1)*30);
        document.querySelector('#comparison-table').innerHTML=`<div class="table-wrap"><table><thead><tr><th>Problem</th><th>变化</th><th>A 状态</th><th>B 状态</th><th>A 修复</th><th>B 修复</th><th>证据</th></tr></thead><tbody>${selected.map(r=>`<tr><td>${esc(r.problem_id)}</td><td>${badge(r.change)}</td><td>${badge(r.a.status)}</td><td>${badge(r.b.status)}</td><td>${show(r.a.repair_attempts)}</td><td>${show(r.b.repair_attempts)}</td><td>${r.a.task_id?`<a data-link href="/tasks/${esc(r.a.task_id)}">A</a>`:''} ${r.b.task_id?`<a data-link href="/tasks/${esc(r.b.task_id)}">B</a>`:''}</td></tr>`).join('')}</tbody></table></div><div class="pager"><button class="button ghost" id="comparison-prev" ${page===0?'disabled':''}>上一页</button><span>${page+1} / ${pages}</span><button class="button ghost" id="comparison-next" ${page>=pages-1?'disabled':''}>下一页</button></div>`;
        document.querySelector('#comparison-prev').onclick=()=>{page--;draw();};
        document.querySelector('#comparison-next').onclick=()=>{page++;draw();};bindLinks();
      };draw();
    }catch(error){document.querySelector('#comparison').innerHTML=`<div class="notice error">${esc(error.message)}</div>`;}
  };
}

async function settingsPage() {
  caps=await api('/api/capabilities');
  app.innerHTML=`${heading('设置与环境','以下是后端当前读取到的配置；密钥内容不会出现在浏览器。')}
    <div class="grid"><div class="card"><h2>模型配置</h2>${Object.entries(caps.profiles||{}).map(([name,item])=>`<p>${badge(item.configured?'可用':'未配置')} ${esc(name)} · ${show(item.model)} · ${show(item.base_url||'默认 endpoint')}</p>`).join('')}<p class="hint">切换配置会同时切换服务端 API 地址、密钥和默认模型；密钥正文不返回网页。</p></div>
    <div class="card"><h2>环境与数据集</h2><p>${badge(caps.docker?.available?'Docker 可用':'Docker 不可用')} ${esc(caps.docker?.reason||'')}</p>${Object.entries(caps.datasets).map(([name,item])=>`<p>${badge(item.available?'可用':'缺失')} ${esc(name)} · ${item.cases.length} 题</p>`).join('')}<p class="hint">浏览历史不消耗额度；静态检查和功能验证要求运行网页服务的进程能够执行 Docker。</p></div></div>
    <section class="section card"><h2>协议边界</h2><p>Oracle：仅 Verifier 可见。模型调用配置固定 temperature、top_p；seed 是否发送由当前 Provider 能力决定。</p><a href="/api/openapi.json">查看 API schema</a></section>`;
}

async function render(resetScroll=true) {
  const version=++renderVersion;
  if(pageController)pageController.abort();
  pageController=new AbortController();
  closeStream();
  const path=location.pathname;
  crumb.textContent='正在加载';
  app.innerHTML='<div class="loading">正在读取运行记录…</div>';
  try {
    if(path==='/'||path==='/tasks/new') await newTask();
    else if(path==='/benchmarks/new') await newBenchmark();
    else if(path==='/history') await historyPage();
    else if(path==='/compare') await comparePage();
    else if(path==='/settings') await settingsPage();
    else if(/^\/tasks\/[A-Za-z0-9_-]+$/.test(path)) await taskDetail(path.split('/')[2]);
    else if(/^\/benchmarks\/[A-Za-z0-9_-]+$/.test(path)) await benchmarkDetail(path.split('/')[2]);
    else app.innerHTML=heading('页面不存在');
    if(version!==renderVersion)return;
    setConnection('后端连接正常');
  } catch(error) {
    if(version!==renderVersion||error.name==='AbortError')return;
    app.innerHTML=errorView(error);setConnection('后端不可用或数据损坏',false);
  }
  if(version!==renderVersion)return;
  bindLinks(); if(resetScroll)window.scrollTo(0,0);
}
window.addEventListener('popstate',()=>render());
render();
