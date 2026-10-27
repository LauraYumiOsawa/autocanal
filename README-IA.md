# autocanal: guia para agentes de IA

Leia este arquivo antes de rodar qualquer comando no projeto. A versão para pessoas está em `README.md`; as regras de segurança também estão em `CLAUDE.md`.

## 1. Regras que não têm exceção

- Nunca rode `publicar`, `vetar`, `auth` nem `instalar-timer --ativar` sem pedido explícito de Laura nesta conversa. Pedido antigo ou de outro contexto não vale.
- Nunca rode `tick` por conta própria: ele envia ao YouTube os jobs `aprovado`, como `publicar`.
- Nunca leia, imprima, registre nem commite `config/client_secret.json`, `config/segredos.env` ou `dados/tokens/`. Para saber se uma chave existe, use `autocanal diagnostico`, que mostra só "definida" ou "ausente".
- Nada em `dados/` vai para o git. Não apague nada em `dados/` (banco, jobs, marcadores de pausa) sem pedido.
- Vídeo com fundo procedural é reprovado. Todo visual tem que mostrar o que a narração diz, seja pela curadoria do Pixabay, seja pelo FLUX.
- `aprovar`, `descartar` e `revisar` são decisões de Laura. Explique as opções e só execute a que ela escolher.

## 2. Como rodar

```bash
cd /home/losawa/autocanal
uv run autocanal <comando>     # CLI
uv run pytest -q               # testes (os marcados `real` ficam de fora)
```

- Nesta máquina o `uv` não está no PATH. Use `/home/losawa/.local/share/autocanal-tools/bin/uv`.
- A raiz do projeto vem de `--raiz`, depois da variável `AUTOCANAL_RAIZ` e, por último, de `~/autocanal`.
- `~/.local/bin/autocanal` é um atalho para `.venv/bin/autocanal --raiz ~/autocanal`.
- Python 3.12 (`requires-python = ">=3.12,<3.13"`). Dependências em `pyproject.toml`, travadas em `uv.lock`.
- O FLUX é um projeto uv separado (`servicos/flux`). O autocanal o chama como subprocesso e nunca importa torch.

## 3. Comandos e o que cada um custa

| comando | efeito | quem decide |
|---|---|---|
| `diagnostico` | confere ffmpeg, IA, GPU, Docker, client_secret, métodos do plugin, chaves e FLUX; sai com 1 se faltar algo obrigatório | livre |
| `status` | jobs por estado, alertas e os próximos 10 jobs | livre |
| `--help`, `<comando> --help` | ajuda | livre |
| `uv run pytest -q` | testes com fakes, sem rede | livre |
| `novo <canal> <formato> "<tema>" [--quando "AAAA-MM-DD HH:MM"]` | cria um job `planejado` no slot pedido ou no próximo slot livre | quando pedirem |
| `produzir [<job>]` | roda as etapas pendentes. Gasta cota do Claude e leva de minutos a dezenas de minutos. Sem argumento, produz o próximo da fila | quando pedirem |
| `planejar` | o Claude sugere temas para os slots livres e cria jobs. Gasta cota | quando pedirem |
| `bancada rodar <job> <roteiro\|metadados\|revisao> --rotulo X` | repete uma etapa numa cópia em `dados/bancada/` e mede tokens. Gasta cota | quando pedirem |
| `bancada relatorio` | médias da bancada | livre |
| `revisar <job>` | refaz só a revisão. Em quarentena, zera as rodadas. Gasta cota | Laura |
| `aprovar <job>` | `quarentena` ou `falhou` (com vídeo pronto e revisão aprovada) vai para `aprovado` | Laura |
| `descartar <job>` | o job vai para `descartado` e o slot fica livre | Laura |
| `tick` | um passo do agendador: envia os aprovados, sincroniza, gera o resumo, planeja e produz um job | Laura |
| `publicar [--job J]`, `vetar <job>`, `auth <canal> [--porta P]` | mexem no YouTube | só com pedido explícito |
| `instalar-timer` | sem `--ativar`, só escreve os arquivos do systemd do usuário | Laura |
| `instalar-timer --ativar` | habilita o timer de 15 em 15 min | só com pedido explícito |

Comandos que mexem na fila usam a trava `dados/tick.lock`. Se ela estiver tomada, o comando sai com código 2 e a mensagem "outro processo do autocanal está rodando". Nesse caso espere; não apague a trava.

Comandos do Claude Code em `.claude/commands/`: `/fila`, `/novo-video <canal> <formato> <tema>`, `/quarentena`.

## 4. Fila e estados

Um job é um vídeo. O id segue o padrão `AAAAMMDD-<canal>-<formato>-<hex4>`, com a data do `publish_at` em UTC.

Transições permitidas (`autocanal/fila.py`, `TRANSICOES`):

