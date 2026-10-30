status=READY_FOR_REVIEW

Expansão PAPER ONLY · 28/10/2026

Entrega de engenharia verificada, incluindo o escopo ampliado dos 104 IDs CCXT.
Release inicial 0.3.0/e86229678cc1e9ecfba07d8d5c5c29db84485477 publicada e verificada:
evidence/expansion-deploy.json, produção PAPER_ONLY íntegra, APIs anônimas 401,
cinco novas fontes 8/8, ciclos legacy continuando e contas preservadas.
O inventário dos 104 IDs foi concluído e auditado separadamente, conforme os números abaixo.
Nenhuma ordem, API key de exchange, depósito, saque, transferência ou capital real.

Implementação e provas já obtidas
  Baseline original Bybit/Bitget × BTC/ETH/SOL preservado; novo banco discovery separado.
  Cinco novas fontes públicas: OKX, KuCoin, Binance, BingX e Gate, oito ativos cada:
  BTC, ETH, SOL, XRP, DOGE, ADA, LINK, AVAX. 40 combinações fonte/ativo observadas,
  com um book spot e um linear perp cada. Observação não é viabilidade nem confiabilidade histórica.
  Gate voltou a responder nos probes reais; USDT linear integrado, BTC inverso deliberadamente proibido.
  Kraken Futures: probe real 200 em PF_XBTUSD; não integrado, pois quote/settlement USD e
  equivalência/financiamento não qualificados. Não equiparar USD e USDT para criar pares.

  1.080 contrafactuais por ciclo: A=120, B=480, C=480, incluindo três cenários virtuais
  R$100/R$1.000/R$5.000. Não são operações nem observações independentes.
  API autenticada de configuração permite 1–5 cenários, entre R$5 e R$10.000,
  somente para ciclos seguintes. Não modifica contas ou ledger.

Qualidade e limitações
  Binance: spot não tem timestamp de origem; A/B rejeitados, sem horário local substituto.
  KuCoin: timePoint não comprovado como frescura da cotação; funding rejeitado.
  BingX: updateTime real pode estar no evento anterior; rejeitado se antigo.
  Gate: books reais com timestamps, contratos normalizados; funding sem as-of verificável.
  OKX: usa info.ts para funding; não inventa mark/index ou mínimo notional.
  Taker CCXT/tabelas públicas + pisos conservadores são modelos, não taxas da conta.
  A contabiliza quatro taxas, spread/profundidade, stress 0,3%, reserva 1%, haircut 50%
  em um evento. B bloqueia inventário/rebalance desconhecidos. C usa as agendas por perna,
  haircut só sobre recebimentos (pagamentos integrais) e quatro taxas; margem/liquidação
  não verificadas bloqueiam qualificação mesmo quando houver projeção positiva.
  BRL: câmbio diário público original, <48h; USDT=USD é hipótese não executável.

Preservação / recuperação
  Backup online reaberto e integrity_check=ok em data/expansion-backups/20261028T194118Z/.
  282 arquivos L2 frios originais copiados, SHA-256 e contagem conferidos.
  evidence/expansion-preservation.json confirma 17.568 snapshots hot originais presentes
  na união hot+cold, arquivos frios intactos, linhas antigas completas de histórico,
  paper exatamente inalterado (999.304521645952 USDT).
  Retenção nova: seis ciclos hot, gzip integral + SHA-256 + readback antes de remover linhas hot;
  cold sem expiração automática, teto 256MiB, parada fail-closed antes da quota.
  Escritores discovery recebem SQLite max_page_count=16384 (64MiB) por conexão.
  journal_size_limit=4MiB é alvo de retenção após checkpoint, não teto instantâneo de WAL.

QA final executado
  122 testes pytest passaram; fixtures sintéticas rotuladas e restritas a tests/.
  Ruff (incluindo regras S), compileall e sintaxe JS verificados em rodadas anteriores.
  Browser E2E real em staging isolado: login/sessão, três estratégias, cenários, filtros,
  auditoria, histórico, logout, desktop e mobile390 sem overflow.
  Rollback ensaiado: candidato 0.3.0 -> baseline 76199d9/0.2.0 -> candidato 0.3.0,
  com o mesmo banco e senha de TESTE; paper preservado e os seis mercados legacy
  observados frescos. Pico candidato ~274MiB (evidência exata no JSON).
  Nenhuma senha/credencial de produção lida, rotacionada ou substituída.
  Semgrep não instalado; não alegar execução. Gate local: Ruff S, AST sem chamadas de
  conta/ordem/transferência, testes de host/path exato/credenciais/redirects/POST/429/5xx.

Evidências
  evidence/expansion-checkpoint.json
  evidence/expansion-probes.json e expansion-source-notes.json
  evidence/expansion-browser.json e expansion-screenshots/
  evidence/expansion-rollback-rehearsal.json
  evidence/expansion-preservation.json
  evidence/expansion-gates.json (PASS final, com 122 testes e captura de produção)
  evidence/expansion-deploy.json (backup/restore, restart somente do serviço próprio, readback)
  evidence/expansion-production-browser.json e expansion-final-live.json (PASS)
  evidence/venue-survey/validated-summary.json (104 relatórios + raw verificados)
  docs/EXPANSAO_CCXT_104_AUDITORIA.md (matriz completa de resultados e impedimentos)

