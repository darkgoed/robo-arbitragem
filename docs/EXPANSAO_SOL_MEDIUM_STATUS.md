status=READY_FOR_REVIEW

Escopo exclusivo: /opt/robo-arbitragem. PAPER ONLY, sem credenciais de exchanges, ordens, depósitos, saques ou capital real.

P0 — INVENTÁRIO / PRESERVAÇÃO: concluído.
  git main em 76199d9, quatro arquivos não rastreados originais (plano + três rascunhos).
  Rascunhos integralmente examinados; não aprovados como implementação final.
  Backup ONLINE: data/expansion-backups/20261028T194118Z/research.sqlite3.
  integrity_check na cópia reaberta: ok. 282 arquivos L2 frios copiados, SHA-256 e contagem verificados.
  Inventário e estado anterior: evidence/expansion-checkpoint.json.
  Produção ativa; 512 MiB máximo, 50% CPU. Baseline não alterado.

P1 — FONTES / PARES: concluído. OKX/KuCoin/Binance/BingX/Gate × 8 ativos, 40 combinações reais observadas. Horário spot ausente na Binance não é preenchido; funding sem as-of é rejeitado. Kraken sondado e não integrado por quote/financiamento não qualificados.
P2 — ESTRATÉGIAS / CENÁRIOS: concluído. A/B/C separados; R$100/R$1000/R$5000 e configuração autenticada, 1.080 contrafactuais por ciclo, 0 fills/PnL real. Inventário/rebalance/margem desconhecidos bloqueiam viabilidade.
P3 — API / UI / HISTÓRICO: concluído. Sessões/CSRF existentes, filtros, cálculo auditável, histórico hot/cold integral, mobile390 sem overflow. Quotas nos escritores e arquivos frios verificados antes da compactação hot.
P4 — QA / E2E REAL / DEPLOY inicial: concluído para a release e86229678cc1e9ecfba07d8d5c5c29db84485477 (0.3.0). 98 testes, E2E real autenticado, rollback isolado e deploy/readback de produção PASS. Cinco novas fontes 8/8, seis mercados legacy frescos e pelo menos dois ciclos novos em cada um; paper, R$5 e shadow R$1000 intactos. evidence/expansion-deploy.json. Browser anônimo de produção PASS, sem ler senha. Serviço íntegro, NRestarts=0.

P5 — ESCOPO ATUALIZADO / TODOS OS 104 IDs CCXT: CONCLUÍDO E AUDITADO.
  CCXT 4.5.90 confirmado com exatamente 104 IDs. Inventário: evidence/venue-survey/inventory.json.
  Auditoria isolada, um subprocesso por vez, teto 16 GET/12MiB/32s por ID, CPU e memória limitadas;
  nenhuma chamada com namespace privado, credencial, POST, redirecionamento ou host desconhecido.
  Relatórios por ID e checkpoint retomável: evidence/venue-survey/.
  Raw público comprimido/hash/readback: data/venue-survey/, quota global 192MiB.
  104/104 IDs, zero restantes; 84 com tentativa de rede e 20 bloqueados antes dela.
  57 com observações de pesquisa e 47 rejeitados nesta rodada; 261 GETs e 245 raws verificados.
  Onze amostras de dez IDs passaram o gate de snapshot; nenhum ID novo teve ambas as
  pernas integralmente qualificadas/sincronizadas. Zero novas admissões contínuas nesta rodada.
  Não promover fontes com base num único probe; faltas de qualidade e impedimentos registrados.
  Relatório: docs/EXPANSAO_CCXT_104_AUDITORIA.md e evidence/venue-survey/validated-summary.json.
  122 testes finais PASS; Ruff, compile, JS, integridade e preservação PASS.
  Produção revalidada após a auditoria: seis mercados legacy frescos, cinco fontes novas
  8/8, contas intactas, APIs privadas 401, serviço ativo/success, NRestarts=0.
  evidence/expansion-final-live.json. O inventário é offline/isolado e não requer restart.
  Comando de retomada: PYTHONPATH=. .venv/bin/python scripts/venue-survey.py --batch 104.
  Os relatórios já salvos são preservados e pulados; consultar checkpoint.json para totais reais.

Checkpoint: o backup acima inclui rascunhos originais em drafts/. Nenhum segredo lido ou alterado.
Retomada: consultar este arquivo, git status e evidence/expansion-checkpoint.json; não executar scripts antigos de deploy/backup que apagam backups ou fazem restore genérico.
