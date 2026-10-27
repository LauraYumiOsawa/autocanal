# Economia de tokens, fase A: plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** reduzir os tokens por vídeo sem mudar modelo, esforço nem julgamento, e medir o ganho com uma bancada que repete etapas sobre cópias de jobs prontos.

**Architecture:** primeiro a bancada (`autocanal/bancada.py` + `autocanal bancada`) e a linha de base. Depois, as alavancas, cada uma numa task:
- chamada enxuta e esforço explícito (`ia/claude_cli.py`, config);
- entradas no prompt (revisão e metadados);
- pontuação numa chamada (`ia/extras/pontuar.py`);
- quadros menores;
- fontes conferidas (`autocanal/fontes.py`);
- refazer barato.

Por fim, a medição depois e o registro na spec.

**Tech Stack:** Python 3.12, requests, `html.parser` (stdlib), difflib, sqlite3, typer, pytest.

**Spec:** `docs/superpowers/specs/2026-10-27-economia-tokens-design.md`

## Global Constraints

- Testes: `/home/losawa/.local/share/autocanal-tools/bin/uv run pytest -q` (o `uv` não está no PATH). Nos comandos abaixo, `UV` = esse caminho.
- Esforço atual a preservar: `xhigh` nas cinco etapas (`roteiro`, `curadoria`, `metadados`, `revisao`, `temas`).
- Flags fixas da chamada enxuta, exatamente: `--disable-slash-commands`, `--strict-mcp-config`, `--setting-sources project`, `--exclude-dynamic-system-prompt-sections`.
- Bancada só para `roteiro`, `metadados` e `revisao`; nunca toca no banco real nem em `dados/jobs/<job>`.
- Jobs medidos: `20261024-conteudo-short-5643` e `20261103-conteudo-medio-e862`.
- Trecho de fonte: até 300 caracteres; conferência com timeout de 15 s, até 2 MB e cobertura mínima de 80%.
- Quadros da revisão: `lado_max=960`.
- Quem chama usa `from autocanal import ia` e `ia.Pedido` (há outro `Pedido` em `provedores/imagens.py`).
- Nunca rodar `publicar`, `vetar`, `auth` nem `instalar-timer`; nunca ler `config/segredos.env`, `config/client_secret.json` ou `dados/tokens/`.

## Review Focus

1. **Página de fonte enorme ou binária (PDF):** a conferência não pode travar nem estourar a memória. Ela deve ler no máximo 2 MB e marcar `inacessivel` quando o conteúdo não for HTML ou texto (Task 7: `test_conferir_pdf_e_inacessivel`).
2. **Job da bancada com estado diferente do esperado pela etapa** (por exemplo, `agendado`): a cópia deve ficar em `produzindo` ou `em_revisao` para a etapa rodar, sem tocar no job real (Task 1: `test_bancada_nao_toca_no_job_real`).
3. **Candidato com aspas, ou contagem ímpar de argumentos em `pontuar.py titulos`:** o script deve sair com uma mensagem de uso e código 2, nunca com traceback (Task 5: `test_pontuar_titulos_impar`).
4. **Roteiro antigo sem `trecho` nas fontes**, por exemplo um job refeito depois da atualização: a conferência marca `sem_trecho` e a revisão abre essas fontes (Task 7: `test_conferir_sem_trecho`).
5. **Médio horizontal com `lado_max`:** a redução deve ser feita pelo lado maior, nunca deformando a imagem (Task 6: `test_extrair_frames_lado_max_paisagem`).

---

### Task 1: Bancada de medição

**Files:**
- Create: `autocanal/bancada.py`
- Modify: `autocanal/cli.py` (subcomando `bancada` com `rodar` e `relatorio`)
- Test: `tests/test_bancada.py`

