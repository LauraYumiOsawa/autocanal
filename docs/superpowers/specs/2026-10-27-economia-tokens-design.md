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

`autocanal bancada rodar <job> <etapa> --rotulo <texto> [--modelo M] [--esforco E] [--primeira-versao]`
e `autocanal bancada relatorio`. `--primeira-versao` tira `problemas_revisao.json` da cópia, para medir
o roteiro como primeira versão e não como refação (seção 11).

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

**Visual de cada segmento (acrescentado na medição, seção 11):** além dos 6 quadros do vídeo final, o
revisor recebe o visual de cada segmento em `revisao/visuais/seg_NN.jpg` (imagem reduzida a 960 px no
lado maior; vídeo vira o quadro do meio), listado no prompt com o texto falado daquele segmento. Antes, o
revisor achava a pasta `visuais/` sozinho com as ferramentas de listagem, que a chamada enxuta (seção 3)
tirou.

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
  outras são abertas com a ferramenta de leitura de páginas. A confirmada só em parte aparece como
  `confirmada, parcial NN%`: o revisor abre a página se a parte não encontrada do trecho traz o dado da
  afirmação (número, data, nome).
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

### Linha de base (2026-10-27, commit `add83a8`, rótulo `antes`)

| Job | Etapa | Tokens de entrada | Tokens de saída | US$ equiv. | Turnos | Métricas |
|---|---|---|---|---|---|---|
| short-5643 | roteiro | 837.232 | 22.222 | 1,18 | 28 | palavras ok, gancho 44, 11 fontes |
| short-5643 | metadados | 222.824 | 2.236 | 0,20 | 9 | título 94, 1 problema |
| short-5643 | revisão | 586.310 | 11.748 | 1,03 | 27 | reprovado (visuais), 6 problemas |
| medio-e862 | roteiro | 1.263.814 | 48.908 | 2,03 | 36 | palavras ok, gancho 44, 25 fontes |
| medio-e862 | metadados | 284.949 | 3.150 | 0,19 | 13 | título 100, 1 problema |
| medio-e862 | revisão | 1.885.374 | 20.939 | 1,96 | 75 | reprovado (visuais), 6 problemas |
| **soma** | | **5.080.503** | **109.203** | **6,59** | | |

A revisão do médio usou 75 turnos com `--max-turns 30`, o que confirma o achado 3.

### Depois (2026-10-27)

Commits e rótulos por etapa:
- roteiro: `0bc30e3`, rótulo `depois-1a`, com `--primeira-versao`;
- metadados: `a1b0e0b`, rótulo `depois`;
- revisão: `c635a91`, rótulo `depois2`.

| Job | Etapa | Tokens de entrada | Tokens de saída | US$ equiv. | Turnos | Métricas |
|---|---|---|---|---|---|---|
| short-5643 | roteiro | 599.698 | 26.034 | 1,19 | 34 | palavras ok, gancho 39, 14 fontes (14 confirmadas) |
| short-5643 | metadados | 60.882 | 4.996 | 0,13 | 6 | título 98, 1 problema |
| short-5643 | revisão | 126.282 | 14.128 | 0,62 | 27 | reprovado (visuais), 5 problemas |
| medio-e862 | roteiro | 1.004.992 | 55.374 | 2,03 | 47 | palavras ok, gancho 40, 26 fontes (25 confirmadas) |
| medio-e862 | metadados | 120.545 | 7.181 | 0,17 | 8 | título 100, 1 problema |
| medio-e862 | revisão | 282.356 | 20.509 | 1,16 | 73 | reprovado (visuais), 8 problemas |
| **soma** | | **2.194.755** | **128.222** | **5,30** | | |

**Redução nos tokens de entrada:** 56,8% no total (5.080.503 → 2.194.755). Por etapa:

| Etapa | Tokens de entrada | US$ equiv. | Turnos |
|---|---|---|---|
| roteiro | −23,6% (2.101.046 → 1.604.690) | +0,5% (3,20 → 3,22) | 64 → 81 |
| metadados | −64,3% (507.773 → 181.427) | −23,6% (0,39 → 0,30) | 22 → 14 |
| revisão | −83,5% (2.471.684 → 408.638) | −40,4% (2,99 → 1,78) | 102 → 100 |

O valor equivalente caiu 19,5% (US$ 6,59 → 5,30), e os tokens de saída subiram 17%. O roteiro quase não
economizou em valor: os tokens de entrada caíram, mas ele usou mais turnos e escreveu mais. Os tokens
cortados nas outras etapas eram, em boa parte, leitura de cache, que custa pouco. Por isso o valor cai
menos que os tokens.

