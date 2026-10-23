# autocanal — design

Data: 2026-10-22 · Solicitante: Laura (laura.osawa@jacto.com.br) · Status: aprovado por delegação

> **Sobre as aprovações.** As decisões das seções 1 (arquitetura) e da abordagem A foram aprovadas
> explicitamente. Para o restante, Laura delegou: "tome suas próprias decisões como o recomendado
> por você mesmo até as 6AM". Tudo abaixo segue as recomendações apresentadas na conversa. Às 6h
> (2026-10-23) o trabalho pausa para confirmação.

## 1. Objetivo

Um sistema local que **cria e publica vídeos no YouTube automaticamente**, em fila agendada, com o
Claude Code (e o plugin `youtube-agent@youtube-agent-skill`) nas etapas criativas e na revisão.
Objetivo de negócio: **monetização** (entrar no Programa de Parcerias do YouTube o mais rápido possível
de forma sustentável). Restrição: **custo zero** em serviços pagos — o Claude roda no plano atual de
Laura.

### O que Laura disse
- Fila agendada, podendo também acionar o Claude manualmente.
- Automatizar criação **e** postagem, num design único.
- Formatos: **Shorts verticais narrados**, **slides narrados (educativo)**, **músicas de 1–5 min**,
  **vídeos médios de 1–5 min** com temas do que está viral.
- Ainda não tem canal; objetivo é monetização; nicho: "me recomende"; orçamento: só camadas grátis.
- Revisão também automática, feita pelo Claude.
- Volume e horários: configuráveis, com um estudo (branding/estratégia) para indicar o melhor
  volume e horários para monetizar mais rápido.
- Gemini: pode conectar se for preciso. GPT: não.

### O que foi assumido (corrigível)
- Idioma pt-BR, público Brasil.
- Dois canais iniciais (conteúdo e música), em Brand Accounts de uma conta Google **pessoal**.
- Roda no notebook de Laura (Linux, RTX PRO 1000 8 GB, 30 GB RAM, Docker com GPU).

### Critérios de sucesso
1. `autocanal produzir` gera, sem intervenção, um MP4 pronto de cada formato, com metadados e relatório
   de revisão.
2. Após o login OAuth único por canal, uploads acontecem sem intervenção humana, inclusive após
   reinícios, até o token ser revogado (e aí o sistema avisa).
3. Nenhum vídeo é enviado sem passar pela revisão (checagens + Claude); reprovados vão para quarentena.
4. A grade de publicação vem de um estudo de estratégia e se ajusta com dados do YouTube Analytics.

### Fora de escopo
Criação de canais, configuração do Google Cloud Console, cadastro de cartão, auditoria da API — são
ações de Laura, listadas no checklist (seção 14). Vídeos com o rosto de Laura. Qualquer serviço pago.

## 2. Restrições externas (verificadas em 2026-10-22)

| Fato | Consequência no design | Fonte |
|---|---|---|
| Service accounts não publicam em canais | OAuth de usuário, refresh token por canal | docs YouTube Data API |
| Projeto de API não auditado → vídeos travados em privado | Checklist: formulário de auditoria antes de monetizar | docs YouTube Data API |
| Upload custa ~100 unidades (desde 2025-12-04); `videos.insert` e `search.list` têm buckets próprios (desde 2026-06-01) | Cota não limita volume; ainda assim o sistema conta unidades e adia se estourar | revision_history |
| `status.containsSyntheticMedia` aceito em `videos.insert` (desde 2024-10-30) | Preenchido pela revisão | revision_history |
| Consentimento OAuth em modo "Testing" → refresh token expira em 7 dias | Checklist: publicar app ("In production") | docs Google OAuth |
| Client do arquivo é tipo `web`, sem `redirect_uris` | Checklist: cadastrar `http://localhost:8080/` | arquivo local |
| ACE-Step 1.5: MIT, uso comercial das músicas declarado, < 4 GB VRAM | Gerador de música local | model card HF |
| Google Cloud TTS: 1M caracteres/mês grátis (Chirp 3 HD), 4M (Standard/WaveNet); exige conta de faturamento | Provedor TTS principal, com Kokoro (Apache 2.0) de reserva local | página de preços |
| Pixabay: API grátis, uso comercial sem atribuição (Pexels removido em 2026-10-23: pausou chaves novas) | Provedor de imagens/b-roll | licenças |
| Política "conteúdo inautêntico" do Programa de Parcerias | Variação obrigatória + checagem de repetição na revisão | política YPP |

