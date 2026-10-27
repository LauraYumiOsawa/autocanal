# Subprojeto 1: núcleo de IA independente de provedor, com Claude e Codex (design)

Data: 2026-10-26. Complementa `2026-10-22-autocanal-design.md` e `2026-10-23-plano2-medio-flux-design.md`;
onde divergem, este vale para o escopo dele. Desenho aprovado por Laura em 2026-10-26, em quatro partes
(peças e interface; config, pausa e registro; adaptador do Codex, método e segredos; prompts e testes).

## 1. Objetivo e escopo

**Por quê:** Laura quer escolher a IA de cada parte do autocanal, inclusive o orquestrador, para que
quem usa ChatGPT (ou Gemini, ou um modelo local) também consiga rodar o sistema. Hoje as 5 chamadas de
IA (roteiro, curadoria, metadados, revisão e temas) passam por `autocanal/claude.py` e falam o dialeto do
Claude Code: nomes de ferramentas (`Read`, `WebSearch`, `Bash(...)`), a ferramenta `Skill` e as regras
`--disallowedTools`.

**Decisões já tomadas:**
- Orquestrador e etapas trocáveis, com o provedor escolhido por etapa na config.
- Tudo continua numa máquina só. Separar em serviços e máquinas fica para depois.
- Provedores desejados: Claude Code (já existe), Codex CLI, Gemini CLI, APIs da Anthropic, da OpenAI e
  do Gemini, e Ollama. São 7 adaptadores, divididos em subprojetos.
- Abordagem: **capacidades neutras**. A etapa declara do que precisa e cada adaptador traduz.
- O protótipo `ead7426` (feito numa sessão na nuvem, sobre o código anterior ao Plano 2) não é usado.

**Divisão em subprojetos** (cada um com spec, plano e execução próprios):
1. **Este:** núcleo independente de provedor, com os adaptadores do Claude e do Codex.
2. Orquestrador independente: CLI com saída `--json`, `AGENTS.md` comum e MCP enxuto.
3. Gemini CLI.
4. APIs (Anthropic, OpenAI, Gemini) e Ollama, com pesquisa na web própria onde faltar.
5. Economia de tokens no roteiro e na revisão.
6. Comparação de qualidade entre provedores, principalmente na revisão.

**Contexto de custo** (tabela `chamadas_claude` em 2026-10-26, 8 jobs, US$ 39,24 em valor de API):

| Etapa | Chamadas | Turnos (média/máx.) | US$ por chamada | US$ total |
|---|---|---|---|---|
| revisão | 14 | 31,6 / 74 | 1,08 | 15,13 |
| roteiro | 11 | 36,1 / 46 | 1,35 | 14,83 |
| curadoria (`visuais`) | 43 | 10,2 / 17 | 0,16 | 6,72 |
| metadados | 12 | 16,0 / 28 | 0,21 | 2,56 |

Revisão e roteiro somam 76%; é o alvo do subprojeto 5. A revisão já passou de 30 turnos (o
`max_turns` configurado), o que pede uma conferência de como os turnos são registrados (fora deste
escopo, anotado para o subprojeto 5).

**Entra:** pacote `autocanal/ia/` com `Pedido`, `Resultado` e erros comuns; adaptadores `claude_cli` e
`codex_cli`; bloco `ia` na config; pausa por provedor; tabela `chamadas_ia`; skills do plugin copiadas
para a pasta do job; lista única de caminhos proibidos (`cofre`); prompts sem dialeto do Claude;
`diagnostico`, `status` e resumo diário por provedor.

**Fica para depois:** tudo dos subprojetos 2 a 6; produzir vários jobs ao mesmo tempo; várias máquinas.

### Critérios de sucesso
1. `uv run pytest -q` passa.
2. Com `ia.padrao: claude`, um short de teste é produzido até o fim e passa na revisão, como antes da
   mudança.
3. Com `ia.padrao: codex`, outro short é produzido até o fim só com o Codex, com visuais da curadoria ou
   do FLUX e sem fundo procedural. Laura assiste e julga.
4. A prova do cofre (seção 10) passa nos dois provedores.
5. Nenhum dos dois shorts é publicado.

## 2. Passo 0: sondagem do Codex (antes das tarefas que dependem do Codex)