**Por que o roteiro foi medido duas vezes.** Os dois jobs medidos têm um `problemas_revisao.json` de
uma revisão anterior, e a cópia da bancada levava esse arquivo junto.
- Na primeira medição (`a1b0e0b`, rótulo `depois`), o roteiro seguiu o caminho de refazer a partir da
  versão anterior (seção 8). Ele devolveu quase o mesmo texto (similaridade 1,00), com o gancho e a nota
  do job real.
- A linha `antes` (código antigo) tinha reescrito do zero.
- Essa primeira medição (227.140 e 712.031 tokens; −55,3%) mostra o que a seção 8 economiza quando a
  revisão reprova e o roteiro é refeito. Não mede a primeira versão.
- A bancada ganhou `--primeira-versao`, que tira `problemas_revisao.json` da cópia, e o roteiro foi
  remedido assim.
- Ressalva: o `antes` ainda tinha os problemas no prompt, mas escreveu do zero. A diferença no tamanho
  do prompt é de poucos KB, contra cerca de 1 milhão de tokens.

**Critérios:**
1. Suíte: 488 testes passando (3 `real` fora).
2. Tokens de entrada caem pelo menos 40% somando roteiro, metadados e revisão: **atendido (−56,8%)**.
3. Qualidade: **atendido, com margem estreita no gancho e só depois da correção da revisão abaixo**:
   - palavras do roteiro dentro da faixa nos dois jobs;
   - `nota_gancho` média de 44 → 39,5 (−4,5, dentro da tolerância de 5);
   - `nota_titulo` média de 97 → 99;
   - a revisão reprova os dois vídeos pelos visuais, antes e depois.
4. Registrado aqui.

A queda do gancho está dentro da tolerância, mas veio com uma execução por job. A causa provável é a
pontuação numa chamada só (seção 5): o modelo pontua os 5 candidatos de uma vez em vez de reescrever e
pontuar um a um. Acompanhar a `nota_gancho` dos próximos vídeos reais; se a média ficar abaixo de 40,
rever a seção 5 antes da fase B.

**Divergência investigada: a revisão ficou cega aos visuais.** Na primeira medição depois (commit
`a1b0e0b`), a revisão **aprovou** os dois vídeos (short nota 84, médio 86, nenhum problema), que antes
reprovava com 6 problemas visuais concretos cada. A revisão do job real em 2026-10-24 apontava os mesmos
problemas.

Causa: pelos horários de acesso dos arquivos nas cópias da bancada, o revisor da linha de base achava
sozinho a pasta `visuais/` e abria a imagem de cada segmento (6 de 12 no short, 40 de 50 no médio). Foi
nelas que achou a maioria dos problemas. A chamada enxuta (`--tools Read WebSearch WebFetch`, seção 3)
tirou as ferramentas de listagem, e o prompt nunca citou `visuais/`. Com isso, o revisor passou a ver só
os 6 quadros do vídeo final e abriu 0 visuais.

Uma sonda descartou a redução para 960 px (seção 6): o defeito do plugue no quadro 2 do short aparece nas
duas resoluções.

Correção (commit `c635a91`, seção 6): o revisor recebe explicitamente o visual de cada segmento com o
texto falado. Na nova medição ele abriu 12 de 12 visuais no short e 50 de 50 no médio, e reprovou os dois.
Os problemas coincidem com os da linha de base:
- short: segmentos 3, 4, 7, 8 e 10 (o 11 não voltou);
- médio: trechos 2, 13, 17, 42 e 44, mais 11, 15 e 25 (o 32 não voltou).

As linhas `depois` da revisão no `resultados.jsonl` ficam como registro da falha e não entram na
comparação.

**Para a fase B:**
- O roteiro é a etapa que menos economizou (−23,6% em tokens e nada em valor) e é a mais cara. Ele é o
  alvo principal da fase B.
- A revisão do médio ainda gasta 73 turnos. O prompt pede para abrir as imagens numa rodada só, mas o
  modelo abre quase uma por turno. Agrupar as leituras é a próxima alavanca da revisão.
- Os metadados geram mais tokens de saída (2.693 → 6.088 em média), embora o valor tenha caído.
- Toda alavanca da fase B que mexer em ferramentas ou entradas do revisor precisa ser medida pelo
  veredito e pelos problemas apontados, não só pelos tokens.
