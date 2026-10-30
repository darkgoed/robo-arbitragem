# LIVE = NÃO — critérios formais de pesquisa

Nenhum estado deste documento/API habilita ordens reais. Nenhum dinheiro real, chave financeira, leverage ou endpoint de ordens foi acrescentado. AUTO_OPEN paper permaneceOFF.

| Critério cumulativo | Evidência exigida | Situação desta entrega |
|---|---|---|
|30dias prospectivos de qualidade/multi-regimes|L2 observado, metadados, hashes, cobertura/gaps por mercado e arquivos retidos|NÃO VALIDADO: poucas horas; capacidade35d não é30d observados|
|Replay causal fora da amostra|Separação temporal, embargo/purga quando necessário, custos, tentativas contabilizadas|NÃO VALIDADO; funding/candles retrospectivos são descritivos|
|14dias de paper contínuo|Fills virtualmente possíveis, hedges/ledger reconciliados e falhas mitigadas|NÃO VALIDADO; não confundir NO_TRADE BRL5 com14dias de fills|
|Edge líquido/stress|Expectativa positiva, drawdown/caudas toleráveis e custos pessimistas com liquidez|NÃO VALIDADO; sem edge demonstrado|
|Legal e operacional|Termos futures aplicáveis ao usuário no Brasil, CVM/BCB, mínimos, saldo, custódia/margem|NÃO VALIDADO; acesso à API pública não comprova elegibilidade|
|Aprovação explícita|Nova decisão do líder técnico antes de qualquer implementação real|NÃO CONCEDIDA; decisão atual proíbe dinheiro real|

Esses não são prazos para rentabilidade. Se nenhum edge for confirmado, continuar coleta e NO_TRADE. API /api/live-gate privada retorna somente critérios e medições; não contém endpoint de ativação.

Fontes primárias consultadas em evidence/phase2-sources.json: Bybit documentação funding e instrumentos; Bitgetintro/REST público verificado; CVM https://www.gov.br/cvm/pt-br/acesso-a-informacao-cvm/perguntas-frequentes-da-cvm/criptoativos-quando-se-aplicam-as . Página BCBResolução520 em https://www.bcb.gov.br/estabilidadefinanceira/exibenormativo?tipo=Resolu%C3%A7%C3%A3o%20BCB&numero=520 retornou shellJS, portanto NÃO se declara texto integral/eligibilidade validada. Revisão normativa/tributária/termos é pendência real, não superada por pesquisa técnica.
