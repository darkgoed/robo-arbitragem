# Pesquisa técnica e riscos — 2026-10-25

## Evidência e escolha
Probes GET reais no host (evidence/public-probes.json): Bybit/Bitget/Binance/Gate 200; OKX 403. Escopo congelado em Bybit + Bitget, BTC/ETH/SOL contra USDT, spot e perpetual linear. Acessibilidade técnica não comprova elegibilidade jurídica do usuário. Nenhuma credencial necessária. Respostas e timestamps são persistidos, nunca sintetizados.

Fontes consultadas diretamente com urllib (extrator web indisponível; nenhuma configuração global alterada):
- Bybit instrumentos https://bybit-exchange.github.io/docs/v5/market/instrument — fundingInterval em minutos, qtyStep/minNotional, Trading; spot usa basePrecision/minOrderAmt.
- Bybit books https://bybit-exchange.github.io/docs/v5/market/orderbook — bid/ask e volume base, ts, u/seq, cts quando disponível; book REST não é garantia de fill.
- Bybit ticker https://bybit-exchange.github.io/docs/v5/market/tickers — fundingRate estimado para próximo evento; nextFundingTime. Não confundir com realizado.
- Bybit histórico https://bybit-exchange.github.io/docs/v5/market/history-fund-rate — fundingRateTimestamp de liquidação; limite 200. Não pressupor janela completa.
- Bitget https://www.bitget.com/api-doc/common/intro informa Classic em manutenção e UTA recomendado. Links antigos /contract/market e /spot/market redirecionam à página UTA genérica: NÃO tratados como prova do contrato. Probes V2 públicos reais em evidence/api-discovery.json verificaram campos, timestamps, fundingRateInterval/fundInterval em horas, sizeMultiplier em unidades base e takerFeeRate público. Adaptação isolada testada contra capturas; migração de API é risco aberto.
- CCXT https://docs.ccxt.com/ : unificação disponível; não elimina diferenças de intervalo/precisão. Adotado ccxt==4.5.90 como dependência MIT, após instrução incremental de reutilização; fronteira própria permite somente GET público. Decisão supersede alternativa nativa inicial (ADR002).
- Hummingbot estratégia https://github.com/hummingbot/hummingbot/blob/master/scripts/v2_funding_rate_arb.py e LICENSE (Apache-2.0, Copyright 2023 Hummingbot Foundation) consultados em raw.githubusercontent.com. Padrões estudados: normalização temporal e contabilização de funding/executors. Código próprio, sem copiar executores financeiros. Estratégia existente comprova comoditização, não edge.
- CVM https://www.gov.br/cvm/pt-br/acesso-a-informacao-cvm/perguntas-frequentes-da-cvm/criptoativos-quando-se-aplicam-as recuperada. Derivativos/valores mobiliários exigem avaliação regulatória. A referência BCB 520 do plano não é tomada como autorização operacional; parecer jurídico/tributário e elegibilidade continuam pré-requisitos externos de qualquer versão futura real.
- Deflated Sharpe Ratio https://doi.org/10.2139/ssrn.2460551 retornou 403; não alegamos implementação nem validação dessa estatística.

## Estratégias confrontadas
Cash-and-carry spot long/perp short: benchmark MVP. Funding positivo remunera short; negativo é débito. Basis atual NÃO é lucro realizado nem convergência certa (perp não vence). Subtrair quatro taxas taker, roundtrip bid/ask e slippage por profundidade, buffers de preço/latência/margem e rebalance/FX. Sem borrow em long spot pago + short 100% colateralizado, mas existe risco de liquidação isolada da short.
Perp-perp cross-venue: diferença de funding normalizada por hora é apenas observação. Não qualificar como negociável: requer margem prefundada nas duas venues, liquidação independente e custo/tempo de transferência não resolvidos.
Spot cross-venue: transferência não atômica e prefunding; triangular: três books sincronizados e mínimos. Não implementados para não fingir executabilidade sem dados completos; sem vantagem provada sobre benchmark.

## Modelo conservador
Horizon 24h: conta eventos agendados discretos (não rate*24/interval fracionário). Crédito futuro com haircut 50%, débitos integralmente. Janela realizada histórica é contexto, nunca previsão causal para um evento anterior à coleta. Net = funding projetado - (roundtrip loss real dos 4 VWAP + 4 fees + drift buffer 20bp + rebalance/FX buffer 10bp). Basis move projetado = ZERO; basis exibido separado. Taker Bybit premissa conservadora spot 10bp/perp 6bp (não taxa personalizada verificada); Bitget usa taxa pública no instrumento e piso conservador. Sem descontos maker/VIP/rebates.
Quantidade comum arredondada para baixo pelo MMC decimal de lotes. Capital reserva spot + short com 100% colateral + buffers. Só rótulo elegível-paper quando validado: minQty/minNotional, volume, status, idade <=15s, relógio <=2s futuro, skew books <=3s, ticker/metadata válidos, funding schedule e intervalo, outlier e margem. Todos os demais são observações com motivos explícitos. Mesmo elegível-paper não promete execução real.

## Honestidade experimental
Histórico funding não contém book/mark histórico completo: replay de funding é estudo por notional fixo, não backtest de lucro. Backtest executável só usa snapshots coletados prospectivamente, entrada anterior ao settlement e mark próximo ao evento. Sem amostras suficientes => INSUFFICIENT_DATA, zero promessa e nenhum Sharpe inventado. No-trade como baseline. ML não promovido sem walk-forward/OOS, purga e ganho líquido convincente. Sem treino pesado neste host.
R$5 e operações R$0,30/R$0,50: cenário educacional separado, FX público USD/BRL diário indicado, USDT/USD=1 é hipótese de conversão não garantia. Mínimos e custo de garantia tornam tamanhos frequentemente inviáveis. Capital maior virtual USDT 1000, sem relação com patrimônio real.
