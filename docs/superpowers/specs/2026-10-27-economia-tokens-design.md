# Subprojeto 5, fase A: economia de tokens sem risco de qualidade (design)

Data: 2026-10-27. Parte de `2026-10-26-ia-neutra-design.md` (subprojeto 5 da divisão) e roda sobre a
branch `feat/ia-neutra`. Laura escolheu: escopo completo (também as mudanças com risco), validação **só
por métricas automáticas**, medição **repetindo etapas sobre cópias de jobs prontos**. Ela aprovou a
parte 1 do desenho e delegou as demais decisões ("tome as decisões").

## 1. Objetivo e escopo

**Por quê:** Laura usa o plano do Claude; tokens gastos aproximam o limite de uso, que pausa a produção.
Em 8 jobs, o valor equivalente foi de US$ 39 (cerca de US$ 4,90 por vídeo): revisão 39%, roteiro 38%,
curadoria 17% e metadados 7%. Retrabalho pesa: quando a revisão reprova, o roteiro é refeito do zero com
pesquisa nova, e a revisão roda de novo.

**Divisão:**
- **Fase A (esta spec):** alavancas sem risco para a qualidade: mesmo modelo, mesmo esforço, mesmo
  julgamento, menos desperdício. Mais a ferramenta de medição.
- **Fase B (spec própria, depois dos números da fase A):** esforço ou modelo menor por etapa, revisor
  mais barato e menos candidatos, cada um aceito só se as métricas automáticas não piorarem
  (critérios na seção 10).

### Achados que orientam o desenho
1. **Contexto fixo:** uma chamada mínima do `claude -p` carrega 29.534 tokens de contexto (skills de
   todos os plugins, MCPs, hooks, ferramentas que a etapa não usa). Com as opções enxutas da seção 3:
   4.550 (−85%, medido em 2026-10-27). Esse contexto é relido a cada turno.
2. **Esforço herdado:** `~/.claude/settings.json` define `effortLevel: xhigh` (e `xhigh` para os Opus em
   `modelSettings`); toda chamada headless herda isso. Deixar de ler as configurações do usuário mudaria
   o esforço sem aviso, então o esforço passa a ser explícito.
3. **`--max-turns` é respeitado** (sonda: limite 2 → `error_max_turns` com `num_turns` 3). Os 74 turnos
   registrados numa revisão com limite 30 vêm, provavelmente, de pesquisa na web, que entra no
   `num_turns` sem contar no limite. A conferência automática das fontes (seção 7) ataca isso; não há
   mudança de código só para o limite.
4. **Turnos de leitura:** revisão e metadados gastam um turno por arquivo lido (`Read`), e roteiro e
   metadados gastam um turno por candidato pontuado (`hookscore.py`, 5×; `title.py`, até 10×).
5. Os scripts do plugin pontuam um candidato por chamada (ou um arquivo, que a IA não pode escrever).

### Critérios de sucesso
1. `uv run pytest -q` passa.
2. A bancada (seção 2) mede, nos mesmos jobs e etapas, antes e depois: **tokens de entrada (incluindo
   cache) por chamada caem pelo menos 40%** em roteiro, metadados e revisão, somados. Jobs medidos:
   `20261024-conteudo-short-5643` (short) e `20261103-conteudo-medio-e862` (médio).
3. Nenhuma métrica automática de qualidade da seção 2 piora além da tolerância: palavras do roteiro
   dentro da faixa; `nota_gancho` média não cai mais que 5 pontos; `nota_titulo` média não cai mais que 5
   pontos; a revisão sobre o mesmo vídeo dá o mesmo veredito (aprovado ou não) antes e depois. Uma
   divergência é investigada pelos problemas apontados e registrada na seção 11.
4. Os resultados ficam registrados na seção 11 desta spec.

## 2. Bancada de medição

`autocanal bancada rodar <job> <etapa> --rotulo <texto> [--modelo M] [--esforco E]` e
`autocanal bancada relatorio`.

- Etapas aceitas: `roteiro`, `metadados`, `revisao` (só IA; curadoria chama Pixabay e FLUX e fica fora).
- **Isolamento:** a pasta do job é copiada para `dados/bancada/<carimbo>-<job>-<etapa>-<rotulo>/jobs/<job>/`;
  a etapa roda com uma `Config` cujo `dados` aponta para essa pasta, com um banco novo nela e uma cópia
  da linha do job. Nada muda no banco real nem na pasta real do job.