Parte da documentação encontrada sobre o `codex exec` não é oficial. Antes de escrever o adaptador,
uma sondagem confirma, com o Codex instalado e logado por Laura:

1. a chave de config que liga e desliga a pesquisa na web no `exec` (suposição: `web_search`, com os
   valores `live` e `disabled`; a flag `--search` é global);
2. a sintaxe das regras de leitura proibida (`[permissions.<nome>.filesystem]` com `deny`) e a versão
   mínima que as aceita (suposição: 0.125), e como passá-las por `-c` ou por um perfil;
3. os campos de tokens nos eventos de `--json` (suposição: `turn.completed.usage` com `input_tokens`,
   `cached_input_tokens` e `output_tokens`);
4. o texto da mensagem de limite de uso (se não der para provocar, vale a da documentação);
5. quais palavras-chave de JSON Schema o modo estrito de `--output-schema` aceita, testando o schema do
   roteiro.

A sondagem roda numa pasta temporária, nunca no projeto, e usa um arquivo-isca para o item 2. O
resultado é registrado nesta seção antes das tarefas do adaptador. **Se a leitura proibida não
funcionar, o adaptador do Codex não é entregue** e o desenho volta para discussão (por exemplo, isolar
o processo com `bwrap`).

## 3. Pacote `autocanal/ia/`

Substitui `autocanal/claude.py`, que deixa de existir.

| Arquivo | Função |
|---|---|
| `ia/__init__.py` | `chamar()`, `livre()`, `pausar()` e o registro de uso |
| `ia/base.py` | `Pedido`, `Resultado`, `Capacidades`, erros e a expressão regular de limite de uso |
| `ia/cofre.py` | lista única de caminhos proibidos |
| `ia/metodo.py` | cópia das skills do plugin para a pasta do job |
| `ia/schema.py` | `schema_estrito()` e `remover_nulos_opcionais()` |
| `ia/claude_cli.py` | adaptador do Claude Code |
| `ia/codex_cli.py` | adaptador do Codex |

### Interface

```python
@dataclass(frozen=True)
class Pedido:
    prompt: str
    schema: dict
    pasta: Path                       # onde roda; a única pasta do projeto que a IA lê
    web: bool = False                 # pesquisa na web e leitura de páginas
    imagens: tuple[Path, ...] = ()    # o adaptador decide como entregar
    metodos: tuple[str, ...] = ()     # skills do plugin copiadas para <pasta>/metodo/
    scripts: tuple[str, ...] = ()     # relativos à pasta, ex.: "metodo/yt-script/hookscore.py"
    timeout_s: int = 900


@dataclass(frozen=True)
class Resultado:
    dados: dict
    provedor: str
    modelo: str | None
    tokens_entrada: int | None        # total de entrada, cache incluído
    tokens_saida: int | None
    custo_usd: float | None           # None quando o provedor não informa (Codex)
    turnos: int | None                # None quando o provedor não informa (Codex)
    duracao_s: float


def chamar(cfg: Config, con: sqlite3.Connection, job_id: str | None,
           etapa: str, pedido: Pedido) -> Resultado: ...
```

`chamar()`:
1. descobre o provedor da etapa (`ia.etapas[etapa]` ou `ia.padrao`), o modelo
   (`ia.provedores[p].modelos[etapa]` ou nenhum) e o `max_turns` (`ia.max_turns[etapa]`);
2. se o provedor estiver pausado, levanta `LimiteUso(provedor)` sem chamar nada;
3. copia os métodos pedidos para `<pasta>/metodo/` (seção 9);
4. chama o adaptador, que devolve o `Resultado` já validado pelo `jsonschema` contra o schema original
   (com até 3 tentativas, como hoje);
5. registra a chamada em `chamadas_ia` (seção 6).

O registro passa a ser responsabilidade de `chamar()`: as etapas não chamam mais `registrar()`.

### Erros

| Erro | Quando | Efeito no produtor |
|---|---|---|
| `ErroIA(provedor, msg)` | resposta inválida após as tentativas, erro do provedor | conta tentativa da etapa |
| `LimiteUso(ErroIA)` | limite de uso, ou provedor pausado | pausa o provedor; não conta tentativa |
| `IAIndisponivel(ErroIA)` | binário ausente ou sem permissão | pausa o provedor com o motivo; não conta tentativa |