```
planejado  -> produzindo, descartado
produzindo -> em_revisao, falhou, descartado
em_revisao -> aprovado, produzindo (revisão reprovou), quarentena, falhou
aprovado   -> agendado, falhou, descartado
agendado   -> publicado, vetado, falhou
quarentena -> aprovado, produzindo, descartado
falhou     -> produzindo, aprovado, descartado
vetado     -> descartado
publicado, descartado: finais
```

- `quarentena`: a revisão reprovou `revisao.max_rodadas` vezes (padrão 2). Espera Laura.
- `falhou`: uma etapa errou 3 vezes (`MAX_TENTATIVAS_ETAPA`), ou o envio falhou.
- `agendado`: o vídeo já está no YouTube como privado, com `publishAt`. O `publicar` e o `tick` consultam o YouTube e marcam o job `publicado` quando o vídeo fica público.
- `produzir <job>` em `falhou` reseta só as etapas com erro. Em `quarentena`, grava `problemas_revisao.json` com os problemas da última revisão e refaz a partir da `etapa_para_refazer`.

## 5. Etapas de produção

Etapas, na ordem (`autocanal/formatos.py`):

- `short` (1080×1920, 30 a 60 s): roteiro, narracao, legendas, visuais, render, metadados, revisao
- `medio` (1920×1080, 60 a 300 s): as mesmas, com `thumbnail` antes da revisão

Como o produtor trata cada caso (`autocanal/produtor.py`):

- Cada etapa tem estado na tabela `etapas` (`rodando`, `ok`, `erro`). As etapas `ok` são puladas, e isso serve de checkpoint.
- Uma etapa que ficou `rodando` (processo morto ou timeout) conta como tentativa interrompida.
- Um erro conta uma tentativa e encerra a chamada. O próximo `produzir` ou `tick` continua dali.
- Limite de uso ou IA indisponível pausa o provedor por 1 h (`dados/pausa_ia/<provedor>`) e não conta tentativa.
- Se o Docker não sobe o Kokoro, a produção inteira pausa por 1 h (`dados/pausa_sistema_ate`).
- Revisão reprovada volta o job a `produzindo`, reseta o que precisa ser refeito e repete o percurso na mesma chamada.
- O Kokoro (Docker) sobe sob demanda na narração e para no fim do `produzir`.

Chamadas de IA e o que cada uma precisa: `roteiro` (web e scripts), `curadoria` (imagens; roda dentro da etapa `visuais`), `metadados` (scripts), `revisao` (web e imagens) e `temas` (web; roda no `planejar`). O provedor de cada etapa vem de `config/autocanal.yaml`, em `ia.padrao` e `ia.etapas`. Hoje só existe o adaptador `claude` (`claude -p`). O adaptador do Codex está pausado: ver `docs/superpowers/plans/2026-10-26-ia-neutra-retomar.md`.

## 6. Onde olhar

### Pasta de um job: `dados/jobs/<job>/`

| arquivo | etapa |
|---|---|
| `roteiro.json` | roteiro: `gancho`, `nota_gancho`, `secoes`, `segmentos` (cada um com `texto`, `visual`, `cena`, `secao`), `fontes` |
| `narracao.wav`, `narracao.json`, `narracao/` | narração |
| `transcricao.json`, `legendas.ass` | legendas (faster-whisper) |
| `visuais/`, `fontes_midia.json`, `flux_lote/` | visuais. `origem` de cada trecho: `pixabay`, `flux` ou `procedural` |
| `render/`, `video.mp4` | render |
| `metadados.json` | título, descrição, tags, capítulos, `sintetico` |
| `thumbnail_base.png`, `thumbnail.jpg`, `thumbnail.json` | thumbnail (médio) |
| `revisao_<n>.json`, `revisao/` | veredito de cada rodada: `aprovado`, `nota`, `problemas`, `etapa_para_refazer`, `refazer_metadados`, `contains_synthetic_media` |
| `problemas_revisao.json` | problemas da última revisão reprovada; o roteiro e os metadados refeitos os recebem no prompt |
| `aprovacao_manual.json` | aprovação feita por pessoa |
| `thumbnail_envio.json` | resultado do envio da miniatura |

### Marcadores em `dados/`

| arquivo | significado |
|---|---|
| `autocanal.db` | SQLite: `jobs`, `etapas`, `revisoes`, `canais_estado`, `cota`, `temas`, `chamadas_ia`, `metricas` |
| `pausa_ia/<provedor>` | provedor de IA pausado até a data na 1ª linha; motivo na 2ª |
| `pausa_sistema_ate` | produção pausada por queda geral (Docker) |
| `planejar_apos` | o planejamento automático espera até essa data (4 h depois de uma falha) |
| `cota_esgotada_ate` | cota do YouTube esgotada; envios suspensos |
| `relatorios/AAAA-MM-DD.md` | resumo diário, gerado pelo tick a partir das 07:00 |
| `tick.lock` | trava entre processos |
| `bancada/` | cópias e `resultados.jsonl` da bancada |

`dados/pausa_claude_ate` é de uma versão antiga e não é mais lido.