Cada licença e limite é reconferido antes da adoção e registrado em `LICENCAS.md` com link e data.

## 3. Arquitetura

Abordagem escolhida: **app Python local que controla o fluxo; Claude headless chamado nas etapas
criativas e de revisão**.

```
autocanal/
├─ config/
│  ├─ canais.yaml          # canais: nicho, formatos, grade, voz, paleta, idioma, categoria
│  ├─ autocanal.yaml       # global: caminhos, provedores, modelos do Claude, limites
│  ├─ segredos.env         # chaves (Pixabay, Google TTS) — fora do git
│  └─ client_secret.json   # OAuth client — fora do git
├─ dados/                  # fora do git
│  ├─ autocanal.db         # SQLite
│  ├─ tokens/<canal>.json  # refresh tokens (chmod 600)
│  ├─ jobs/<job_id>/       # artefatos de cada vídeo
│  ├─ biblioteca/musica/   # faixas aprovadas (reuso como trilha e em compilações)
│  ├─ estrategia/          # relatórios de nicho, branding, grade
│  └─ relatorios/          # resumo diário (digest)
├─ autocanal/              # pacote Python
│  ├─ cli.py  config.py  db.py  fila.py  agendador.py  claude.py  midia.py  relatorio.py
│  ├─ etapas/              # uma função por etapa
│  ├─ formatos/            # receitas: short, medio, slides, musica, compilacao
│  ├─ provedores/          # tts/, musica/, imagens/ — cada um atrás de uma interface
│  ├─ youtube/             # oauth.py upload.py analytics.py pesquisa.py
│  └─ estrategia/          # nicho.py branding.py grade.py
├─ servicos/               # docker-compose / venvs dos modelos locais (Kokoro, ACE-Step)
├─ .claude/                # CLAUDE.md do projeto + comandos (/fila, /quarentena, /estrategia)
├─ LICENCAS.md
└─ tests/
```

### Princípios
- **Etapas idempotentes com checkpoint.** Cada etapa lê de `jobs/<id>/`, grava sua saída lá e registra
  conclusão no banco. Uma execução interrompida continua da próxima etapa pendente.
- **Um job de GPU por vez** (trava em arquivo). Serviços de modelo sobem sob demanda e são parados
  depois para liberar VRAM.
- **Publicação delegada ao YouTube.** O vídeo sobe como `private` com `publishAt`; o YouTube torna
  público no horário. O notebook só precisa estar ligado para produzir. A fila mantém uma folga
  (`folga_dias`, padrão 3) de vídeos prontos.
- **Formatos são receitas** (lista ordenada de etapas + parâmetros). Provedores são plugáveis por
  configuração — Gemini entra depois como mais um provedor, sem mexer no resto.

## 4. Modelo de dados (SQLite)

- `jobs(id, canal, formato, tema, briefing_json, estado, publish_at, tentativas_revisao,
  youtube_video_id, erro, criado_em, atualizado_em)`
- `etapas(job_id, nome, estado, tentativas, iniciado_em, concluido_em, erro)` — PK `(job_id, nome)`
- `revisoes(job_id, rodada, aprovado, motivos_json, etapa_para_refazer, criado_em)`
- `canais_estado(canal, youtube_channel_id, auth_ok, ultimo_erro)`
- `cota(dia, bucket, unidades)`
- `metricas(youtube_video_id, dia, views, minutos_assistidos, inscritos_ganhos, ctr)`
- `temas(id, canal, formato, tema, referencia_url, multiplo, usado, criado_em)`

Estados de job:

```
planejado → produzindo → em_revisao → aprovado → agendado (enviado, com publishAt) → publicado
                ↑             │
                └─ refazer ───┘ (até 2 rodadas)
em_revisao → quarentena (após 2 reprovações) → [humano: aprovar | descartar | refazer]
qualquer → falhou (erro não transitório após 3 tentativas da etapa)
agendado → vetado (humano, antes do publishAt)
```

## 5. Formatos e etapas

Todas as receitas terminam em `metadados → revisao → upload`.

| Formato | Saída | Etapas antes de metadados |
|---|---|---|
| `short` | 1080×1920, 30–60 s | roteiro → narração → legendas → visuais → render |
| `medio` | 1920×1080, 1–5 min | roteiro → narração → legendas → visuais → render → thumbnail |
| `slides` | 1920×1080, 3–6 min | roteiro_slides → narração (por slide) → slides (render PNG) → render → thumbnail |
| `musica` | 1920×1080, 1–5 min | briefing_musical → gerar_musica → masterizar → capa → render_visualizador → thumbnail |
| `compilacao` | 1920×1080, 30–60 min | selecionar_faixas (da biblioteca) → concatenar → capa → render_visualizador → thumbnail |

### Detalhes por etapa
- **roteiro / roteiro_slides / briefing_musical** — Claude com `youtube-agent:yt-script`. Entrada: tema,
  perfil de voz do canal, os últimos 15 títulos do canal (para variar), formato e duração alvo. Saída
  JSON (schema por formato): gancho escolhido (com nota do `hookscore.py`), segmentos com texto falado
  e descrição visual, **lista de fontes (URLs) para cada afirmação factual** (via WebSearch). Slides:
  lista de slides `{titulo, topicos[], narracao}`. Música: `{titulo, genero, clima, bpm, instrumental,
  letra?, tags_estilo}`.
- **narração** — provedor TTS do canal (`google` → reserva `kokoro`). Um WAV por segmento + um WAV total.
- **legendas** — faster-whisper com timestamps por palavra sobre a narração real; agrupa em 2–4 palavras;
  gera `.ass` com estilo do canal. A transcrição também alimenta a revisão.
- **visuais** — para cada segmento, busca b-roll/foto pela descrição visual (Pixabay) e
  registra origem + licença em `fontes_midia.json`. Reserva sem chave/rede: fundo procedural
  (gradiente + tipografia da paleta do canal). Gerador local (FLUX.2 klein 4B) e Gemini ficam como
  provedores futuros.
- **slides** — Pillow renderiza cada slide em PNG com a paleta e fontes do canal.
- **gerar_musica** — ACE-Step 1.5 (serviço local na GPU). Faixa salva em `dados/biblioteca/musica/`
  ao ser aprovada.
- **masterizar** — ffmpeg `loudnorm` para −14 LUFS, corte de silêncio no início/fim.
- **capa** — arte procedural determinística por seed (formas/gradientes da paleta) + título.
- **render / render_visualizador** — ffmpeg: clipes com movimento (zoompan), concatenação, legendas
  queimadas, trilha de fundo da biblioteca a −20 dB sob a narração, H.264 + AAC, −14 LUFS. Visualizador:
  capa + forma de onda/espectro (`showwaves`/`showfreqs`).
- **thumbnail** — Pillow, 1280×720: imagem-chave + no máximo 3 palavras (do `yt-package`) na paleta.
- **metadados** — Claude com `yt-package` (título + texto da thumb, linter `title.py`), `yt-seo`
  (descrição, tags ≤ 15, 3 consultas-alvo) e `yt-chapters` (quando houver ≥ 3 seções de ≥ 10 s;
  validado por `chapters.py`). Saída JSON com `titulo`, `descricao`, `tags`, `capitulos`, `categoria`.

### Perfil de voz por canal
As skills leem `~/.claude/youtube/voice.md`, que é global. Para suportar vários canais sem sobrescrever o
arquivo pessoal de Laura, o prompt de cada chamada **inclui o perfil de voz do canal** (de
`config/vozes/<canal>.md`) e instrui a skill a usá-lo no lugar do arquivo global e a não fazer perguntas.

## 6. Integração com o Claude

