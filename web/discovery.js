'use strict';
// Uses the existing same-origin session and CSRF wrapper. No exchange credentials.
(() => {
  let data = null, loading = false, lastLoad = 0;
  const el = id => document.getElementById('discovery-' + id);
  const option = (value, text) => {const n = document.createElement('option'); n.value = value; n.textContent = text; return n;};
  const amount = v => v === null || v === undefined ? 'UNKNOWN' : fmt(v, 3);
  function options(select, values, label) {
    const selected = select.value;
    select.replaceChildren(...values.map(v => option(v, label(v))));
    if (values.map(String).includes(selected)) select.value = selected;
  }
  function audit(decision) {
    const dialog = node('dialog', undefined, 'panel');
    dialog.setAttribute('aria-label', 'Auditoria da expansão');
    dialog.append(node('h2', 'Snapshot observado · cálculo contrafactual'));
    const pre = node('pre', JSON.stringify({run_id: data.id, fx: data.fx, ...decision}, null, 2));
    pre.className = 'table-wrap';
    dialog.append(pre);
    const close = node('button', 'Fechar'); close.onclick = () => dialog.close();
    dialog.append(close); dialog.addEventListener('close', () => dialog.remove(), {once:true});
    document.body.append(dialog); dialog.showModal();
  }
  function render() {
    if (!data) return;
    const rows = (data.decisions || []).filter(d => d.budget_brl === Number(el('budget').value)
      && (!el('venue').value || d.venue === el('venue').value)
      && (!el('base').value || d.base === el('base').value) && d.strategy === el('strategy').value);
    el('decisions').replaceChildren();
    for (const d of rows) {
      const e = d.estimate || {}, tr = node('tr');
      td(tr, d.strategy + ' · ' + d.venue + (d.other_venue ? ' → ' + d.other_venue : ''));
      td(tr, d.base); td(tr, amount(e.capital_required_brl)); td(tr, amount(e.gross_spread_usdt));
      const spread = e.spread_depth_usdt ?? 0, buffer = e.latency_rebalance_buffer_usdt ?? e.buffer_usdt;
      td(tr, e.fees_usdt === undefined || buffer === undefined ? 'UNKNOWN' : amount(e.fees_usdt + spread + buffer));
      const net = e.net_one_event_usdt ?? e.net_24h_usdt ?? e.net_before_unknown_rebalance_usdt;
      td(tr, amount(net));
      td(tr, d.status + ' · ' + d.reasons.join(', '), d.status === 'REJECT' ? 'negative' : '');
      const button = node('button', 'Ver cálculo'); button.onclick = () => audit(d); td(tr, '').append(button);
      el('decisions').append(tr);
    }
    el('count').textContent = rows.length + ' contrafactuais filtrados; snapshot vencido para execução. Net B exclui custo desconhecido de rebalanceamento.';
  }
  async function load() {
    if (loading || document.getElementById('workspace').hidden) return;
    loading = true;
    try {
      const selected = el('window').value;
      const [overview, history] = await Promise.all([api('/api/discovery?budget=' + encodeURIComponent(el('budget').value)), api('/api/discovery/history')]);
      options(el('window'), ['latest', ...history.runs.map(r => r.id)], id => id === 'latest' ? 'Última' : date(history.runs.find(r => r.id === id).ts));
      el('window').value = [...el('window').options].some(o => o.value === selected) ? selected : 'latest';
      data = el('window').value === 'latest' ? overview : await api('/api/discovery/history/' + encodeURIComponent(el('window').value));
      if (!data.ready && !data.id) {
        el('status').textContent = 'Sem ciclo completo. ' + (overview.worker?.last_error || 'Aguardando fontes reais.');
        el('decisions').replaceChildren(); return;
      }
      options(el('budget'), data.scenarios_brl, v => 'R$ ' + fmt(v, 0));
      const venues = overview.venues || Object.keys(data.health || {}), bases = overview.bases || [];
      options(el('venue'), ['', ...venues], v => v || 'Todas');
      options(el('base'), ['', ...bases], v => v || 'Todos');
      el('status').textContent = 'Coleta: ' + date(data.asof || data.finished) + ' · snapshot vencido para execução · 0 fills / 0 PnL real.' + (overview.worker?.last_error ? ' Worker: ' + overview.worker.last_error : '');
      el('health').replaceChildren();
      for (const [venue, h] of Object.entries(data.health || {})) {
        el('health').append(node('article', venue + ': ' + h.state + ' · ' + h.pairs + '/8 observados · HTTP ' + h.requests + ' · erros ' + h.errors + ' · latência média ' + h.latency_mean_ms + 'ms' + (h.error ? ' · ' + h.error : ''), 'panel'));
      }
      el('export').href = '/api/discovery/history/' + data.id;
      render(); lastLoad = Date.now();
    } catch (e) { el('status').textContent = 'Scanner indisponível: ' + e.message; el('decisions').replaceChildren(); }
    finally { loading = false; lastLoad = Date.now(); }
  }
  el('config').onsubmit = async event => {
    event.preventDefault();
    try {
      const values = el('config-values').value.split(',').map(v => Number(v.trim()));
      const response = await api('/api/discovery/scenarios', {budgets_brl: values});
      el('config-status').textContent = 'Configurados: ' + response.budgets_brl.join(', ') + ' BRL. Aplicação no próximo ciclo; nenhum saldo alterado.';
    } catch (e) { el('config-status').textContent = e.message; }
  };
  for (const id of ['venue', 'base', 'strategy']) el(id).addEventListener('change', render);
  for (const id of ['budget', 'window']) el(id).addEventListener('change', load);
  document.getElementById('refresh').addEventListener('click', load);
  setInterval(() => { if (!document.hidden && Date.now() - lastLoad > 30000) load(); }, 1000);
})();
