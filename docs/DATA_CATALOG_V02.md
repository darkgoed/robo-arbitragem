# Catálogo histórico / procedência — v0.2

Ingestão REAL em evidence/phase2-backfill.json, reimport real em phase2-backfill-reimport.json (zero novas linhas). Não confundir fixtures emtests/ com datasets. Checksums e fontes porarquivo/página estão noSQLite e nas evidências.

| Dataset | Origem | Resultado verificado inicial | Semântica |
|---|---|---|---|
|Bybit funding BTC/ETH/SOL|api.bybit.com/v5/market/funding/history viaCCXT4.5.90|540eventos porpar,3páginas de200, recorte alvo180dias|Rates realizados por settlement; não previsão|
|Bitget funding BTC/ETH/SOL|api.bitget.com/api/v2/mix/market/history-fund-rate viaCCXT|270eventos porpar,3páginas até limite observado~90dias|Limite de fonte documentado, não inventar180dias|
|Binance spot eUSD-M perp BTC/ETH/SOL5m|data.binance.vision/data/{spot|futures/um}/monthly/klines|12ZIPs de2026-08/09,105408candles, hashes iguais a.CHECKSUM oficiais|OHLCV, NÃO bid/ask, mark oficial Bybit ou book/fill|
|L2 prospectivo Bybit/Bitget|mesmos adaptadores públicos do runtime|contagem/range efetivos naAPIcoverage eSQLite; horas, não30dias|até50níveis de cada lado efetivamente observados, timestamps econstraints salvos|

Funding total inicial2430; candle105408. Ranges exatosUTCms e contagens porseries estão no relatório, não arredondados para prometer janela inteira. Paginação backward endTimeBybit/pageNoBitget, máximo8requisições porcontinuação, checkpoint porpar e idempotência de chave. Reimport verificou novamente os12checksums, inseriu0funding/0candles. Revisões conflitantes não são silenciosamente sobrescritas.

Binance: spot>=2025timestampsµs (documentação oficial github.com/binance/binance-public-data), convertidos paraUTCms; futuresUM ms mantidos. Segundos rejeitados, datas futuras/relaçõesOHLCV/duplicados/formato/limites validados, ZIP único CSV de nome esperado, sem extração,4MiB comprimido/32MiB expandido/10000linhas porarquivo. Arquivos baixados sequencialmente, não manterGBs deZIPs; row-levelsource/hash preservados. PeakRSS do primeiro backfill156964KiB e47,57s medidos, sem instalar runner ou bibliotecas novas.

Rawfunding normalizado temhash, URL e timestampobserved; data defunding é tempo do evento e nunca da coleta. Novos eventos do coletor atualizam também historical_funding. Candles são backfill manual explícito, sem daemon novo nem download descontrolado. Retrospectivos mantidos até730dias.

## Consulta/qualidade
/api/data/coverage privado: contagens/ranges, gapscandles>5min e diferenças funding vs intervalo modalobservado. Não assumir que o intervalo histórico foi sempre8h: podem existir mudanças de agenda. Ausências são reportadas, não interpoladas.

A amostra do audit inicial teve1299snapshots/600funding do cache antigo, gaps>90s emtodos6mercados, maior~429s e nenhuma anomalia dequality no momento da coleta naquelafatia. Lacunas são reais; backfill de funding/candles não as preenche emL2.

Arquivo L2 lossless: chunks até500, SHA256, readback integral antes deDELETE, manifesto indexado paraqueryintervalo. Hot~24h; cold35dias,768MiB e pin paraexposições/pendências. Projeção de compressão~485MiB/35dias veio de amostra real, não garantia universal. Se exceder budget, statusARCHIVE_BUDGET_BLOCKED e fontes mantidas; exigir mitigação antes de aprovar pesquisa. DB512MiB e rawHTTP32MiB. Apenas registros maisantigos que35dias e não pinados expiram.

## O que NÃO está disponível
Não háL2 histórico completo anterior ao início da coleta, historical executable quotes ou invoices históricos de funding/margem. Não sintetizar livros a partir de candles. Tardis e feeds terceiros premium/contas não instalados: opcionais futuros e sujeitos a licença/custo/coverage; não necessários aos datasets oficiais destaentrega. Não replicar Nautilus/Hummingbot/QuantsLab completos nestaVPS.

## Experimento descritivo honesto
/api/data/stress/{venue}/{symbol}: soma de funding pornotionalfixo100USDT, splitcronológico70/30 SEM tuning, sensibilidade fee-only32/52bpou taxas atuais de metadata (e1,5x/2x), hipótese de roundtrip diário. Edge datesUTCpodem serparciais; não éOOS de execução nemPnL. Não inclui preço dehedge/spread/liq/slippage/borrow históricos. BaselineNO_TRADE0. ML não treinado/promovido, DSR/Sharpe não inventados. Dados históricos não estabelecem ganho líquido validado.
