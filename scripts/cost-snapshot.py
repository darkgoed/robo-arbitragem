"""Dated real metadata comparison, not a current executable offer."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from robo.store import Store, now_ms
from robo.monitor import minimum_capital

ROOT = Path(__file__).resolve().parents[1]
store = Store(ROOT / "data")
fx = store.get("fx")
rows = []
for venue in ["bybit", "bitget"]:
    for symbol in ["BTCUSDT", "ETHUSDT", "SOLUSDT"]:
        q = store.snapshots(venue, symbol, 1)[0]
        # Evaluate at the ORIGINAL collection time and show it, never claim it is fresh now.
        result = minimum_capital(q, fx, q["collected_at"])
        rows.append(
            result
            | {
                "quote_asof": q["collected_at"],
                "computed_at": now_ms(),
                "kind": "DATED_SNAPSHOT_NOT_CURRENT_EXECUTION",
            }
        )
report = {
    "at": now_ms(),
    "fx": fx,
    "rows": rows,
    "warning": "USD/BRL daily indication; USDT/USD=1 assumed; not a binding quote or legal eligibility",
}
(ROOT / "evidence/phase2-minimum-capital.json").write_text(json.dumps(report, indent=2))
text = "# Comparação de capital mínimo — snapshot público\n\nGerado automaticamente por scripts/cost-snapshot.py, sem arredondar ordens BRL5 para cima. Valores abaixo são estimativas datadas, NÃO capital garantido de execução, autorização regulatória ou recomendação de depósito. Prefunding: spot pago + short100% + quatro fees, spread/depth, drift/FX e buffer de margem1%.\n\n| Corretora | Par | Coleta UTC ms | Quantidade comum | USDT total | BRL indicativo | Edge aprovado? |\n|---|---|---|---|---|---|---|\n"
for r in rows:
    usd = "indisponível" if r["capital_usdt"] is None else f"{r['capital_usdt']:.6f}"
    brl = "indisponível" if r["capital_brl"] is None else f"{r['capital_brl']:.6f}"
    text += f"| {r['venue']} | {r['symbol']} | {r['quote_asof']} | {r['quantity']} | {usd} | {brl} | NÃO; ver net e razões |\n"
text += "\nCada payload integral, metadata/fee source, custos por etapa e timestamps estão em evidence/phase2-minimum-capital.json. Taxas são públicas/pisos conservadores não personalizados, não comprovam taxas históricas. Câmbio stale ou book inválido impede comparação em BRL.\n\nR$0,30/R$0,50 e totalR$5 não pagam as duas pernas mínimas neste snapshot. O scanner não inventa fills fracionários; qualquer conta matemática é HIPOTÉTICA INEXEQUÍVEL e não altera saldo.\n\nFontes oficiais: https://bybit-exchange.github.io/docs/v5/market/instrument ; https://www.bitget.com/api-doc/common/intro ; probes REST reais nos metadados BybitV5 / BitgetV2 via CCXT4.5.90. Links Bitget legados redirecionam à UTA, não são tomados como validação documental de todo contrato V2. Acesso público não prova elegibilidade Brasil. CVM/BCB e termos individuais permanecem gate NÃO VALIDADO.\n"
(ROOT / "docs/COST_COMPARISON_V02.md").write_text(text)
print(
    json.dumps(
        [
            {k: r[k] for k in ["venue", "symbol", "quantity", "capital_usdt", "capital_brl", "quote_asof"]}
            for r in rows
        ]
    )
)
