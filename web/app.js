'use strict';
const $ = id => document.getElementById(id);
let csrf = '', state = null, busy = false, selectedWindow = 'latest';
const fmt = (v, d = 4) => v === null || v === undefined || !Number.isFinite(Number(v)) ? '—' : Number(v).toLocaleString('pt-BR', {minimumFractionDigits: d, maximumFractionDigits: d});
const date = v => v ? new Date(v).toLocaleString('pt-BR', {timeZoneName:'short'}) : '—';
const reasons = {NET_NONPOSITIVE:'Custos superam funding estimado', FUNDING_NONPOSITIVE:'Funding nulo ou negativo', STALE_BOOK:'Book antigo', FUTURE_CLOCK:'Relógio futuro', UNSYNCED_BOOKS:'Books sem sincronia', STALE_METADATA:'Metadados antigos', MIN_NOTIONAL:'Abaixo do mínimo financeiro', BELOW_LOT:'Abaixo do lote', LOT_LIMIT:'Limite de quantidade', INVALID_FUNDING:'Funding inválido', INSUFFICIENT_DEPTH:'Profundidade insuficiente', INSUFFICIENT_PREFUNDING:'R$ 5 não cobrem o capital mínimo das duas pernas', FX_UNAVAILABLE:'Câmbio indisponível ou antigo', NO_DATA:'Sem dados de mercado', SCHEMA_ERROR:'Falha de integridade/esquema', INSUFFICIENT_SHADOW_CAPITAL:'Saldo R$ 1.000 não cobre o mínimo', MINIMUM_CAPITAL_UNAVAILABLE:'Capital mínimo indisponível', NO_VALID_QUOTE:'Dados de mercado inválidos ou ausentes', INSUFFICIENT_RESEARCH_CAPITAL:'Capital virtual de pesquisa insuficiente', PAPER_NOTIONAL_LIMIT:'Acima do limite notional do paper', PAPER_HALTED:'Entradas paper pausadas', BASIS_OUTLIER:'Basis fora do limite'};
const node = (tag, text, cls) => { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (cls) n.className = cls; return n; };
function moneyClass(n, val) { n.className = val > 0 ? 'positive' : val < 0 ? 'negative' : ''; }
function error(e) { $('error').textContent = String(e.message || e); $('error').hidden = false; }
async function api(path, data) {
  const options = {credentials:'same-origin', headers:{}};
  if (data !== undefined) { options.method='POST'; options.headers={'Content-Type':'application/json', 'X-CSRF-Token':csrf}; options.body=JSON.stringify(data); }
  const r = await fetch(path, options);
  const result = await r.json();
  if (!r.ok) { if (r.status===401) showLogin(); throw new Error(typeof result.detail === 'string' ? result.detail : JSON.stringify(result.detail)); }
  return result;
}
function showLogin() { csrf=''; $('login').hidden=false; $('workspace').hidden=true; }
function showWorkspace() { $('login').hidden=true; $('workspace').hidden=false; }
function td(tr, value, cls) { const n=node('td',value,cls);tr.append(n); return n; }
function detail(o) {
  const dialog=node('dialog', undefined, 'panel'); dialog.setAttribute('aria-label','Estimativa detalhada');
  dialog.append(node('h2',o.symbol+' · '+o.venue), node('p','Projeção 24h, não garantia. Basis não contabilizado como lucro.'));
  for (const [k,v] of Object.entries({quantity:o.quantity, capital_required:o.capital_required, gross_funding:o.gross_funding, ...o.prefunding, ...o.costs, net_usdt:o.net_usdt, net_apr_extrapolated_pct:o.net_apr_on_capital_pct})) {
    if(v && typeof v === 'object'){for(const [stage,cost] of Object.entries(v)){const row=node('div',undefined,'row');row.append(node('span','Taxa / '+stage),node('code',fmt(cost,6)));dialog.append(row);}}else{const row=node('div',undefined,'row'); row.append(node('span',k),node('code',fmt(v,6))); dialog.append(row);}
  }
  dialog.append(node('p','Próximo evento: '+date(o.next_funding)),node('p',o.risk));
  const close=node('button','Fechar'); close.onclick=()=>{dialog.close();dialog.remove();}; dialog.append(close); document.body.append(dialog); dialog.showModal();
  dialog.addEventListener('close',()=>dialog.remove(),{once:true});
}
function render(s) {
  state=s;
  $('clock').textContent=date(s.now);
  $('sync').textContent='Última coleta: '+date(s.last_cycle);
  const fresh=s.providers.reduce((n,p)=>n+p.fresh_symbols,0);
  $('fresh').textContent=fresh+' / 6'; $('eligible').textContent=s.opportunities.filter(o=>o.eligible).length;
  $('equity').textContent=fmt(s.paper.equity_mtm,2);
  const pnl=s.paper.balance-s.paper.initial;
  $('pnl').textContent=fmt(pnl,4);moneyClass($('pnl'),pnl);
  $('providers').replaceChildren();
  for(const p of s.providers){const card=node('div',undefined,'provider');card.append(node('b',p.venue.toUpperCase()),node('span',p.state,p.state==='LIVE_DATA'?'ok':'warn'),node('small',p.fresh_symbols+'/3 frescos · latência '+fmt(p.latency_ms,0)+' ms · erros de transporte '+fmt(p.error_rate*100,1)+'%'));if(p.last_error)card.append(node('small',p.last_error));$('providers').append(card);}
  $('opportunities').replaceChildren();$('scanner-empty').hidden=s.opportunities.length>0;
  for(const o of s.opportunities){const tr=node('tr'); const name=td(tr,o.symbol);name.append(node('small',o.venue+' · spot/perp público'));td(tr,fmt(o.funding_rate == null ? null : o.funding_rate*100,4)+'% / '+o.interval_h+'h');td(tr,fmt(o.basis_pct,4)+'%');td(tr,fmt(o.gross_funding));td(tr,fmt(o.costs.total));const net=td(tr,fmt(o.net_usdt));moneyClass(net,o.net_usdt);td(tr,fmt(o.age_s,1)+'s',o.age_s>15?'warn':'ok');const decision=td(tr,'');const b=node('button',o.eligible?'ELEGÍVEL-PAPER':'OBSERVAÇÃO');b.onclick=()=>detail(o);decision.append(b,node('small',o.reasons.map(r=>reasons[r]||r).join(' · ')));$('opportunities').append(tr);}
  $('cross').replaceChildren();for(const o of s.cross_venue){const row=node('div',undefined,'row');row.append(node('span',o.symbol),node('code',fmt(o.hourly_rate_diff*100,5)+'%/h'+(o.stale?' · STALE':'')));$('cross').append(row);}if(!s.cross_venue.length)$('cross').append(node('p','NO DATA'));
  if(s.fx){$('fx').textContent='USD/BRL '+fmt(s.fx.usd_brl,4)+' · '+date(s.fx.asof)+' · '+s.fx.warning; const eligible=s.educational_sizes.filter(x=>x.eligible).length;$('education').textContent=s.educational_sizes.length?'R$ 0,30 / R$ 0,50: '+eligible+' de '+s.educational_sizes.length+' cenários elegíveis; mínimos/custos e qualidade se aplicam.':'FX indisponível ou antigo; cenário não calculado.';}
  $('paper-summary').textContent='Caixa virtual: '+fmt(s.paper.balance,4)+' USDT · Reservado: '+fmt(s.paper.reserved,4)+' · MTM top-of-book (sem fees de saída): '+fmt(s.paper.unrealized_mtm,4)+(s.paper.stale_exposure.length?' · Exposição sem marca fresca: '+s.paper.stale_exposure.length:'');
  $('halt').textContent=s.paper.halted?'Retomar entradas paper':'Pausar entradas';
  $('positions').replaceChildren();for(const p of [...s.paper.positions].reverse()){const tr=node('tr');const first=td(tr,p.symbol);first.append(node('small',p.venue+' · '+p.id.slice(0,8)+(p.research_override?' · sem edge / educacional':'')));const status=td(tr,p.status);if(p.pending_funding.length)status.append(node('small','Funding pendente de mark: '+p.pending_funding.length));td(tr,fmt(p.quantity,6));td(tr,fmt(p.entry_spot,3)+' / '+fmt(p.entry_perp,3));td(tr,fmt(p.funding_pnl,6));td(tr,fmt(p.realized_pnl,6));const control=td(tr,'');if(['OPEN','RESIDUAL_SPOT'].includes(p.status)){const close=node('button','Fechar paper');close.onclick=()=>action(()=>api('/api/paper/close/'+p.id,{}));control.append(close);}else control.textContent=p.close_reason||p.scenario;$('positions').append(tr);}if(!s.paper.positions.length){const tr=node('tr');const cell=td(tr,'Nenhuma posição virtual. Não operar também é uma decisão.');cell.colSpan=7;$('positions').append(tr);}
  $('movements').replaceChildren();for(const m of [...s.paper.ledger].slice(-40).reverse())$('movements').append(node('div',date(m.ts)+' · '+m.kind+' · '+m.debit+' ← '+m.credit+' · '+fmt(m.amount,6)+' USDT virtuais'));
  $('budget').textContent='DB '+fmt(s.budget.db_bytes/1048576,2)+' MiB · raw '+fmt(s.budget.raw_bytes/1048576,2)+' MiB';
}
async function chart(){const h=await api('/api/history/'+$('chart-market').value);const svg=$('chart');svg.replaceChildren();const rows=h.snapshots;const ns='http://www.w3.org/2000/svg';if(rows.length<2){const text=document.createElementNS(ns,'text');text.setAttribute('x','20');text.setAttribute('y','85');text.textContent='Aguardando ao menos 2 snapshots reais.';svg.append(text);}else{const min=Math.min(...rows.map(r=>r.basis_pct)),max=Math.max(...rows.map(r=>r.basis_pct));const t0=rows[0].ts,t1=rows.at(-1).ts;let segment=[];const draw=()=>{if(segment.length){const line=document.createElementNS(ns,'polyline');line.setAttribute('points',segment.join(' '));svg.append(line);segment=[];}};rows.forEach((r,i)=>{if(i && r.ts-rows[i-1].ts>90000)draw();segment.push((15+(r.ts-t0)/(t1-t0||1)*610)+','+(160-(r.basis_pct-min)/(max-min||1)*135));});draw();const text=document.createElementNS(ns,'text');text.setAttribute('x','20');text.setAttribute('y','20');text.textContent=fmt(min,4)+'% a '+fmt(max,4)+'%';svg.append(text);}
$('chart-label').textContent=rows.length+' snapshots · '+(rows.length?date(rows[0].ts)+' → '+date(rows.at(-1).ts):'NO DATA')+' · '+h.settlements.length+' funding settlements históricos';}
async function experiments(){const list=await api('/api/experiments');$('experiments').replaceChildren();for(const r of list){const d=node('div',undefined,'experiment');d.append(node('b',r.venue+' / '+r.symbol+' · '+r.status),node('p',r.reason||''),node('p',r.snapshot_count+' snapshots · '+r.funding_study.n+' eventos retrospectivos · replay PnL '+fmt(r.paper_net_pnl,4)+' · baseline 0 USDT'),node('code','dataset SHA-256 '+r.dataset_sha256),node('small','Versão '+r.version+' · trial '+r.config.trial+' · seed '+r.seed+' · ML não promovido'));$('experiments').append(d);}if(!list.length)$('experiments').append(node('p','Sem experimentos. Avaliar o dataset não cria dados ausentes.'));}
async function refresh(){if(busy||!csrf)return;busy=true;try{render(await api('/api/overview'));const tasks=[chart(),experiments()];if(typeof monitorPanel==='function')tasks.push(monitorPanel(),coveragePanel(),researchPanel());await Promise.all(tasks);}catch(e){error(e);}finally{busy=false;}}
async function action(fn){$('error').hidden=true;try{await fn();await refresh();}catch(e){error(e);}}
$('login-form').onsubmit=async e=>{e.preventDefault();const form=new FormData(e.target);const button=e.target.querySelector('button');button.disabled=true;try{const r=await api('/auth/login',Object.fromEntries(form));csrf=r.csrf;e.target.reset();showWorkspace();await refresh();}catch(err){$('login-error').textContent=err.message;}finally{button.disabled=false;}};
$('paper-form').onsubmit=e=>{e.preventDefault();const f=new FormData(e.target);action(()=>api('/api/paper/open',{venue:f.get('venue'),symbol:f.get('symbol'),notional:Number(f.get('notional')),scenario:f.get('scenario'),research:f.has('research'),request_id:crypto.randomUUID()}));};
$('halt').onclick=()=>action(()=>api('/api/paper/halt',{halted:!state.paper.halted}));
$('logout').onclick=()=>action(async()=>{await api('/auth/logout',{});showLogin();});
$('run-research').onclick=()=>{const [venue,symbol]=$('chart-market').value.split('/');action(()=>api('/api/experiments',{venue,symbol,notional:100}));};
$('refresh').onclick=refresh;$('chart-market').onchange=()=>chart().catch(error);
setInterval(()=>{if(!document.hidden)refresh();},5000);
(async()=>{try{const s=await api('/auth/session');csrf=s.csrf;showWorkspace();await refresh();}catch{showLogin();}})();