Substituem `ErroClaude`, `LimiteUsoClaude` e `ErroClaudeIndisponivel`.

### Capacidades por etapa

| Etapa (chave na config) | Web | Imagens | Métodos | Scripts |
|---|---|---|---|---|
| `roteiro` | sim | não | `yt-script` | `metodo/yt-script/hookscore.py` |
| `curadoria` | não | sim (folhas de contato) | — | — |
| `metadados` | não | não | `yt-package`, `yt-seo`, `yt-chapters` | `metodo/yt-package/title.py` |
| `revisao` | sim | sim (quadros do vídeo) | — | — |
| `temas` | sim | não | `yt-plan` | — |

Cada adaptador declara as suas `Capacidades` (web, imagens, scripts). Claude e Codex têm as três.

### Renomeações nas etapas
- `visuais.escolher_com_claude` → `escolher_com_ia`; `planejador.sugerir_temas_claude` → `sugerir_temas`.
- As funções `ferramentas()` de `roteiro.py` e `metadados.py` e as constantes `FERRAMENTAS` de
  `revisao.py` e `planejador.py` saem; cada etapa monta o seu `Pedido`.
- A curadoria passa a registrar com a etapa `curadoria` (antes, `visuais`).
- As etapas não precisam tratar `null`: o adaptador do Codex remove os nulos dos campos que eram
  opcionais no schema original antes de validar (seção 8).

## 4. Config

```yaml
ia:
  padrao: claude                 # provedor das etapas sem exceção
  etapas: {}                     # só as exceções, ex.: {curadoria: codex}
  max_turns: {roteiro: 30, metadados: 20, revisao: 30, temas: 15, curadoria: 25}
  provedores:
    claude: {bin: claude, modelos: {metadados: sonnet, temas: sonnet}}
    codex:  {bin: codex,  modelos: {}}
```

- Os modelos ficam dentro de cada provedor; trocar `padrao` não leva um modelo de um provedor para outro.
- `max_turns` não depende de provedor. O Codex não tem limite de turnos; nele, vale o `timeout_s`.
- O `bin` de cada provedor é resolvido como o do Claude hoje (`resolver_bin_claude` vira `resolver_bin`).
- O bloco antigo `claude:` é recusado com a mensagem: "o bloco `claude:` virou `ia:`; veja
  `config/autocanal.yaml`". O `config/autocanal.yaml` é migrado no mesmo commit.
- Validação ao carregar: `padrao` e os valores de `etapas` existem em `provedores`; as chaves de
  `etapas`, `max_turns` e `modelos` são etapas conhecidas.

### `diagnostico`
Para cada provedor em uso: `<bin> --version` responde; no Codex, a versão é pelo menos a mínima da
sondagem. Para cada etapa: o provedor tem as capacidades da tabela da seção 3. O `plugin_dir` tem as
skills listadas em "Métodos".

## 5. Pausa por provedor

- `dados/pausa_claude_ate` vira `dados/pausa_ia/<provedor>`: primeira linha com o fim em ISO UTC,
  segunda com o motivo (`limite de uso` ou a mensagem de `IAIndisponivel`). A duração continua 1 h.
- `ia.livre(cfg, etapa, agora)` diz se o provedor da etapa está livre.
- `produtor`: `LimiteUso` e `IAIndisponivel` chamam `ia.pausar(cfg, e.provedor, motivo)`, restauram a
  etapa e encerram, sem contar tentativa (como hoje no limite de uso).
- `pausa_sistema` continua, só para o Docker (`ServicoIndisponivel`).
- `agendador`:
  - **produção:** só a `pausa_sistema` impede. Com um provedor pausado, o job anda até a primeira etapa
    que depende dele; as que não usam IA (narração, legendas, render) seguem. Limitação aceita: o tick
    seguinte pega o mesmo job, então um job parado num provedor pausado segura a fila até a pausa acabar.
    Com um provedor só, é o comportamento de hoje.
  - **planejamento:** roda só se `ia.livre(cfg, "temas", agora)`.
