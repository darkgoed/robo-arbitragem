"""Research requirements are not an executable live-trading switch."""

from robo.store import now_ms


def live_gate(store):
    with store.connect() as db:
        hot = db.execute("SELECT MIN(ts),MAX(ts) FROM snapshots").fetchone()
        cold = db.execute("SELECT MIN(first_ts),MAX(last_ts) FROM snapshot_archives").fetchone()
    starts = [x for x in [hot[0], cold[0]] if x is not None]
    ends = [x for x in [hot[1], cold[1]] if x is not None]
    span = (max(ends) - min(starts)) / 86400000 if starts and ends else 0
    return {
        "live": False,
        "real_execution": "NOT_IMPLEMENTED",
        "auto_open": False,
        "at": now_ms(),
        "criteria": [
            {
                "id": "prospective_30d",
                "label": "30 dias prospectivos de qualidade / multi-regimes",
                "state": "NOT_VERIFIED",
                "observed_span_days": span,
                "required_days": 30,
                "note": "Span não prova continuidade; lacunas e cobertura por mercado precisam de auditoria.",
            },
            {
                "id": "causal_oos",
                "label": "Replays causais OOS, datasets separados e múltiplas tentativas",
                "state": "NOT_VERIFIED",
            },
            {
                "id": "paper_14d",
                "label": "14 dias de paper contínuo / hedge e ledger sem falhas não mitigadas",
                "state": "NOT_VERIFIED",
                "required_days": 14,
            },
            {
                "id": "net_edge_stress",
                "label": "Expectativa líquida positiva, drawdown/caudas e stress toleráveis",
                "state": "NOT_VERIFIED",
            },
            {
                "id": "legal_operational",
                "label": "Viabilidade legal/termos Brasil, mínimos e margem adequados",
                "state": "NOT_VERIFIED",
                "note": "Acessibilidade pública da API não comprova elegibilidade individual.",
            },
            {
                "id": "explicit_approval",
                "label": "Aprovação explícita do líder técnico para futura implementação real",
                "state": "NOT_GRANTED",
            },
        ],
        "notice": "Funding/candles retrospectivos não substituem L2 prospectivo. Nenhum critério habilita ordens reais neste código.",
    }
