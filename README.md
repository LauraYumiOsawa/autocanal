# autocanal

Programa local que cria e publica vídeos no YouTube sozinho, em dois formatos:

- `short`: vertical (1080×1920), de 30 a 60 s
- `medio`: horizontal (1920×1080), de 1 a 5 min, com capítulos e thumbnail

Para cada vídeo, o Claude escreve o roteiro e escolhe imagens e vídeos do Pixabay que mostram o que está sendo dito. O FLUX (local, na GPU) gera o que o Pixabay não cobre. O Google TTS ou o Kokoro narram, o ffmpeg monta o vídeo e o Claude revisa o resultado. Os vídeos aprovados sobem como privados, com data de publicação marcada. Depois de ligado, um timer do systemd roda tudo de 15 em 15 minutos.

> Se você é uma IA, leia `README-IA.md`.

## Como um vídeo nasce

1. Planejar: o Claude sugere temas para os horários livres da grade de cada canal. Também dá para criar um vídeo à mão com `autocanal novo`.
2. Produzir: roteiro, narração, legendas, visuais, montagem, metadados (título, descrição, tags, capítulos), thumbnail (só no médio) e revisão.
3. Revisar: se a revisão reprovar, o sistema refaz a parte com problema. Depois de 2 reprovações, o vídeo vai para a quarentena e espera você decidir.
4. Publicar: o vídeo aprovado sobe como privado, com `publishAt`, e o YouTube o torna público no horário marcado.
5. Resumo diário: a partir das 07:00, o agendador grava um resumo em `dados/relatorios/` e avisa na área de trabalho.

## O que você precisa