- `--modelo` e `--esforco` sobrescrevem só a etapa medida e só nessa execução (base para a fase B).
- A etapa roda pela função do registro (`etapas.REGISTRO[etapa]`); erro da etapa é registrado como
  resultado (`erro`), não interrompe.
- Cada execução acrescenta uma linha em `dados/bancada/resultados.jsonl`: `quando`, `commit` (HEAD),
  `job`, `etapa`, `rotulo`, `modelo`, `esforco`, `chamadas`, `tokens_entrada`, `tokens_saida`,
  `custo_usd`, `turnos`, `duracao_s` (somas das linhas de `chamadas_ia` do banco temporário), `erro` e
  `metricas`:
  - roteiro: `palavras_ok` (bool), `nota_gancho`, `fontes`, `fontes_confirmadas` (seção 7; `null` se
    não houver conferência);
  - metadados: `nota_titulo`, `problemas_titulo` (quantidade);
  - revisão: `aprovado`, `etapa_para_refazer`, `problemas` (quantidade).
- `relatorio` imprime uma tabela por etapa e rótulo, com médias: chamadas, tokens de entrada, tokens de
  saída, custo, turnos e as métricas.

## 3. Chamada enxuta (adaptador do Claude)

O `claude_cli` acrescenta a toda chamada:
- `--tools` com só as ferramentas do pedido: `Read`; `WebSearch` e `WebFetch` se `web`; `Bash` se houver
  scripts. A `--allowedTools` continua limitando o `Bash` aos scripts listados.
- `--disable-slash-commands`, `--strict-mcp-config`, `--setting-sources project` e
  `--exclude-dynamic-system-prompt-sections`.
- `--effort <nível>` quando a config define um esforço para a etapa.

**Esforço explícito:** `ConfigProvedor` ganha `esforco: dict[str, str | None] = {}`, por etapa, como
`modelos`. Todo adaptador recebe `esforco: str | None` em `chamar`/`montar_argv` (o do Codex vai
traduzi-lo quando existir). `config/autocanal.yaml` passa a declarar `xhigh` para as cinco etapas no
provedor `claude` (o valor efetivo de hoje).

## 4. Entradas no prompt

Os prompts de revisão e metadados deixam de mandar ler arquivos; os dados vão no prompt, em JSON
compacto (`ensure_ascii=False`, sem indentação), por campos novos do template:

| Etapa | Campo | Conteúdo |
|---|---|---|
| revisão | `{roteiro}` | `segmentos` (só `texto` e `cena`) e `fontes` |
| revisão | `{transcricao}` | só o `texto` de `transcricao.json` |
| revisão | `{metadados}` | `titulo`, `texto_thumb`, `cena_thumb`, `descricao_final`, `tags`, `capitulos` |
| revisão | `{midias}` | por trecho: `trecho` (1, 2, …), `origem`, `licenca`, `motivo` |
| metadados | `{roteiro}` | `segmentos` (só `texto`) e `fontes` |
| metadados | `{transcricao}` | só o `texto` |
| metadados | `{tempos}` | por segmento: `inicio` e `fim`, de `narracao.json` |

A seção "O que ler (na pasta atual)" da revisão vira "Dados do vídeo"; a regra 1 dos metadados vira
"os dados do vídeo estão abaixo". `Read` continua disponível.

## 5. Pontuação numa chamada só

Novo script nosso, `autocanal/ia/extras/pontuar.py`, copiado por `metodo.preparar` para
`<pasta>/metodo/pontuar.py` sempre que houver métodos. Ele carrega as funções do próprio plugin, a
partir da cópia em `metodo/` (`score` e `band` de `yt-script/hookscore.py`; `check` de
`yt-package/title.py`), sem modificá-las:
- `python3 metodo/pontuar.py ganchos "<g1>" "<g2>" …` → JSON: lista ordenada por `verdict` decrescente
  de `{gancho, verdict, band, formula}`;
- `python3 metodo/pontuar.py titulos "<t1>" "<thumb1>" "<t2>" "<thumb2>" …` → JSON: lista ordenada por
  `score` decrescente de `{titulo, thumb, score, issues}`.

`ia.SCRIPTS` passa a ter `metodo/pontuar.py` para roteiro e metadados. Os prompts pedem uma única
chamada com todos os candidatos; a IA continua escolhendo (o maior `verdict` / `score`).