Inventário integral CCXT 4.5.90
  104 IDs auditados, sem itens restantes. São IDs de adaptadores, não 104 empresas
  independentes: variantes regionais/derivativos e aliases podem compartilhar infraestrutura.
  84 IDs tiveram tentativa de acesso à rede; 20 foram bloqueados antes dela por gate
  de capacidade/segurança. Bloqueio conservador não prova indisponibilidade da corretora.
  57 IDs retornaram amostras observadas para pesquisa, inclusive dados incompletos;
  47 IDs foram rejeitados nesta rodada. HTTP 200 nunca é aprovação financeira.
  261 GETs de mercado; 245 arquivos raw distintos gzip com SHA-256/readback verificados.
  Armazenamento raw: 3.188.755 bytes. Pico do subprocesso: 158.628 KiB.
  Execução sequencial isolada, limites por ID de 16 GET/12MiB/32s; nenhum worker contínuo
  adicional ficou rodando. Checkpoint por ID preservado, sem repetir probes já concluídos.

  Onze amostras de dez IDs passaram o gate conservador de dados no instante da coleta:
  aster/BTC spot, bitget/BTC spot, bitrue/BTC spot, bybit/BTC spot e perp,
  digifinex/ETH spot, kucoin/BTC spot, mexc/BTC spot, poloniex/BTC spot,
  whitebit/BTC spot e woo/BTC perp. Este PASS de dados não é elegibilidade operacional,
  confiabilidade histórica, independência estatística ou lucro.
  Na rodada nenhum ID novo teve AMBAS as pernas integralmente qualificadas e sincronizadas.
  Não se criou adapter contínuo genérico para contornar as faltas. Zero admissões contínuas
  adicionais após a release inicial. As sete fontes contínuas de observação continuam:
  Bybit/Bitget legacy + OKX/KuCoin/Binance/BingX/Gate da release 0.3.0.
  As cinco fontes novas da release cobrem 40 combinações fonte/ativo; dados sem qualidade
  permanecem REJECT, não são promovidos porque estão sendo coletados.

  Exemplos de impedimentos auditados: APIs privadas/auth de Alpaca bloqueadas; protocolos
  públicos POST de CEX/Hyperliquid/GRVT bloqueados pela exigência GET-only; namespaces
  não revisados bloqueados sem inferir segurança pelo nome; books sem timestamp,
  tabelas de fees/mínimos ausentes, funding sem as-of/intervalo, mercados em outra quote,
  timeout/HTTP e catálogos grandes. Cada ID tem seus paths negados e respostas reais no JSON.
  Binance/Gate podem falhar no probe genérico mais restrito, enquanto seus adapters
  específicos, limitados e já validados seguem coletando; não confundir as duas aprovações.

Validação de produção após os 104 IDs
  0.3.0@e86229678cc1e9ecfba07d8d5c5c29db84485477 responde PAPER_ONLY,
  real_trading=NOT_IMPLEMENTED. APIs privadas anônimas 401; seis mercados legacy
  observados frescos, cinco fontes novas 8/8. NRestarts=0, serviço active/success.
  1.080 contrafactuais atuais: A=120, B=480, C=480; zero hipóteses qualificadas,
  zero fills e zero PnL real. Nenhum critério foi relaxado para fabricar oportunidade.
  17.568 snapshots hot originais preservados na união hot+cold, 282 arquivos frios
  originais preservados e 306 arquivos frios atuais verificados. 105.408 candles e
  2.484 registros históricos de funding preservados, paper legacy exatamente igual.
  O inventário novo é ferramenta isolada, não importada pelo app em produção;
  não exige novo restart só para publicar relatórios. Release executada: e862296.
  Commit local final registra esta auditoria; nenhum remote Git está configurado.

Reprodução / continuidade de pesquisa (não são gates pendentes desta entrega)
  Ler STATUS.md e git status. Para validar sem repetir rede dos 104 IDs:
  PYTHONPATH=. .venv/bin/python scripts/venue-survey-report.py.
  Para retomar uma interrupção: scripts/venue-survey.py --batch 104 pula IDs já salvos.
  Para investigar uma fonte rejeitada, preservar o relatório original e criar nova rodada;
  não liberar POST/PRIVATE ou cadastro de chaves para obter aprovação.
  Captura das fontes promovidas: PYTHONPATH=. .venv/bin/python scripts/discover.py.
  Gates: PYTHONPATH=. .venv/bin/python scripts/expansion-gates.py.
  Não usar scripts antigos de restore genérico. Nenhum push possível sem remote.
  Publicação exclusivamente por restart de robo-arbitrage.service, com rollback dos
  arquivos de entrada/VERSION para 76199d9 se a verificação de produção falhar.
  Jamais restaurar DB de produção por cima do atual apenas para voltar código.
