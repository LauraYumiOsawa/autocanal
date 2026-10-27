# autocanal

App local em Python que cria e publica vídeos no YouTube automaticamente (formatos `short` e `medio`). O Claude (via `claude -p`) escreve roteiros, faz a curadoria dos visuais do Pixabay e revisa; o FLUX.2 [klein] 4B (projeto `servicos/flux`, em lote) gera as imagens que faltam; Kokoro/Google TTS narram; ffmpeg monta; o médio ganha capítulos e thumbnail; o publicador envia como privado com `publishAt`.

## Documentos
- Spec: `docs/superpowers/specs/2026-10-22-autocanal-design.md` e, para médio/visuais/FLUX, `docs/superpowers/specs/2026-10-23-plano2-medio-flux-design.md`
- Planos: `docs/superpowers/plans/`
- Ledgers (andamento das tarefas): `.superpowers/sdd/<plano>/progress.md`

## Regras de segurança (obrigatórias)
- NUNCA publicar (`autocanal publicar`), vetar (`vetar`), autenticar (`auth`) nem rodar `instalar-timer --ativar` sem pedido explícito de Laura.
- NUNCA imprimir, registrar ou commitar segredos: `config/client_secret.json`, `config/segredos.env`, `dados/tokens/`.
- Nada em `dados/` vai para o git.
- O mais importante do vídeo é o visual: todo visual mostra o que está sendo falado (curadoria ou FLUX); vídeo com fundo procedural é reprovado.
- Testes: `uv run pytest -q` (os marcados `real` ficam de fora por padrão).

## Comandos da CLI (`uv run autocanal ...`)
- `diagnostico`: confere ferramentas, credenciais e chaves.
- `status`: jobs por estado, alertas e próximos 10 jobs.
- `novo <canal> <formato> <tema> [--quando "AAAA-MM-DD HH:MM"]`: cria um job.
- `planejar`: o Claude sugere temas para os slots livres da grade.
- `produzir <job>`: roteiro, narração, legendas, visuais (curadoria + FLUX), vídeo, metadados, thumbnail (médio) e revisão; refaz `falhou`/`quarentena`.
- `revisar <job>`, `aprovar <job>`, `descartar <job>`: ações sobre um job.
- `tick`: um passo do agendador (o timer roda a cada 15 min).
- `bancada rodar <job> <roteiro|metadados|revisao> --rotulo <texto> [--modelo M] [--esforco E] [--primeira-versao]`: repete a etapa numa cópia do job em `dados/bancada/` e mede os tokens. Cada execução gasta cota do plano. `--primeira-versao` mede o roteiro como primeira versão, não como refação.
- `bancada relatorio`: médias por etapa e rótulo.
- `publicar`, `vetar`, `auth`, `instalar-timer`: ver regras acima.

## Comandos do projeto (`.claude/commands/`)
`/fila`, `/novo-video <canal> <formato> <tema>`, `/quarentena`.
