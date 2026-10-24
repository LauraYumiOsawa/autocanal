# autocanal

Criação e publicação automática de vídeos no YouTube: `short` (9:16, 30–60 s) e `medio` (16:9, 1–5 min, com capítulos e thumbnail).

## Instalação
1. `uv sync`
2. ffmpeg/ffprobe em `vendor/bin/`; Docker para o Kokoro (`docker compose -f servicos/kokoro/docker-compose.yml up -d`).
3. Copie `config/canais.yaml.exemplo` para `config/canais.yaml` e `config/segredos.env.exemplo` para `config/segredos.env`; ajuste.
4. Testes: `uv run pytest -q`.
5. FLUX (opcional, gera as imagens que o Pixabay não cobre): `uv sync --project servicos/flux` e baixar os pesos de `black-forest-labs/FLUX.2-klein-4B` (Apache 2.0, ~16 GB) **fora da rede da empresa** (o firewall intercepta o huggingface.co). Precisa de GPU com 8 GB.

## Visuais
O mais importante do vídeo é o visual: cada trecho (uma frase) precisa mostrar o que está sendo falado.
- `visuais` do canal define a ordem das fontes. Com `pixabay`, o sistema busca até 6 candidatos por trecho (vídeos primeiro), monta uma folha de contato e o Claude escolhe o que combina com a fala (ou nenhum). Com `flux`, o que sobrar é gerado localmente a partir da cena descrita no roteiro.
- `estilo_visual` (`realista` ou `desenho`) vale para o vídeo inteiro: filtra o Pixabay (filmagem/foto ou animação/ilustração) e define o estilo do FLUX.
- O fundo procedural só existe para a etapa não quebrar: vídeo com ele é reprovado na revisão.
- Vídeo com imagem do FLUX sobe marcado como conteúdo sintético; a descrição credita o Pixabay e avisa sobre IA.

## Checklist de Laura (ações fora do sistema)
1. Criar uma conta Google pessoal (não a corporativa) e dois canais de marca.
2. Verificar o telefone dos canais em youtube.com/verify (miniaturas personalizadas do `medio` e vídeos > 15 min) e definir o país do canal (Brasil) no YouTube Studio.
3. Google Cloud: ativar YouTube Data API v3, YouTube Analytics API e Cloud Text-to-Speech; tela de consentimento em **In production**; adicionar `http://localhost:8080/` (e `http://localhost:8090/` se a 8080 estiver ocupada: `autocanal auth <canal> --porta 8090`) em URIs de redirecionamento autorizados do client; criar chave de API (TTS e pesquisa) e, em **Restrições de API**, incluir a **Cloud Text-to-Speech API** (sem isso o Google responde `API_KEY_SERVICE_BLOCKED` e a narração cai no Kokoro).
4. Conta de faturamento no projeto (exigida pelo TTS, mesmo na camada grátis) — ou optar só pelo Kokoro.
5. Conta Pixabay → chave de API em `config/segredos.env` (**obrigatória** para visuais de qualidade: sem ela tudo vira imagem procedural, a revisão tende a reprovar e o resumo diário alerta).
6. Rodar `autocanal auth conteudo` e `autocanal auth musica` (login único por canal).
7. Enviar o formulário de auditoria da YouTube API (necessário para vídeos públicos).
8. Confirmar e rodar `autocanal instalar-timer`. **Não habilite o timer antes de a auditoria ser aprovada**: uploads de projetos não auditados ficam presos em privado (o `status` e o resumo alertam quando um agendado passa 2 h do horário sem ficar público).

## Primeiro uso
1. `uv run autocanal diagnostico`
2. `uv run autocanal auth conteudo` (e `musica`)
3. `uv run autocanal planejar`
4. `uv run autocanal tick` (o timer: `uv run autocanal instalar-timer --ativar` — só depois de confirmar a auditoria aprovada e um `tick` manual sem erros)
5. Para o timer do usuário rodar sem sessão aberta (após reiniciar a máquina): `loginctl enable-linger $USER`. O serviço já leva `~/.local/bin` no `PATH`; o `claude.bin` da configuração é resolvido para caminho absoluto ao carregar.

Para uma prova local sem enviar nada: `autocanal novo teste short "tema"` (ou `medio`) e `autocanal produzir <job>` (o canal `teste` tem grade vazia). Veja `CLAUDE.md` para as regras de segurança.