`autocanal/claude.py` expõe `chamar(prompt, schema, ferramentas, modelo, cwd, max_turns) -> dict`:

```
claude -p <prompt> --output-format json --json-schema <schema>
       --allowedTools <lista> --no-session-persistence --max-turns <n> [--model <m>]
```

- Plugin carregado do diretório instalado (`--plugin-dir` quando necessário).
- `cwd` = pasta do job, para que o Claude leia/grave só ali.
- Ferramentas por etapa (mínimo necessário): roteiro → `Skill, Read, Bash(python3:*), WebSearch, WebFetch`;
  metadados → `Skill, Read, Bash(python3:*)`; revisão → `Read, WebFetch, WebSearch, Bash(python3:*)`.
- Valida `structured_output` com o schema localmente; se inválido ou `is_error`, tenta de novo até 2
  vezes; depois marca a etapa como falha.
- Modelo configurável por etapa (`claude.modelos` em `autocanal.yaml`), padrão do plano para roteiro e
  revisão.
- Registra custo equivalente (`total_cost_usd`) e turnos por chamada no banco, para acompanhar consumo
  do plano.

Smoke test realizado em 2026-10-22: `yt-package` + `title.py` em modo headless retornou JSON validado
em ~17 s.

## 7. Revisão automática

Duas camadas, nesta ordem; a segunda só roda se a primeira passar.

**Checagens determinísticas** (`etapas/revisao.py`, via ffprobe e arquivos do job):
duração dentro do alvo do formato; resolução/aspecto; áudio presente; loudness −14 ± 1,5 LUFS;
nenhum silêncio > 2 s (exceto música); sem clipping; legendas cobrindo a narração; capítulos válidos
(`chapters.py`); título com nota ≥ limiar (`title.py`); toda mídia com origem e licença registradas;
similaridade de roteiro (Jaccard de 5-gramas) < 0,35 contra os últimos 30 roteiros do canal.

**Revisor Claude** (prompt de revisor rigoroso, chamada separada do autor). Recebe: roteiro, transcrição
real, 6 frames extraídos, thumbnail, metadados, fontes, últimos 15 títulos do canal. Verifica:
1. Cada afirmação factual contra as fontes citadas (abre os links). Afirmação sem fonte = reprovação.
2. Transcrição bate com o roteiro (TTS não pulou/trocou nada).
3. Valor próprio e variação (risco de conteúdo inautêntico/repetitivo).
4. Título/thumbnail não enganosos; nada de tema sensível ou proibido pelas diretrizes.
5. Decide `containsSyntheticMedia` (true se houver mídia sintética realista).

Saída: `{aprovado, problemas[], etapa_para_refazer, contains_synthetic_media, nota}`.

**Limite conhecido:** o Claude não ouve áudio. Música é revisada só por métricas, metadados e — quando
houver letra — pela transcrição.

**Fluxo:** aprovado → `aprovado`. Reprovado → volta para `etapa_para_refazer` com os problemas no
prompt; após 2 reprovações → `quarentena`.

**Supervisão inicial:** até `revisao.supervisao_ate` (padrão: 14 dias após o primeiro upload), o
resumo diário lista todos os aprovados com link; Laura pode vetar antes do `publishAt`
(`autocanal vetar <job>`). Depois, só a quarentena chega até Laura.

## 8. YouTube: OAuth, upload, pesquisa e Analytics

- **OAuth** (`youtube/oauth.py`): `google-auth-oauthlib` com o client do arquivo, servidor local na
  porta 8080, `access_type=offline`, `prompt=consent`. Escopos: `youtube.upload`, `youtube`,
  `yt-analytics.readonly`. `autocanal auth <canal>` grava `dados/tokens/<canal>.json` (chmod 600),
  confirma o canal via `channels.list(mine=true)` e grava o `youtube_channel_id`. `invalid_grant` →
  `auth_ok=false`, uploads do canal param e o resumo diário avisa.