**Interfaces:**
- Produces:
  - `ETAPAS_BANCADA = ("roteiro", "metadados", "revisao")`
  - `rodar(cfg: Config, con_real: sqlite3.Connection, job_id: str, etapa: str, rotulo: str, *, modelo: str | None = None, esforco: str | None = None, registro=None, agora: datetime | None = None) -> dict`
    - devolve a linha gravada no `resultados.jsonl` (campos da spec §2);
    - `registro` padrão: `etapas.REGISTRO`;
    - `--modelo` e `--esforco` alteram a cópia da config em `ia.provedores[provedor_de(etapa)]` (`modelos[etapa]` e `esforco[etapa]`).
  - `relatorio(cfg: Config) -> list[str]`: linhas da tabela.
- Isolamento:
  - `base = cfg.dados / "bancada" / f"{carimbo}-{job}-{etapa}-{rotulo}"`, com `copytree` de `dados/jobs/<job>` para `base/jobs/<job>`;
  - `cfg_b = cfg.model_copy(update={"dados": base}, deep=True)` e `con_b = conectar(base / "autocanal.db")`;
  - a linha do job é copiada do banco real com `estado` = `em_revisao` (revisão) ou `produzindo` (as outras) e `tentativas_revisao = 0`;
  - a etapa roda com `contexto_para(cfg_b, con_b, job)`.
- Métricas (lidas da cópia depois da etapa):
  - roteiro: `palavras_ok`, `nota_gancho`, `fontes`, `fontes_confirmadas` (contagem de `status == "confirmada"` em `fontes_conferidas.json`, ou `None`);
  - metadados: `nota_titulo`, `problemas_titulo`;
  - revisão: `aprovado`, `etapa_para_refazer`, `problemas`, de `revisao_1.json`.
- `commit` = `git rev-parse --short HEAD` (ou `None`).

- [ ] **Step 1: Escrever os testes que falham**

```python
def test_bancada_nao_toca_no_job_real(cfg, con, claude_falso): ...
    # job real em "agendado" com roteiro.json; rodar(..., "metadados", "antes") com o falso
    # → estado real continua "agendado"; dados/jobs/<job>/metadados.json não mudou;
    #   a cópia tem metadados.json novo
def test_bancada_grava_somas_e_metricas(cfg, con, claude_falso):
    # 1 chamada com usage {input 10, cache_read 5, output 3}, custo 0.1
    linha = rodar(cfg, con, job.id, "metadados", "antes")
    assert (linha["chamadas"], linha["tokens_entrada"], linha["tokens_saida"]) == (1, 15, 3)
    assert linha["metricas"]["nota_titulo"] == 100 and linha["rotulo"] == "antes"
    assert json.loads((cfg.dados / "bancada" / "resultados.jsonl").read_text().splitlines()[-1]) == linha
def test_bancada_modelo_e_esforco_so_na_etapa(cfg, con, claude_falso):
    # rodar(..., "metadados", "x", modelo="haiku", esforco="low") → argv tem --model haiku e
    # --effort low; cfg.ia (o objeto original) não mudou
def test_bancada_erro_vira_resultado(cfg, con):
    # registro {"roteiro": etapa que levanta ErroEtapa("quebrou")} → linha["erro"] == "quebrou"
def test_bancada_etapa_invalida(cfg, con):  # "visuais" → ValueError
def test_relatorio_medias(cfg):
    # duas linhas "antes" (tokens 100 e 300) → relatorio contém "metadados" "antes" "200"
```

- [ ] **Step 2: Rodar e ver falhar.** Run: `UV run pytest tests/test_bancada.py -q`. Expected: FAIL (`ModuleNotFoundError: autocanal.bancada`).
- [ ] **Step 3: Implementar `bancada.py` e o subcomando** (`autocanal bancada rodar <job> <etapa> --rotulo R [--modelo M] [--esforco E]` imprime a linha; `autocanal bancada relatorio` imprime a tabela).

  O teste do `--esforco` depende do `--effort` no argv, que só existe a partir da Task 3. Nesta task, ele confere só `--model` e o campo `esforco` da linha. A Task 3 acrescenta a asserção do argv.
