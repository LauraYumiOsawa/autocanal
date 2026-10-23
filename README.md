# autocanal

Criação e publicação automática de vídeos no YouTube (Plano 1: núcleo + `short`).

## Instalação
1. `uv sync`
2. ffmpeg/ffprobe em `vendor/bin/`; Docker para o Kokoro (`docker compose -f servicos/kokoro/docker-compose.yml up -d`).
3. Copie `config/canais.yaml.exemplo` para `config/canais.yaml` e `config/segredos.env.exemplo` para `config/segredos.env`; ajuste.
4. Testes: `uv run pytest -q`.

## Checklist de Laura (ações fora do sistema)
1. Criar uma conta Google pessoal (não a corporativa) e dois canais de marca.
2. Verificar o telefone dos canais (miniaturas personalizadas e vídeos > 15 min).
3. Google Cloud: ativar YouTube Data API v3, YouTube Analytics API e Cloud Text-to-Speech; tela de consentimento em **In production**; adicionar `http://localhost:8080/` em URIs de redirecionamento autorizados do client; criar chave de API (TTS e pesquisa).
4. Conta de faturamento no projeto (exigida pelo TTS, mesmo na camada grátis) — ou optar só pelo Kokoro.
5. Contas Pexels e Pixabay → chaves de API em `config/segredos.env`.
6. Rodar `autocanal auth conteudo` e `autocanal auth musica` (login único por canal).
7. Enviar o formulário de auditoria da YouTube API (necessário para vídeos públicos).
8. Confirmar e rodar `autocanal instalar-timer`.

## Primeiro uso
1. `uv run autocanal diagnostico`
2. `uv run autocanal auth conteudo` (e `musica`)
3. `uv run autocanal planejar`
4. `uv run autocanal tick` (o timer: `uv run autocanal instalar-timer --ativar`)

Para uma prova local sem enviar nada: `autocanal novo teste short "tema"` e `autocanal produzir <job>` (o canal `teste` tem grade vazia). Veja `CLAUDE.md` para as regras de segurança.