- `status` e resumo diário: um aviso por provedor pausado, com o motivo e o horário de fim.

## 6. Registro de uso

```sql
CREATE TABLE IF NOT EXISTS chamadas_ia (
  id INTEGER PRIMARY KEY,
  job_id TEXT,
  etapa TEXT NOT NULL,
  provedor TEXT NOT NULL,
  modelo TEXT,
  tokens_entrada INTEGER,
  tokens_saida INTEGER,
  custo_usd REAL,
  turnos INTEGER,
  duracao_s REAL NOT NULL,
  criado_em TEXT NOT NULL
);
```

- Migração em `db.conectar`, numa transação: se `chamadas_claude` existir, as linhas são copiadas com
  `provedor = 'claude'`, `modelo`/tokens nulos e `etapa = 'visuais'` renomeada para `curadoria`; depois
  a tabela antiga é apagada.
- Resumo diário: chamadas, tokens e custo por provedor. Custo nulo aparece como "—", não como US$ 0.

## 7. Adaptador do Claude (`claude_cli`)

É o código atual de `claude.py` recebendo um `Pedido`:
- `--allowedTools`: `Read` sempre; `WebSearch` e `WebFetch` se `web`; `Bash(python3 <script>:*)` para
  cada script. A ferramenta `Skill` sai (o método é lido como arquivo).
- `--disallowedTools`: `Read`, `Edit` e `Write` de cada caminho do cofre (seção 10).
- `imagens`: acrescenta ao fim do prompt a lista "Imagens deste pedido:" com os caminhos relativos à
  pasta; o modelo as abre com `Read`.
- Tokens: `usage` da saída JSON (`input_tokens` + `cache_read_input_tokens` +
  `cache_creation_input_tokens`; `output_tokens`). Custo e turnos como hoje.
- Limite de uso e indisponibilidade: como hoje.

## 8. Adaptador do Codex (`codex_cli`)

```
codex exec --json --ephemeral --skip-git-repo-check
  -C <pasta> -s read-only
  --output-schema <pasta>/.ia/schema.json -o <pasta>/.ia/resposta.json
  [-m <modelo>] [-i <imagem> ...]
  -c web_search=<live|disabled> -c project_doc_max_bytes=0
  -c <regras de leitura proibida>
  "<prompt>"
```

(As chaves de `-c` são as confirmadas na sondagem.)

- `schema.json` recebe `schema_estrito(pedido.schema)`. Da resposta lida de `-o`, saem as chaves com
  `null` que eram opcionais no schema original (`remover_nulos_opcionais()`); depois ela é validada
  contra o schema original.
- Tokens: do evento `turn.completed`. `custo_usd` e `turnos` ficam `None`.
- Limite de uso: a expressão regular de `ia/base.py`, ampliada com as mensagens do Codex, aplicada aos
  eventos de erro e ao stderr.
- `project_doc_max_bytes=0` impede o Codex de carregar o `AGENTS.md` do projeto dentro das etapas.
- `.ia/` fica dentro da pasta do job (em `dados/`, fora do git) e é reescrita a cada chamada.

### `schema_estrito()`
Gera, a partir do schema da etapa, uma versão aceita pelo modo estrito: `additionalProperties: false` em
todo objeto; toda propriedade em `required`, e as que eram opcionais passam a aceitar `null`; sem as
palavras-chave que a sondagem mostrar não aceitas. O schema original não muda.

## 9. Método: skills sem depender do Claude Code

O `plugin_dir` fica em `~/.claude/plugins/…`, uma pasta proibida pelo cofre, e quem usa ChatGPT não
tem esse plugin instalado. Por isso:
- antes de cada chamada, `ia/metodo.py` copia `<plugin_dir>/skills/<nome>/` para `<pasta>/metodo/<nome>/`
  para cada nome em `pedido.metodos` (sobrescreve; é idempotente);
- os prompts apontam para `metodo/<nome>/SKILL.md` e rodam os scripts por `metodo/...`;
- `plugin_dir` continua configurável. Quem não tem o Claude Code aponta para uma cópia do repositório
  do plugin `youtube-agent` (licença MIT), que entra no `LICENCAS.md`;
- `ferramentas_plugin.nota_titulo`, que roda no núcleo e não pela IA, continua usando o `plugin_dir`.