- **Upload** (`youtube/upload.py`): upload resumável em blocos, retentativa com backoff em 5xx/erros de
  rede; `status.privacyStatus=private`, `publishAt`, `selfDeclaredMadeForKids=false`,
  `containsSyntheticMedia`; `snippet` com título, descrição (+ capítulos), tags, `categoryId`,
  `defaultLanguage`/`defaultAudioLanguage`. Depois `thumbnails.set` (exceto Shorts). Contabiliza cota.
- **Veto** (`autocanal vetar`): `videos.update` mantendo privado e removendo `publishAt`.
- **Pesquisa** (`youtube/pesquisa.py`): `search.list` por consulta do nicho (últimos 30 dias, região BR),
  `videos.list chart=mostPopular`, e uploads recentes dos canais encontrados (playlist de uploads) para
  calcular a mediana por canal. Saída no formato de entrada do `swipe.py`. Autenticação por chave de API
  (`YOUTUBE_API_KEY`) ou pelo token OAuth de qualquer canal.
- **Analytics** (`youtube/analytics.py`): métricas diárias por vídeo (views, minutos assistidos,
  inscritos, CTR quando disponível) gravadas em `metricas`.

## 9. Estratégia, branding e grade

Comandos `autocanal estrategia ...`, cada um gera um relatório em `dados/estrategia/`:

1. **`nicho`** — o Claude propõe 10 nichos candidatos para pt-BR; para cada um, `pesquisa.py` coleta
   dados e `swipe.py` ranqueia por múltiplo sobre a mediana do canal. O Claude pontua: taxa de outliers,
   tamanho da concorrência, CPM relativo (qualitativo), adequação aos formatos automatizáveis e risco de
   política. Entrega top 3 com justificativa. Laura escolhe; o nicho vai para `canais.yaml`.
2. **`branding <canal>`** — nome (5 opções, com checagens a fazer: disponibilidade do @handle, busca no
   YouTube, INPI), posicionamento, paleta (contraste verificado), estilo de thumbnail, perfil de voz
   (`config/vozes/<canal>.md`) e voz TTS recomendada.
3. **`grade`** — volume e horários por formato, sustentáveis na capacidade do notebook, orientados ao
   caminho mais rápido para o Programa de Parcerias (1.000 inscritos + 4.000 h assistidas em 12 meses,
   ou 1.000 inscritos + 10 M views de Shorts em 90 dias). Grade inicial padrão (antes do estudo):
   - conteúdo: 1 Short/dia (12h ou 19h, alternando), 2 médios/semana (ter/qui 18h), 1 slides/semana
     (sáb 10h);
   - música: 3 faixas/semana (seg/qua/sex 17h) e 1 compilação de 30–60 min/semana (dom 15h) — as
     compilações aceleram as horas assistidas.
   Horários em `America/Sao_Paulo`. Ponto de partida, não verdade: não existe horário universal ótimo.
4. **`ajustar`** — após ≥ 21 dias e ≥ 15 vídeos no canal, compara desempenho por formato/dia/horário
   (views e minutos nas primeiras 72 h) e propõe nova grade; aplica sozinho se a mudança for dentro de
   limites (±1 slot por formato por semana) e registra no relatório.

Temas: `planejar` preenche a fila até cobrir `folga_dias` + 7 dias da grade. Temas vêm de uma pesquisa
de tendências semanal do nicho (`yt-viral` + `yt-plan`), guardados em `temas` com o vídeo de referência
que inspirou cada um.

## 10. Agendador, operação e interação

- **`autocanal tick`** (systemd user timer a cada 15 min, `Persistent=true`): (1) `planejar` se a fila
  estiver curta; (2) avança **um** job até concluir ou falhar a próxima etapa; (3) envia jobs `aprovado`;
  (4) atualiza estados `agendado → publicado`; (5) 1×/dia: Analytics + resumo diário. Trava global por
  arquivo impede execuções sobrepostas.
- `autocanal instalar-timer` gera e habilita o timer — **só executado após confirmação de Laura**.
- **Resumo diário** em `dados/relatorios/AAAA-MM-DD.md` + `notify-send`: produzidos, agendados,
  publicados, quarentena, falhas, alertas de auth/cota, consumo do Claude.