- [ ] **Step 4: Rodar a suíte.** Run: `UV run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat: bancada que repete etapas de IA sobre cópias de jobs prontos"`

---

### Task 2: Linha de base

**Files:** nenhum código. Resultado vai para a spec §11.

- [ ] **Step 1:** Para cada job medido e cada etapa (`roteiro`, `metadados`, `revisao`), rodar `UV run autocanal bancada rodar <job> <etapa> --rotulo antes`. Expected: 6 linhas novas no `resultados.jsonl`, nenhuma com `erro` (se houver erro, registrar e repetir uma vez).
- [ ] **Step 2:** `UV run autocanal bancada relatorio`. Copiar a tabela para a spec §11, em "Linha de base".
- [ ] **Step 3: Commit** — `git commit -m "docs: linha de base da bancada de tokens"`

---

### Task 3: Chamada enxuta e esforço explícito

**Files:**
- Modify: `autocanal/config.py` (`ConfigProvedor.esforco`), `autocanal/ia/__init__.py`, `autocanal/ia/claude_cli.py`, `config/autocanal.yaml`, `tests/conftest.py`
- Test: `tests/test_ia_claude.py`, `tests/test_ia.py`, `tests/test_bancada.py`

**Interfaces:**
- Produces:
  - `ConfigProvedor.esforco: dict[str, str | None] = {}`, com as chaves validadas como etapas, igual a `modelos`;
  - `claude_cli.montar_argv(pedido, *, modelo, esforco, max_turns, bin, raiz)` e `chamar(pedido, *, modelo, esforco, max_turns, bin, raiz, tentativas=3, executar=...)`; o `ia.chamar` passa `esforco=conf.esforco.get(etapa)`;
  - `FLAGS_ENXUTAS = ["--disable-slash-commands", "--strict-mcp-config", "--setting-sources", "project", "--exclude-dynamic-system-prompt-sections"]`.
- Ordem do argv: `bin -p prompt --output-format json --json-schema S --no-session-persistence --max-turns N [--model M] [--effort E] *FLAGS_ENXUTAS --tools <ferramentas> --disallowedTools … --allowedTools …`. As ferramentas são `Read`, mais `WebSearch WebFetch` se `web`, mais `Bash` se `scripts`.
- `config/autocanal.yaml`: `claude: {bin: claude, modelos: {metadados: sonnet, temas: sonnet}, esforco: {roteiro: xhigh, curadoria: xhigh, metadados: xhigh, revisao: xhigh, temas: xhigh}}`.

- [ ] **Step 1: Testes que falham**

```python
def test_argv_enxuto(tmp_path):
    a = _argv(pedido(tmp_path, web=True), tmp_path)
    for f in ("--disable-slash-commands", "--strict-mcp-config", "--exclude-dynamic-system-prompt-sections"):
        assert f in a
    assert a[a.index("--setting-sources") + 1] == "project"
    i = a.index("--tools"); assert a[i + 1:a.index("--disallowedTools")] == ["Read", "WebSearch", "WebFetch"]
def test_argv_tools_com_scripts(tmp_path):  # scripts → "--tools" ... "Bash"
def test_argv_esforco_so_quando_definido(tmp_path):  # esforco="xhigh" → "--effort" "xhigh"; None → sem "--effort"
def test_chamar_usa_esforco_da_etapa(cfg_falso, ...):  # conftest com esforco {metadados: high} → "--effort" "high"
def test_config_esforco_etapa_desconhecida(raiz_teste):  # esforco {render: low} → ValueError
```

  Na Task 1, acrescentar a `test_bancada_modelo_e_esforco_so_na_etapa` a asserção `argv[argv.index("--effort") + 1] == "low"`.
- [ ] **Step 2: Rodar e ver falhar.** Run: `UV run pytest tests/test_ia_claude.py tests/test_ia.py -q`. Expected: FAIL nos novos.
- [ ] **Step 3: Implementar.**
- [ ] **Step 4: Rodar a suíte.** Run: `UV run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat: chamada enxuta do Claude e esforço explícito por etapa"`

