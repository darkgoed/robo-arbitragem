# Experimento autônomo R$5 — virtual e separado

Saldo inicial/baseline:5BRL. Passos pedidos:0,30/0,50BRL. Não há compra, venda ou transferência física nem aumento para atingir mínimos. O monitor roda a cada5min no coletor existente, registra18observações por janela com PASS/REJECT, horários, fontes, mínimo, custo4estágios, funding projetado, ROI24h hipotético e motivos.

CenárioA: usa notional convertido por USD/BRL diário público (USDT/USD=1 é hipótese). Lote é arredondado PARA BAIXO. Se abaixo de mínimo/lote/prefunding: INEXEQUÍVEL NA CORRETORA. O custo de uma fração ignorando lotes pode ser calculado SOMENTE com rótulo HIPOTÉTICA INEXEQUÍVEL; nenhuma liquidação, fill ou PnL realizado dela chega ao saldo. Sem dados/FX válido: DATA_UNAVAILABLE e valoresnulos, não zeros inventados. Baseline permanece5 ewin-rateNULL por não haver fills, não95%.

CenárioB: MMC decimal dos lotes; menor quantidade comum que passa minQty/minNotional em AMBAS pernas. Ceil é apenas recomendação do mínimo, não alteração da operação BRL5. Exige books/depth/metadata frescos, calcula spot pago +short100% de garantia +fees4execuções +spread/VWAP +20bpdrift/latência +10bprebalance/FX +1%buffer de margem. Mínimo é aproximado e datado. O buffer de margem é capital reservado, não custo realizado. Net não ganha basis hipotético.

PASS de hipótese exige net positivo, mínimos/qualidade e compatibilidade com capital virtual disponível e limite400USDT da conta de pesquisa; não autoriza LIVE nem abre posição. AUTO_OPEN=OFF e não há controle remoto para ligá-lo nesta versão. Paper manual existente continua em conta separada1000USDT; seus ganhos/perdas não alteramBRL5.

Persistência: monitor_runs, janela UTC única, gzip+SHA256 de decisões e dos snapshots originais utilizados, totais atualizados na mesma transação. Reiniciar não duplica janela e não inventa janelas passadas.35dias de registros, lifetime counters separados. CSV resumo da última janela e JSON auditável privado porwindow. Disponibilidade = fontes frescas/esperadas NAS JANELAS observadas; não é uptime financeiro ou taxa de operações vencedoras. Gaps e cobertura prospectiva são outra medida.

Consulta: /api/monitor, /api/monitor/{window}, /api/live-gate, /api/data/export?kind=decisions, todos autenticados. Snapshot antigo continua pesquisável mas após15s NÃO é sinal atual executável. A UI declara a idade/expiração. Sem custo estimável, exibe indisponível. Nenhum gráfico obriga operações.

Riscos: funding variável/inverte, posição short pode liquidar independente do spot, custódia, depegUSDT, latência/spread, mínimos que mudam, jurisdição. Nenhuma promessa de lucro nem aprovação legal. Comparação datada e decomposição em docs/COST_COMPARISON_V02.md e evidence/phase2-minimum-capital.json.