## 6. Quadros menores na revisão

`Midia.extrair_frames(video, n, destino, lado_max: int | None = None)` reduz o quadro para que o lado
maior tenha `lado_max` pixels. A revisão usa `lado_max=960` (Short 540×960; médio 960×540). A thumbnail
continua no tamanho original.

## 7. Fontes conferidas pelo Python

- O schema das fontes do roteiro ganha `trecho` (obrigatório): uma frase copiada literalmente da página
  que confirma a afirmação, até 300 caracteres. A regra 6 dos prompts de roteiro passa a pedi-lo.
- Novo módulo `autocanal/fontes.py`:
  - `conferir(fontes, obter=requests.get) -> list[dict]`: para cada fonte, `{afirmacao, url, trecho,
    status, detalhe}`, com `status` em `confirmada`, `trecho_ausente`, `inacessivel` ou `sem_trecho`;
  - baixa a página (timeout 15 s, até 2 MB, user-agent de navegador), extrai o texto com `html.parser`
    (sem `script`/`style`), normaliza (minúsculas, sem acentos, aspas e travessões unificados, espaços
    colapsados) e procura o trecho normalizado; se não achar inteiro, aceita quando o maior bloco em
    comum cobre pelo menos 80% do trecho;
  - `conferidas(pasta, fontes, obter=requests.get) -> list[dict]`: usa `fontes_conferidas.json` quando
    ele corresponde às mesmas fontes (URL + trecho); senão confere e grava.
- O roteiro confere logo depois de validar a resposta. A revisão usa `conferidas()` e põe o resultado no
  prompt (`{fontes_conferidas}`): as confirmadas são julgadas pelo trecho, sem abrir a página; só as
  outras são abertas com a ferramenta de leitura de páginas.
- Página que bloqueia robôs ou depende de JavaScript cai em `inacessivel`; o revisor abre essa.

## 8. Refazer mais barato

Quando a revisão reprova e o roteiro é refeito, o `roteiro.json` anterior ainda está na pasta. O prompt
do roteiro ganha a seção "Versão anterior" (`{roteiro_anterior}`): os segmentos (`texto`, `visual`,
`cena`) e as fontes com o status da conferência, ou "(nenhuma: esta é a primeira versão)". A regra 9 diz:
partir da versão anterior e corrigir só o que os problemas pedem, mantendo as fontes confirmadas e
pesquisando apenas o que for novo; se os problemas pedirem outro ângulo (por exemplo, roteiro parecido
com outro vídeo), escrever do zero.

## 9. Testes

Sem rede (`uv run pytest -q`), com o `claude_falso` e um `obter` falso:
- bancada: cópia isolada (o banco real e a pasta real não mudam), linha do `resultados.jsonl` com somas e
  métricas, `--modelo`/`--esforco` só na etapa medida, erro da etapa vira resultado, relatório com médias;
- argv enxuto: `--tools` por pedido, flags fixas, `--effort` só quando configurado;
- prompts: campos novos presentes, sem "leia `roteiro.json`"; dados compactos sem `palavras` da
  transcrição;
- `pontuar.py`: ganchos e títulos ordenados, rodando sobre a cópia real do plugin;
- `extrair_frames` com `lado_max` (retrato e paisagem);
- `fontes`: confirmada, trecho com acento/aspas diferentes, trecho parcial ≥80%, ausente, inacessível,
  sem trecho, cache válido e invalidado;
- roteiro: grava `fontes_conferidas.json`; com versão anterior e problemas, o prompt leva a versão
  anterior.

## 10. Fase B (decidida depois, com os números)

Candidatas e regra de aceite, todas medidas pela bancada:
- **esforço menor** em metadados, temas e curadoria (e, se os números pedirem, roteiro): aceito se os
  tokens caem ≥20% e as métricas da etapa não pioram além das tolerâncias da seção 1;
- **revisor mais barato** (modelo ou esforço): os dois revisores julgam os mesmos vídeos; aceito só se o
  mais barato **não aprovar nenhum vídeo que o atual reprova** e concordar em pelo menos 80% dos casos;
- **menos candidatos** (3 ganchos, 5 títulos): aceito se a nota média do escolhido não cai mais que 5
  pontos.

## 11. Resultados

(preenchido pela execução: linha de base, depois, decisão)