---

### Task 4: Entradas no prompt (revisão e metadados)

**Files:**
- Modify: `autocanal/etapas/revisao.py`, `autocanal/etapas/metadados.py`, `autocanal/etapas/base.py` (helper), `autocanal/prompts/revisao.md`, `autocanal/prompts/metadados.md`
- Test: `tests/test_revisao.py`, `tests/test_metadados.py`

**Interfaces:**
- Produces (`etapas/base.py`): `json_compacto(dados) -> str` = `json.dumps(dados, ensure_ascii=False, separators=(",", ":"))`.
- revisão: novos campos `{roteiro}`, `{transcricao}`, `{metadados}` e `{midias}`, com o conteúdo da spec §4. O `roteiro` leva os segmentos achatados do `roteiro.json` (só `texto` e `cena`) mais as `fontes`. As `midias` saem de `fontes_midia.json` como `[{trecho: i+1, origem, licenca, motivo}]`, com `motivo` igual a `""` quando ausente.
- metadados: novos campos `{roteiro}` (segmentos só com `texto`, mais `fontes`), `{transcricao}` e `{tempos}` (`[{inicio, fim}]` dos segmentos de `narracao.json`).
- Prompt da revisão: o título "## O que ler (na pasta atual)" vira "## Dados do vídeo". Os três itens de arquivo JSON viram blocos:
  - "Roteiro (texto falado, cena e fontes):\n{roteiro}";
  - "Transcrição da narração gerada:\n{transcricao}";
  - "Metadados (título, thumbnail, descrição, tags, capítulos):\n{metadados}".

  O item de `fontes_midia.json` vira "Mídias por trecho (origem, licença, motivo da escolha):\n{midias}", e o resto do texto daquele item continua.
- Prompt dos metadados: a regra 1 vira "1. Os dados do vídeo estão abaixo: o roteiro (texto falado e fontes), a transcrição (o que a narração realmente disse) e os tempos de cada segmento (início e fim, em segundos)." Ganha também a seção "## Dados do vídeo" com os três blocos, antes de "## Perfil de voz".

- [ ] **Step 1: Testes que falham**

```python
def test_revisor_recebe_dados_no_prompt(ctx, cfg, con, claude_falso):
    # prompt contém json_compacto do texto da transcrição, o título do metadados.json e
    # '"origem":"pixabay"'; não contém '"palavras"' nem "roteiro.json"
def test_metadados_recebe_dados_no_prompt(ctx, cfg, claude_falso):
    # prompt contém o texto do segmento, '"inicio":' e o texto da transcrição; não contém "Leia, na pasta"
```

- [ ] **Step 2: Rodar e ver falhar.** Run: `UV run pytest tests/test_revisao.py tests/test_metadados.py -q`. Expected: FAIL nos novos.
- [ ] **Step 3: Implementar.**
- [ ] **Step 4: Rodar a suíte.** Run: `UV run pytest -q`. Expected: PASS (inclusive `test_prompts.py`).
- [ ] **Step 5: Commit** — `git commit -m "feat: revisão e metadados recebem os dados no prompt"`

---

### Task 5: Pontuação numa chamada só

**Files:**
- Create: `autocanal/ia/extras/pontuar.py`
- Modify: `autocanal/ia/metodo.py`, `autocanal/ia/__init__.py` (`SCRIPTS`), `autocanal/prompts/roteiro_short.md`, `roteiro_medio.md`, `metadados.md`, `pyproject.toml` (se preciso, para empacotar `extras/`)
- Test: `tests/test_pontuar.py`, `tests/test_ia_base.py`, testes de roteiro e metadados