### Consultas ao banco (só leitura)

Nesta máquina não há o `sqlite3` de linha de comando. Use Python:

```bash
uv run python - <<'EOF'
import sqlite3
con = sqlite3.connect("dados/autocanal.db"); con.row_factory = sqlite3.Row
job = "20261103-conteudo-medio-e862"
for r in con.execute("SELECT nome, estado, tentativas, erro FROM etapas WHERE job_id=?", (job,)):
    print(dict(r))
for r in con.execute("SELECT rodada, aprovado, motivos_json, etapa_para_refazer FROM revisoes WHERE job_id=?", (job,)):
    print(dict(r))
EOF
```

Gasto de IA por etapa: `SELECT etapa, COUNT(*), SUM(tokens_entrada), SUM(custo_usd) FROM chamadas_ia GROUP BY etapa`.

Nunca escreva no banco direto. Toda mudança de estado passa pela CLI, que valida as transições.

## 7. Receitas

**Prova local, sem enviar nada.** O canal `teste` tem grade vazia e o tick nunca planeja nele.
```bash
uv run autocanal novo teste short "tema"
uv run autocanal produzir <job>
uv run autocanal status
```

**Explicar a quarentena:** rode `status`, leia os `revisao_*.json` do job e resuma o veredito e os problemas. Ofereça três caminhos: `aprovar` (o vídeo serve como está), `produzir <job>` (refaz com os problemas da última revisão) ou `descartar`.

**Job em `falhou`:** veja `jobs.erro` e a tabela `etapas` para achar a etapa e a mensagem. Corrija a causa (chave, serviço, código) antes de rodar `produzir <job>`. Se todas as etapas estão `ok`, quem falhou foi o envio. O próximo passo é `aprovar <job>` ou `revisar <job>`, e a decisão é de Laura.

**Nada anda:** rode `status`. Os alertas mostram provedor pausado, queda geral, recuo do planejador, cota do YouTube, canal sem autorização e vídeo agendado ainda privado (projeto da API não auditado). Rode `diagnostico` para conferir ferramentas e chaves.

**Visuais procedurais:** o alerta "só com visuais procedurais" quase sempre significa `PIXABAY_API_KEY` ausente. Confirme com `diagnostico` sem abrir o `segredos.env`.

## 8. Mapa do código

```
autocanal/
  cli.py            comandos (typer)
  config.py         carrega config/*.yaml e segredos.env (pydantic)
  db.py, fila.py    schema SQLite, jobs e máquina de estados
  agendador.py      tick: publicar → sincronizar → resumo → planejar → produzir 1 job
  planejador.py     slots livres da grade + temas sugeridos pela IA
  grade.py          slots de horário por canal/formato
  produtor.py       percorre as etapas com checkpoint, tentativas e pausas
  etapas/           uma função executar(ctx) por etapa (REGISTRO em __init__.py)
  ia/               núcleo de IA neutro: Pedido/Resultado, pausas, adaptador claude_cli, cofre (caminhos proibidos à IA)
  prompts/          prompts .md de cada etapa de IA
  provedores/       tts (Google, Kokoro, falso), imagens (Pixabay), curadoria, flux
  publicador.py     envio ao YouTube, cota, reagendamento, sincronização
  youtube/          oauth e upload
  relatorio.py      resumo diário, alertas, notify-send
  bancada.py        medição de tokens por etapa
servicos/flux/      gerar.py (FLUX.2 klein 4B em lote, projeto uv à parte)
servicos/kokoro/    docker-compose do Kokoro-FastAPI (CPU)
config/             autocanal.yaml, canais.yaml, vozes/*.md (+ segredos, fora do git)
tests/              pytest; fakes/claude_falso.py e fakes/flux_falso.py substituem os binários
docs/superpowers/   specs, planos e o andamento de cada um
```

## 9. Mudanças de código

- Código, nomes, mensagens e commits em português, no estilo do código vizinho: docstrings curtas e comentários só onde o motivo não é óbvio.
- Testes primeiro. Os testes usam a fixture `raiz_teste`/`cfg` (`tests/conftest.py`), o `claude_falso` e o `flux_falso`. A rede fica bloqueada para `autocanal.fontes`.
- Os testes `@pytest.mark.real` (`uv run pytest -m real`) rodam Kokoro, FLUX e Whisper de verdade, com Docker, GPU e rede. Só rode com pedido.
- Os prompts das etapas de IA ficam em `autocanal/prompts/`; o schema da resposta fica no módulo da etapa (ex.: `SCHEMAS` em `etapas/roteiro.py`). Se mudar o formato da resposta, mude os dois. Meça antes e depois com a `bancada` e compare o veredito e os problemas da revisão além dos tokens: uma economia já deixou o revisor cego aos visuais.
- Antes de começar uma tarefa grande, leia a spec e o plano em `docs/superpowers/` e o andamento em `.superpowers/sdd/<plano>/progress.md`.
- Só faça commit ou push com pedido.