| item | para quê | obrigatório |
|---|---|---|
| Linux com systemd | timer do agendador | sim |
| [uv](https://docs.astral.sh/uv/) e Python 3.12 | rodar o projeto (o uv instala o Python) | sim |
| [Claude Code](https://claude.com/claude-code) logado (`claude`) num plano com cota | roteiro, curadoria, metadados, revisão e temas | sim |
| Plugin [youtube-agent](https://github.com/Jakeschincariol/youtube-agent-skill) no Claude Code, na pasta `plugin_dir` do `autocanal.yaml` | métodos que os prompts seguem (yt-script, yt-package, yt-seo, yt-chapters, yt-plan) | sim |
| ffmpeg e ffprobe em `vendor/bin/` | montar o vídeo | sim |
| Docker sem `sudo` | Kokoro: narração local, reserva do Google e voz do canal `teste` | recomendado |
| Fonte DejaVu Sans (`sudo apt install fonts-dejavu-core`) | legendas e thumbnail | sim (ou outra fonte no `canais.yaml`) |
| Chave da API do Pixabay | visuais de qualidade | na prática, sim |
| Projeto no Google Cloud (YouTube Data API, TTS) | publicar e narrar com o Google | para publicar |
| GPU NVIDIA com 8 GB e ~16 GB de disco | FLUX, imagens geradas | não |
| `notify-send` (`libnotify-bin`) | aviso do resumo diário | não |

## Instalação

```bash
git clone https://github.com/LauraYumiOsawa/autocanal.git ~/autocanal
cd ~/autocanal
uv sync
```

1. ffmpeg: baixe um build GPL para linux64 de [BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds/releases) e copie `ffmpeg` e `ffprobe` para `vendor/bin/`.
2. Configuração: copie os modelos e preencha (veja [Configuração](#configuração)):
   ```bash
   cp config/canais.yaml.exemplo config/canais.yaml
   cp config/segredos.env.exemplo config/segredos.env
   ```
3. Kokoro: não precisa subir nada à mão. A narração sobe o contêiner quando precisa, e a produção o para no fim. Na primeira vez o Docker baixa a imagem, e o modelo leva uns 4 minutos para carregar na CPU.
4. Whisper: na primeira produção, o faster-whisper baixa o modelo `small` (~500 MB) do Hugging Face.
5. FLUX (opcional): gera as imagens que o Pixabay não cobre.
   ```bash
   uv sync --project servicos/flux
   uv run --project servicos/flux hf download black-forest-labs/FLUX.2-klein-4B
   ```
   Os pesos têm ~16 GB, licença Apache 2.0. Baixe **fora da rede da empresa**: o firewall intercepta o huggingface.co e derruba downloads grandes.
6. Conferir:
   ```bash
   uv run autocanal diagnostico
   uv run pytest -q
   ```

O comando `uv run autocanal` procura o projeto em `~/autocanal`. Se ele estiver em outro lugar, use `--raiz <pasta>` ou a variável `AUTOCANAL_RAIZ`.

## Configuração

Tudo fica em `config/`. `segredos.env` e `client_secret.json` ficam fora do git; nunca os compartilhe.

### `config/segredos.env`

```
PIXABAY_API_KEY=...
GOOGLE_TTS_API_KEY=...
YOUTUBE_API_KEY=...
```

Sem a chave do Pixabay, o FLUX gera todos os visuais (se estiver instalado e na lista `visuais` do canal). Sem nenhum dos dois, os visuais viram fundo procedural, a revisão reprova e o resumo diário avisa. Sem a chave do Google TTS, a narração usa o Kokoro.

### `config/autocanal.yaml`

Configuração geral. Normalmente só se mexe em:

- `fuso` (padrão `America/Sao_Paulo`): horários da grade e do resumo.
- `folga_dias` (padrão 3): o planejador mantém a fila cheia para os próximos N dias.
- `ia`: qual IA cada etapa usa, com modelo, esforço e limite de turnos. Hoje só o `claude` funciona.
- `flux`: projeto, tamanho das imagens e variantes por trecho.
- `revisao` (opcional): `nota_titulo_minima` (70), `similaridade_maxima` com vídeos anteriores (0,35), `max_rodadas` antes da quarentena (2) e `supervisao_dias` (14), o período inicial em que o resumo lista cada vídeo aprovado para você conferir.

### `config/canais.yaml`

Um bloco por canal. A chave (`conteudo`, `musica`, `teste`...) é o nome do canal nos comandos.

| campo | o que é |
|---|---|
| `nome` | nome exibido |
| `nicho` | assunto do canal; os temas saem daqui. Vazio = o planejador ignora o canal |
| `idioma`, `categoria_id` | idioma e categoria do YouTube (27 = Educação, 10 = Música) |
| `paleta` | cores `#RRGGBB` de fundo, texto e destaque (legendas e thumbnail) |
| `tts` | `provedor` (`google`, `kokoro` ou `falso`), `voz`, `reserva` e `voz_reserva` |
| `fonte_arquivo`, `fonte_nome` | fonte das legendas e da thumbnail |
| `perfil_voz` | arquivo `.md` com o tom do canal (veja `config/vozes/`) |
| `grade` | horários fixos: `{formato: short, dias: [seg, qua, sex], hora: "12:00"}`. Lista vazia = nada é planejado sozinho |
| `visuais` | fontes na ordem, ex.: `[pixabay, flux]` |
| `estilo_visual` | `realista` ou `desenho`, para o vídeo inteiro (filtra o Pixabay e define o estilo do FLUX) |
| `estilo_ia` | texto extra para o FLUX, em inglês (ex.: `"warm cinematic tones"`) |
| `feito_para_criancas` | marca o envio como conteúdo infantil |

O canal `teste` tem grade vazia e serve para provas locais.

### Perfil de voz (`config/vozes/*.md`)

Texto curto com o tom, o tamanho das frases e o tipo de gancho e de fechamento. O roteiro segue esse perfil. Veja `config/vozes/exemplo.md`.

## Contas e autorizações (uma vez)

1. Crie uma conta Google pessoal (não a corporativa) e os canais de marca.
2. Em youtube.com/verify, verifique o telefone dos canais (exigido para miniaturas personalizadas do `medio` e vídeos de mais de 15 min). No YouTube Studio, defina o país do canal (Brasil).
3. No Google Cloud:
   - ative a YouTube Data API v3, a YouTube Analytics API e a Cloud Text-to-Speech;
   - coloque a tela de consentimento em **In production**;
   - crie um client OAuth do tipo **App para computador** e salve o JSON baixado em `config/client_secret.json`. Com um client do tipo Web, adicione `http://localhost:8080/` (e `http://localhost:8090/`, se a 8080 estiver ocupada) aos URIs de redirecionamento autorizados;
   - crie uma chave de API e, em **Restrições de API**, inclua a **Cloud Text-to-Speech API**. Sem isso o Google responde `API_KEY_SERVICE_BLOCKED` e a narração cai no Kokoro.
4. Associe uma conta de faturamento ao projeto (o TTS exige, mesmo na camada grátis), ou use só o Kokoro.
5. Crie uma conta no Pixabay e coloque a chave de API em `config/segredos.env`.
6. Autorize cada canal (abre o navegador; o login é único):
   ```bash
   uv run autocanal auth conteudo      # --porta 8090 se a 8080 estiver ocupada
   uv run autocanal auth musica
   ```
7. Envie o formulário de auditoria da YouTube API. Enquanto o projeto não for auditado, os vídeos enviados ficam presos em privado.

## Primeiro uso

Faça uma prova local, sem enviar nada:

```bash
uv run autocanal diagnostico
uv run autocanal novo teste short "por que o céu é azul"
uv run autocanal produzir <job>      # o id aparece no comando anterior
```

O vídeo fica em `dados/jobs/<job>/video.mp4`. Assista e leia `revisao_*.json` na mesma pasta.

Para colocar um canal real para rodar:

```bash
uv run autocanal planejar            # preenche a grade com temas
uv run autocanal status
uv run autocanal tick                # um passo completo: publica, planeja e produz
```

## Dia a dia

| quero... | comando |
|---|---|
| ver a fila e os alertas | `uv run autocanal status` |
| criar um vídeo com tema meu | `uv run autocanal novo <canal> <short\|medio> "<tema>" [--quando "AAAA-MM-DD HH:MM"]` |
| produzir agora | `uv run autocanal produzir [<job>]` |
| aprovar um vídeo da quarentena | `uv run autocanal aprovar <job>` |
| refazer com os problemas apontados | `uv run autocanal produzir <job>` |
| só revisar de novo | `uv run autocanal revisar <job>` |
| desistir de um vídeo | `uv run autocanal descartar <job>` |
| impedir um vídeo já agendado de ir ao ar | `uv run autocanal vetar <job>` |
| enviar os aprovados agora | `uv run autocanal publicar [--job <job>]` |

Nas primeiras 2 semanas depois do primeiro envio, o resumo diário lista cada vídeo agendado com o link e o comando para vetar. Depois disso, só a quarentena chega até você.

No Claude Code, os comandos `/fila`, `/novo-video <canal> <formato> <tema>` e `/quarentena` fazem o mesmo conversando.

### Estados de um vídeo

```
planejado → produzindo → em_revisao → aprovado → agendado → publicado
                ↑             │
                └─ reprovado ─┤
                              └→ quarentena (2 reprovações: você decide)
```

Um vídeo vai para `falhou` quando uma etapa erra 3 vezes ou o envio ao YouTube falha. `vetado` e `descartado` são saídas manuais. `quarentena` e `falhou` voltam com `produzir`, `revisar` ou `aprovar`.

## Ligar o timer

Ligue só depois de a auditoria da API ser aprovada e de um `tick` manual rodar sem erros.

```bash
uv run autocanal instalar-timer            # só escreve ~/.config/systemd/user/autocanal.{service,timer}
uv run autocanal instalar-timer --ativar   # habilita: um tick a cada 15 min
loginctl enable-linger $USER               # roda mesmo sem sessão aberta, após reiniciar
```

Para ver o log do timer: `journalctl --user -u autocanal.service`.

## Problemas comuns

| sintoma | causa provável | o que fazer |
|---|---|---|
| "outro processo do autocanal está rodando" | um tick ou produção em andamento | esperar terminar |
| alerta "IA `claude` pausada até..." | limite de uso do plano | nada: retoma sozinho em 1 h |
| alerta "Produção pausada... queda geral" | Docker parado | `sudo systemctl start docker`; a produção volta quando a pausa de 1 h acabar |
| alerta "só com visuais procedurais" | `PIXABAY_API_KEY` ausente ou inválida | conferir `config/segredos.env` e rodar `diagnostico` |
| alerta "Canal sem autorização" | token expirado ou revogado | `uv run autocanal auth <canal>` |
| alerta "agendados seguem privados" | projeto da API não auditado | conferir o YouTube Studio e o formulário de auditoria |
| alerta "Miniatura não foi enviada" | telefone do canal não verificado | youtube.com/verify |
| alerta "Cota do YouTube esgotada" | limite diário da API | nada: os envios voltam depois da meia-noite do Pacífico |
| narração com voz do Kokoro em vez do Google | chave do TTS sem a API liberada ou sem faturamento | passo 3 de [Contas](#contas-e-autorizações-uma-vez) |

## Testes

```bash
uv run pytest -q          # rápidos, sem rede (usam um claude e um flux falsos)
uv run pytest -m real     # Kokoro, FLUX e Whisper de verdade: Docker, GPU e rede
```

## Onde fica cada coisa

| caminho | conteúdo |
|---|---|
| `config/` | configuração, perfis de voz, segredos (fora do git) |
| `dados/autocanal.db` | fila, etapas, revisões, cota e gasto de IA |
| `dados/jobs/<job>/` | tudo de um vídeo: roteiro, narração, visuais, `video.mp4`, metadados, revisões |
| `dados/relatorios/` | resumos diários |
| `dados/tokens/` | autorizações do YouTube (fora do git; não compartilhe) |
| `servicos/` | FLUX e Kokoro |
| `docs/superpowers/` | especificações e planos |
| `LICENCAS.md` | licenças e limites de cada ferramenta |

`dados/` nunca vai para o git.