**Interfaces:**
- `pontuar.py` (stdlib), comandos e saída como na spec §5.
  - Carrega `score` e `band` de `Path(__file__).parent / "yt-script" / "hookscore.py"` e `check` de `.../yt-package/title.py`, com `importlib.util.spec_from_file_location`.
  - Para `ganchos`, a saída é `[{"gancho", "verdict", "band", "formula"}]`, a partir do retorno `(parts, verdict, name, hits)` de `score(t)`.
  - Para `titulos`, é `[{"titulo", "thumb", "score", "issues"}]`, a partir do dict de `check(t, thumb)`.
  - Sem argumentos, com comando desconhecido ou com `titulos` em número ímpar de argumentos: imprime o uso no stderr e sai com código 2.
- `metodo.preparar`: se `nomes` não for vazio, copia também `autocanal/ia/extras/pontuar.py` para `<pasta>/metodo/pontuar.py`.
- `ia.SCRIPTS = {"roteiro": ("metodo/pontuar.py",), "metadados": ("metodo/pontuar.py",)}`.
- Prompts:
  - roteiro, regra 5: "Escreva 5 ganchos diferentes, usando fórmulas diferentes. Pontue os 5 numa única chamada com\n   `python3 metodo/pontuar.py ganchos \"<gancho 1>\" \"<gancho 2>\" \"<gancho 3>\" \"<gancho 4>\" \"<gancho 5>\"`\n   e use o de maior `verdict`. Essa nota (0 a 100) vai em `nota_gancho`.";
  - metadados, regra 6: "Escreva 10 títulos diferentes, cada um com o seu texto de thumbnail, e pontue os 10 pares numa única chamada com\n   `python3 metodo/pontuar.py titulos \"<título 1>\" \"<thumb 1>\" \"<título 2>\" \"<thumb 2>\" …`.\n   Fique com o par de maior `score`.";
  - o "hookscore" citado em `nota_gancho` do formato da resposta vira "a nota do `pontuar.py`".

- [ ] **Step 1: Testes que falham**

```python
def test_pontuar_ganchos_ordenados(tmp_path):
    # preparar(tmp_path, PLUGIN_DIR, ("yt-script",)); rodar pontuar.py ganchos "a" "b" com sys.executable
    # → JSON com 2 itens, verdict decrescente, chaves {"gancho","verdict","band","formula"}
def test_pontuar_titulos_pares(tmp_path):  # yt-package; 2 pares → score decrescente, thumb correspondente
def test_pontuar_titulos_impar(tmp_path):  # 3 argumentos → returncode 2, "uso" no stderr, sem "Traceback"
def test_metodo_copia_pontuar(tmp_path):   # preparar com ("a",) → metodo/pontuar.py existe; com () não
```

  Ajustar os testes de roteiro e metadados: `Bash(python3 metodo/pontuar.py:*)` e "python3 metodo/pontuar.py ganchos" / "titulos" no prompt.
- [ ] **Step 2: Rodar e ver falhar.** Run: `UV run pytest tests/test_pontuar.py tests/test_ia_base.py -q`. Expected: FAIL.
- [ ] **Step 3: Implementar.**
- [ ] **Step 4: Rodar a suíte.** Run: `UV run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat: ganchos e títulos pontuados numa chamada só"`

---

### Task 6: Quadros menores na revisão

**Files:**
- Modify: `autocanal/midia.py:106-118`, `autocanal/etapas/revisao.py`
- Test: `tests/test_midia.py`, `tests/test_revisao.py`

**Interfaces:** `extrair_frames(self, video, n, destino, lado_max: int | None = None) -> list[Path]`. Com `lado_max`, acrescenta `-vf scale='if(gte(iw,ih),{L},-2)':'if(gte(iw,ih),-2,{L})'` antes de `-frames:v`. A revisão passa `lado_max=960`.

- [ ] **Step 1: Testes que falham**

```python
def test_extrair_frames_lado_max_retrato(midia, tmp_path):   # vídeo 1080x1920 → quadros 540x960
def test_extrair_frames_lado_max_paisagem(midia, tmp_path):  # vídeo 1920x1080 → quadros 960x540
def test_revisao_quadros_reduzidos(ctx, cfg, con, claude_falso):  # quadro_01.jpg com maior lado 960
```

