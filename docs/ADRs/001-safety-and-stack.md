# ADR 001 — runtime de pesquisa isolado

Decisão: Python 3.12, FastAPI, SQLite WAL, JS/CSS próprios e SVG; dois adaptadores REST nativos Bybit V5 e Bitget V2. Sem CCXT no runtime: reduz dependências e torna impossível chamar métodos de trading por engano. Transporte permite apenas GET em allowlist de host/path de mercado. Sem código LIVE, credenciais ou endpoints financeiros.

Servidor 127.0.0.1:3215, uma instância, cgroup CPU 50%, RAM 384 MiB. Coleta a cada 30s, concorrência máxima 3, timeout 8s, retry limitado com jitter, históricos a cada hora. WAL, retenção 7 dias de snapshots, 90 dias funding, raw gzip com SHA256 e orçamento 256 MiB. Metadados públicos por hora.

UI sem CDN: CSP self, cookies HttpOnly SameSite=Strict, Secure somente no HTTPS (localhost HTTP explícito), sessões revogáveis em memória 8h, autenticação por senha PBKDF2. CSRF por Origin e token de sessão em toda mutação autenticada. Segredos gerados em secrets/ 0700/0600, nunca impressos. Apenas /health/live e tela de login são públicos. /health/ready requer auth.

Os critérios P2/P3/P4/P5 do plano autorizam as fronteiras de teste sem nova entrevista. A pesquisa é executada no parent, não em worker, conforme solicitação DarkGate de manter risco alto no Sol. Não tocar outros sistemas; vhost fica em deploy/ enquanto DNS for NXDOMAIN.
