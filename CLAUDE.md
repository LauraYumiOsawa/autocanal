# autocanal

App local em Python que cria e publica vídeos no YouTube automaticamente (canais `conteudo` e `musica`; Plano 1: núcleo + `short`). O Claude (via `claude -p`) escreve roteiros e revisa; Kokoro/Google TTS narram; ffmpeg monta; o publicador envia como privado com `publishAt`.

## Documentos
- Spec: `docs/superpowers/specs/2026-10-22-autocanal-design.md`
- Planos: `docs/superpowers/plans/`
- Ledger do SDD (andamento das tarefas): `.superpowers/sdd/2026-10-23-plano1-nucleo-short-youtube/progress.md`

## Regras de segurança (obrigatórias)
- NUNCA publicar (`autocanal publicar`), vetar (`vetar`), autenticar (`auth`) nem rodar `instalar-timer --ativar` sem pedido explícito de Laura.
- NUNCA imprimir, registrar ou commitar segredos: `config/client_secret.json`, `config/segredos.env`, `dados/tokens/`.
- Nada em `dados/` vai para o git.
- Testes: `uv run pytest -q` (os marcados `real` ficam de fora por padrão).

## Comandos da CLI (`uv run autocanal ...`)
- `diagnostico`: confere ferramentas, credenciais e chaves.
- `status`: jobs por estado, alertas e próximos 10 jobs.
- `novo <canal> <formato> <tema> [--quando "AAAA-MM-DD HH:MM"]`: cria um job.
- `planejar`: o Claude sugere temas para os slots livres da grade.
- `produzir <job>`: roteiro, narração, vídeo e revisão; refaz `falhou`/`quarentena`.
- `revisar <job>`, `aprovar <job>`, `descartar <job>`: ações sobre um job.
- `tick`: um passo do agendador (o timer roda a cada 15 min).
- `publicar`, `vetar`, `auth`, `instalar-timer`: ver regras acima.

## Comandos do projeto (`.claude/commands/`)
`/fila`, `/novo-video <canal> <formato> <tema>`, `/quarentena`.