- [ ] **Step 2: Rodar e ver falhar.** Run: `UV run pytest tests/test_midia.py tests/test_revisao.py -q`. Expected: FAIL nos novos.
- [ ] **Step 3: Implementar.**
- [ ] **Step 4: Rodar a suíte.** Run: `UV run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat: quadros da revisão reduzidos para 960 px no lado maior"`

---

### Task 7: Fontes conferidas pelo Python

**Files:**
- Create: `autocanal/fontes.py`
- Modify: `autocanal/etapas/roteiro.py` (`_FONTES` com `trecho`; conferência depois de validar), `autocanal/etapas/revisao.py` (`{fontes_conferidas}`), `autocanal/prompts/roteiro_short.md`, `roteiro_medio.md`, `revisao.md`
- Test: `tests/test_fontes.py`, testes de roteiro e revisão

**Interfaces:**
- `fontes.py`:
  - `normalizar(texto: str) -> str`;
  - `texto_da_pagina(html: str) -> str`;
  - `conferir(fontes: list[dict], obter=requests.get) -> list[dict]`;
  - `conferidas(pasta: Path, fontes: list[dict], obter=requests.get) -> list[dict]`, com cache em `fontes_conferidas.json` gravado como `{"fontes": [...], "resultado": [...]}`. O cache vale quando `fontes` (url + trecho) é igual ao atual.
- Status e detalhe:

  | Status | Quando | Detalhe |
  |---|---|---|
  | `confirmada` | o trecho aparece na página | `"inteiro"` ou `"parcial NN%"` |
  | `trecho_ausente` | a página abriu, mas o trecho não aparece | `"maior trecho em comum NN%"` |
  | `inacessivel` | `RequestException`, HTTP ≥ 400 ou `Content-Type` sem `html`/`text` | — |
  | `sem_trecho` | sem `trecho` ou trecho vazio; não chama `obter` | — |
- `obter(url, timeout=15, headers={"User-Agent": UA}, stream=True)`: lê no máximo 2 MB de `iter_content` e decodifica com `r.encoding or "utf-8"` e `errors="replace"`.
- `_FONTES` (roteiro): itens com `required: ["afirmacao", "url", "trecho"]`, `trecho` como `{"type": "string", "maxLength": 300}` e `additionalProperties: false`.
- Roteiro: depois de gravar `roteiro.json`, chama `fontes.conferidas(ctx.pasta, dados["fontes"])`. A falha de rede não derruba a etapa, porque vira `inacessivel`.
- Prompt do roteiro, regra 6: "Toda afirmação factual do texto falado precisa de fonte: pesquise na web, confirme abrindo a página e registre em `fontes` a `afirmacao`, a `url` e o `trecho`: uma frase copiada literalmente da página (até 300 caracteres) que confirma a afirmação. O sistema confere automaticamente se o trecho está na página." O formato da resposta passa a dizer "com `afirmacao`, `url` e `trecho`".
- Revisão: o campo `{fontes_conferidas}` é uma lista em markdown, uma linha por fonte: ``- [<status>] <afirmacao> — <url> — "<trecho>"``. A regra 2 ("Fatos") vira: "**Fatos.** A conferência automática abaixo diz se o trecho citado de cada fonte foi encontrado na página. Fonte `confirmada`: julgue só se o trecho sustenta a afirmação, sem abrir a página. Fonte com outro status: abra a URL e confirme que a página diz o que o roteiro afirma. Afirmação factual sem fonte, ou com fonte que não a confirma = reprovação. A pesquisa na web serve para entender o contexto; não substitui a fonte citada." O bloco entra em "Dados do vídeo" como "Conferência automática das fontes:\n{fontes_conferidas}".

- [ ] **Step 1: Testes que falham** (`obter` falso devolvendo um objeto com `status_code`, `headers`, `encoding` e `iter_content`)