- **Interação pelo Claude**: `CLAUDE.md` do projeto documenta a CLI; comandos de projeto
  `/fila` (status), `/novo-video <canal> <formato> <tema>`, `/quarentena` (explica e resolve),
  `/estrategia`.
- **CLI**: `auth`, `estrategia {nicho,branding,grade,ajustar}`, `planejar`, `produzir [job]`,
  `revisar [job]`, `publicar [job]`, `vetar <job>`, `aprovar <job>`, `descartar <job>`, `status`, `tick`,
  `instalar-timer`, `diagnostico` (checa ffmpeg, GPU, serviços, chaves, tokens).

## 11. Erros

- Etapa com erro transitório (rede, 5xx, serviço subindo): até 3 tentativas com backoff; persiste →
  job `falhou`, motivo no resumo.
- Cota esgotada (403 `quotaExceeded`): adia o upload para o dia seguinte (reset à meia-noite do
  Pacífico).
- `invalid_grant`: canal marcado `auth_ok=false`; produção continua, upload para; alerta.
- Upload aceito mas vídeo rejeitado/processamento falhou: job `falhou` com o motivo da API.
- GPU sem memória: libera serviços e tenta de novo uma vez; persiste → `falhou`.
- Claude: saída inválida → até 2 novas tentativas; erro de limite de uso do plano → adia o job 1 h.

## 12. Testes

- `pytest`, sem rede por padrão. Unitários: máquina de estados da fila, agendador/grade, checagens de
  revisão, schemas, montagem de comandos ffmpeg, OAuth/upload com cliente Google simulado.
- `claude.py` testado contra um executável falso que imita a saída JSON do CLI.
- Provedores com dublês (tom senoidal no lugar do TTS, imagens procedurais).
- **Integração offline**: produz um `short` e um `slides` reais (render ffmpeg de verdade) com
  provedores dublês e verifica o MP4 com ffprobe.
- Testes marcados `@pytest.mark.real` (rede, GPU, Claude) rodam só manualmente.

## 13. Dependências

Python 3.12 (via `uv`), `google-api-python-client`, `google-auth-oauthlib`, `pyyaml`, `pydantic`,
`jsonschema`, `pillow`, `faster-whisper`, `requests`, `typer`, `pytest`. ffmpeg/ffprobe estáticos
(build BtbN, com libass) em `vendor/bin`, caminho configurável. Kokoro via container
Kokoro-FastAPI (CPU). ACE-Step 1.5 em ambiente isolado próprio, exposto como HTTP local. Fontes: DejaVu
(sistema) por padrão, configuráveis por canal.

## 14. Checklist de Laura (ações fora do sistema)

1. Criar uma conta Google pessoal (não a corporativa) e dois canais de marca.
2. Verificar o telefone dos canais (miniaturas personalizadas e vídeos > 15 min).
3. Google Cloud: ativar YouTube Data API v3, YouTube Analytics API e Cloud Text-to-Speech; tela de
   consentimento em **In production**; adicionar `http://localhost:8080/` em URIs de redirecionamento
   autorizados do client; criar chave de API (TTS e pesquisa).
4. Conta de faturamento no projeto (exigida pelo TTS, mesmo na camada grátis) — ou optar só pelo Kokoro.
5. Conta Pixabay → chave de API em `config/segredos.env`.
6. Rodar `autocanal auth conteudo` e `autocanal auth musica` (login único por canal).
7. Enviar o formulário de auditoria da YouTube API (necessário para vídeos públicos).
8. Confirmar e rodar `autocanal instalar-timer`.

## 15. Ordem de construção

1. Núcleo: config, banco/fila, CLI, wrapper do Claude, utilitários de mídia (ffmpeg).
2. `short` ponta a ponta (fatia vertical), com revisão.
3. YouTube: OAuth, upload, veto (testado com simulação; real após checklist).
4. `slides`, depois `medio`.
5. `musica` + `compilacao` (ACE-Step).
6. Estratégia (nicho, branding, grade) + Analytics + `ajustar`.
7. Agendador, resumo diário, comandos de projeto do Claude.