## 10. Cofre: proteção dos segredos

- `ia/cofre.py` tem a lista única de caminhos proibidos, a mesma de `regras_negadas` hoje:
  `<raiz>/config/**`, `<raiz>/dados/tokens/**`, `<raiz>/dados/*.db` e as pastas e arquivos pessoais
  (`~/.claude`, `~/.ssh`, `~/.config`, `~/.aws`, `~/.gnupg`, `~/.docker`, `~/.kube`,
  `~/.local/share`, `~/Downloads`, `~/Documents`, `~/Desktop`, `~/.netrc`, `~/.git-credentials`,
  `~/.bash_history`), mais `~/.codex`.
- Claude: vira `--disallowedTools` (como hoje).
- Codex: vira regras de leitura proibida.
- No Codex não há lista de comandos permitidos como no Claude: ele pode rodar qualquer comando, mas no
  modo somente leitura, sem rede para os comandos e sem ler os caminhos proibidos. O risco que sobra foi
  aceito por Laura.
- O `diagnostico` recusa um Codex com versão abaixo da mínima.
- **Prova do cofre** (teste `real`): uma pasta temporária com um arquivo-isca é declarada proibida; o
  modelo é instruído a ler o arquivo; o teste só passa se o conteúdo não aparecer na resposta nem nos
  eventos. Nunca usa os segredos de verdade.

## 11. Prompts

Os 6 arquivos de `autocanal/prompts/` mudam só no que os prende ao Claude:
- "Use a skill `youtube-agent:<nome>`" → "siga o método em `metodo/<nome>/SKILL.md`";
- `python3 {dir_plugin}/skills/<nome>/<script>` → `python3 metodo/<nome>/<script>`;
- "Abra cada folha com Read" → "as folhas de contato estão nas imagens deste pedido" (o mesmo para os
  quadros da revisão);
- `Read`, `WebSearch` e `WebFetch` citados no texto → "abra o arquivo", "pesquise na web", "abra a
  página da fonte";
- a exceção "arquivos da própria skill" na regra "não leia nem escreva fora da pasta atual" sai, porque
  o método agora está dentro da pasta.

O resto do texto não muda. O risco de a qualidade do Claude mudar ao ler o método como arquivo é
coberto pelo critério de sucesso 2.

## 12. Testes

Sem rede (rodam em `uv run pytest -q`):
- `tests/fakes/codex_falso.py`, no modelo do `claude_falso.py`: registra o argv, escreve eventos JSONL
  pré-gravados no stdout e a resposta no arquivo de `-o`.
- Por adaptador: `Pedido` → argv (web, imagens, scripts, modelo, cofre); leitura da saída; tokens;
  detecção de limite; indisponibilidade.
- Contrato comum, parametrizado nos dois adaptadores: formato do `Resultado`; pausa checada antes da
  chamada (o executável não é chamado); cofre presente no argv; resposta fora do schema gera nova
  tentativa e, depois de 3, `ErroIA`.
- `schema_estrito()`: objetos aninhados, opcionais viram anuláveis, original intacto.
- `metodo.preparar()`: copia as skills pedidas e só elas.
- `test_claude.py` vira `test_ia_claude.py`; os testes das etapas conferem o `Pedido` em vez da lista de
  ferramentas; a fixture `claude_falso` passa a valer via `ia.provedores.claude.bin`.
- Banco: a migração preserva as linhas, renomeia `visuais` e apaga a tabela antiga; rodar de novo não
  duplica.
- Config: bloco `claude:` recusado com a mensagem; provedor inexistente recusado.
- Agendador e produtor: pausa por provedor; job avança nas etapas sem IA com o provedor pausado;
  `IAIndisponivel` pausa o provedor, não o sistema.

Marcados `real` (só com ok de Laura): prova do cofre nos dois provedores; uma chamada mínima de verdade
em cada adaptador.

## 13. Checklist de Laura
- [ ] Instalar o Codex CLI e rodar `codex login` (antes do passo 0).
- [ ] Liberar os testes `real` e os dois shorts de teste (critérios 2 e 3).
- [ ] Assistir ao short feito só com o Codex e julgar a qualidade.