```python
def test_conferir_confirmada_inteiro():
def test_conferir_ignora_acento_aspas_e_espacos():  # página “Água” e trecho "agua"
def test_conferir_parcial_80():                     # 85% do trecho em sequência → confirmada, "parcial 85%"
def test_conferir_trecho_ausente():
def test_conferir_inacessivel_http_e_excecao():
def test_conferir_pdf_e_inacessivel():              # Content-Type application/pdf
def test_conferir_limita_2mb():                     # iter_content infinito → lê no máximo 2 MB
def test_conferir_sem_trecho():                     # não chama obter
def test_conferidas_usa_cache_e_invalida(tmp_path): # 2ª chamada não chama obter; trecho mudou → chama
def test_texto_da_pagina_sem_script_style():
def test_roteiro_grava_fontes_conferidas(ctx, cfg, claude_falso, monkeypatch):
def test_revisor_recebe_conferencia(ctx, cfg, con, claude_falso, monkeypatch):  # "[confirmada]" no prompt
```

- [ ] **Step 2: Rodar e ver falhar.** Run: `UV run pytest tests/test_fontes.py -q`. Expected: FAIL (`ModuleNotFoundError`).
- [ ] **Step 3: Implementar.** Os testes existentes que montam roteiros passam a incluir `trecho` nas fontes. Nos testes de roteiro e revisão, `fontes.requests.get` é trocado por um falso, sem rede.
- [ ] **Step 4: Rodar a suíte.** Run: `UV run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat: fontes conferidas pelo Python e revisor só abre as não confirmadas"`

---

### Task 8: Refazer o roteiro a partir da versão anterior

**Files:**
- Modify: `autocanal/etapas/roteiro.py`, `autocanal/prompts/roteiro_short.md`, `roteiro_medio.md`
- Test: `tests/test_etapas_iniciais.py`

**Interfaces:**
- Novo campo `{roteiro_anterior}`.
  - Se houver problemas da revisão e `roteiro.json` existir, ele recebe `json_compacto({"segmentos": [{texto, visual, cena}], "fontes": [fonte + "status"]})`, com o status vindo de `fontes_conferidas.json` (ou `"?"`).
  - Senão, recebe `"(nenhuma: esta é a primeira versão)"`.
- A nova seção "## Versão anterior" fica depois de "## Problemas da versão anterior".
- A regra 9 vira: "9. Se houver uma versão anterior, parta dela: corrija só o que os problemas pedem, mantenha as fontes confirmadas e pesquise apenas o que for novo. Se os problemas pedirem outro ângulo (por exemplo, roteiro parecido com outro vídeo), escreva do zero."

- [ ] **Step 1: Testes que falham**

```python
def test_roteiro_primeira_versao_sem_anterior(ctx, cfg, claude_falso):  # "(nenhuma: esta é a primeira versão)"
def test_roteiro_refeito_leva_versao_anterior(ctx, cfg, claude_falso):
    # roteiro.json + problemas_revisao.json existentes → prompt contém o texto do segmento anterior
    # e '"status":'
```

- [ ] **Step 2: Rodar e ver falhar.** Run: `UV run pytest tests/test_etapas_iniciais.py -q`. Expected: FAIL nos novos.
- [ ] **Step 3: Implementar.**
- [ ] **Step 4: Rodar a suíte.** Run: `UV run pytest -q`. Expected: PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat: roteiro refeito parte da versão anterior"`

---

### Task 9: Medição depois e registro

**Files:** `docs/superpowers/specs/2026-10-27-economia-tokens-design.md` (§11)

- [ ] **Step 1:** Repetir a Task 2 com `--rotulo depois`. Expected: 6 linhas sem `erro`.
- [ ] **Step 2:** `UV run autocanal bancada relatorio`. Comparar com a linha de base usando os critérios 2 e 3 da spec, com a soma de `tokens_entrada` de roteiro, metadados e revisão.
- [ ] **Step 3:** Registrar na spec §11 as tabelas, a redução percentual e a decisão (critérios atendidos ou não; divergências investigadas).
- [ ] **Step 4: Commit** — `git commit -m "docs: resultado da fase A da economia de tokens"`
