# Plano 2 — Vídeo médio, visuais relacionados à fala e imagens FLUX (design)

Data: 2026-10-23. Complementa `2026-10-22-autocanal-design.md` (o "spec principal"); onde os dois
divergem, este vale para o escopo dele. Desenho aprovado por Laura em 2026-10-23 (opção A para o FLUX) e
revisado no mesmo dia depois do feedback dela sobre a prova do Short (seção 13): curadoria de relevância,
estilo visual por canal, procedural proibido no vídeo final e trocas de imagem mais frequentes.

## 1. Objetivo e escopo

**Por quê:** o caminho para o Programa de Parcerias passa por 4.000 h assistidas em vídeos longos, e
views de Shorts não contam. O formato `medio` (16:9, 1–5 min) é o que gera essas horas. E, nas palavras
de Laura, **o mais importante dos vídeos é o visual**: imagens realistas, vídeos satisfatórios ou
desenho, mas sempre relacionados ao que está sendo falado.

**Entra:** formato `medio` completo (roteiro com seções, legendas 16:9, capítulos, thumbnail, upload com
miniatura); curadoria do Pixabay pelo Claude (cada visual precisa mostrar o que é falado); imagens
geradas localmente pelo FLUX.2 [klein] 4B a partir do trecho narrado quando o Pixabay não tem nada que
sirva; estilo visual por canal (`realista` ou `desenho`); fundo procedural fora do vídeo final; um
visual por frase; crédito "Imagens: Pixabay" e aviso de IA na descrição; `feito_para_criancas` por canal.

**Fica para depois:** slides, canal em inglês, música/compilação, estratégia/Analytics, trilha sonora
de fundo (a biblioteca ainda está vazia).

### Critérios de sucesso
1. `autocanal novo conteudo medio "<tema>"` + `autocanal produzir` geram, sem intervenção, um MP4
   1920×1080 de 60–300 s a −14 LUFS, com legendas 16:9, capítulos válidos, `thumbnail.jpg` 1280×720,
   metadados e relatório de revisão.
2. Todo visual que vai ao ar foi **escolhido pela curadoria** como relacionado à fala do trecho ou
   **gerado pelo FLUX** a partir dessa fala. Nenhum vídeo com fundo procedural é aprovado.
3. Todos os visuais de um vídeo seguem o estilo do canal (`realista` ou `desenho`).
4. O visual troca a cada frase (alvo de 3–5 s por trecho).
5. Vídeo com mídia do FLUX sobe com `containsSyntheticMedia=true`; a descrição credita o Pixabay e
   avisa sobre IA conforme as origens reais das mídias.
6. Canal com `feito_para_criancas: true` sobe com `selfDeclaredMadeForKids=true` (upload e veto).
7. O FLUX nunca passa de 8 GB de VRAM e não roda quando a RAM livre não comporta (protege o desktop).

## 2. Passo 0 — teste de viabilidade do FLUX (antes das tarefas que dependem do FLUX)

**Pergunta:** o FLUX.2 [klein] 4B roda na RTX PRO 1000 (8 GB) com a RAM livre típica do notebook,
gerando em 16:9 e 9:16?

**Sonda:** projeto uv separado (torch CUDA + diffusers), transformer em fp8 e encoder de texto
(Qwen3-4B) quantizado ou com offload. Execução em **duas fases**: codifica todos os prompts → libera o
encoder → carrega o transformer e gera todas as imagens. Mede pico de VRAM, pico de RAM do processo,
tempo de carga e segundos por imagem em 1360×768 e 768×1360 (e 1920×1088 se couber); salva 4 amostras
(2 realistas, 2 desenho) em `dados/amostras/flux/` para Laura ver.

**Vai em frente se:** pico de VRAM ≤ 7,5 GB; pico de RAM do processo ≤ 6 GB; ≤ 30 s por imagem; Laura
acha as amostras aceitáveis. **Se não:** o Plano 2 segue sem FLUX (as tarefas do FLUX saem do plano, a
ordem de visuais fica sem `flux`) e o motivo vai para o ledger e para a memória. Sem FLUX, trecho sem
candidato aprovado na curadoria termina em procedural → a revisão reprova → quarentena.

**Rede:** o firewall da empresa intercepta `huggingface.co` (e derruba downloads grandes do PyPI); a
sonda só roda em outra rede. Nunca desligar a verificação TLS.

**Licença:** confirmar Apache 2.0 do 4B no model card oficial e registrar em `LICENCAS.md` com data. O
klein 9B é não comercial: não usar.

## 3. Formato `medio`

```
FORMATOS["medio"] = Formato("medio", 1920, 1080, 60, 300, 150, 750, "paisagem",
    ("roteiro", "narracao", "legendas", "visuais", "render", "metadados", "thumbnail", "revisao"))
```

- O prompt mira 3–4 min (450–600 palavras); os limites do formato continuam 60–300 s / 150–750.
- **Desvio do spec principal:** `thumbnail` vem depois de `metadados`, porque o texto e a cena da
  thumbnail saem do `yt-package`, que roda nos metadados.
- Os parâmetros de legenda saem das constantes de `etapas/legendas.py` e passam para o `Formato`:

| campo | short (valores atuais) | medio |
|---|---|---|
| `legenda_tamanho` | 88 | 60 |
| `legenda_margem_v` | 560 | 70 |
| `legenda_max_palavras` | 3 | 6 |
| `legenda_max_duracao_s` | 1.6 | 2.5 |

## 4. Roteiro: um visual por frase

- **Cada segmento é uma frase** (o visual troca a cada frase, alvo de 3–5 s). Short: 6–16 segmentos.
  Médio: 3–7 seções, 2–10 segmentos por seção, no máximo 60 no total.
- Prompt novo `prompts/roteiro_medio.md`, com as mesmas regras de trabalho do `roteiro_short.md`
  (yt-script como método, perfil de voz do canal, 5 ganchos pontuados pelo `hookscore.py`, fonte
  verificada para toda afirmação, nada inventado, variação frente aos títulos recentes, correção dos
  problemas da rodada anterior).
- Schema `ROTEIRO_MEDIO`: `{gancho, nota_gancho, secoes: {titulo, segmentos: {texto, visual, cena}},
  fontes, resumo}`.
- `visual`: consulta curta em inglês (2 a 5 palavras) para o Pixabay, como hoje.
- `cena`: 15 a 40 palavras **em inglês** descrevendo concretamente o que a imagem deve mostrar para
  ilustrar aquela frase; sem texto escrito na imagem, marcas, logotipos ou pessoas reais/famosas. Serve
  à curadoria (critério) e ao FLUX (prompt).
- O `roteiro_short.md` e o schema do Short também ganham `cena` e a regra de uma frase por segmento.
- A etapa grava `roteiro.json` com `secoes` (títulos) e `segmentos` **achatados**, cada um com `secao`
  (índice). Narração, legendas e render continuam lendo `segmentos` sem mudança.

## 5. Capítulos

- No médio, os capítulos são **calculados**, não escritos pelo Claude: cada seção começa no `inicio` do
  seu primeiro segmento em `narracao.json`, e o título é o da seção.
- Regras do YouTube: o primeiro em 00:00, pelo menos 3, cada um com pelo menos 10 s. Uma seção com
  menos de 10 s é fundida à anterior; se for a primeira, funde-se à seguinte (o capítulo começa em 0 com
  o título da seguinte). Se sobrarem menos de 3, o vídeo sai sem capítulos.
- O Short continua como hoje.

## 6. Visuais

### Estilo visual por canal
`estilo_visual: realista | desenho` (padrão `realista`). Vale para o vídeo inteiro:

| | Pixabay vídeos | Pixabay imagens | Sufixo do prompt do FLUX |
|---|---|---|---|
| `realista` | `video_type=film` | `image_type=photo` | `photorealistic photo, natural light, no text, no watermark` |
| `desenho` | `video_type=animation` | `image_type=illustration` | `colorful 2D cartoon illustration, clean lines, flat shading, no text, no watermark` |

Candidatos com `isLowQuality` são descartados sempre; com `isAiGenerated`, descartados no `realista`
(imagem de IA realista sem marcação seria um risco de política).

### Provedores e ordem
- `visuais` por canal, valores `pixabay` e `flux`; padrão `[pixabay]`. O canal `conteudo` usa
  `[pixabay, flux]`. O procedural é sempre o último, como **emergência**: se for usado, a revisão reprova.
- Os provedores são percorridos na ordem; cada um recebe **todos** os trechos ainda sem mídia, em lote.

### Curadoria do Pixabay (provedor `pixabay`)
1. **Candidatos:** para cada trecho, busca vídeos (até 4, na orientação do formato) e imagens (até 4) com
   a consulta `visual` e os filtros do estilo; tira URLs já usadas no job; fica com até 6, vídeos
   primeiro. Pré-visualização: a miniatura do vídeo (`videos.<tamanho>.thumbnail`) ou o `webformatURL`.
2. **Folha de contato:** uma imagem por trecho (`visuais/curadoria/seg_XX.jpg`), grade 3×2 com as
   pré-visualizações numeradas de 1 a 6 em números grandes; marca "VÍDEO" nos que são vídeo.
3. **Escolha:** o Claude (modelo `claude.modelos.curadoria`, padrão `sonnet`; ferramenta só `Read`)
   recebe, para cada trecho, o texto falado, a `cena` e o caminho da folha, e devolve `{escolhas: [{id,
   candidato (0 = nenhum), motivo}]}`. Regras do prompt: o candidato precisa **mostrar o que está sendo
   dito naquela frase** (ilustração concreta; metáfora visual só se óbvia); recusar logotipos e marcas
   visíveis, texto legível, pessoas famosas, conteúdo fora do estilo do canal; entre dois igualmente bons,
   preferir vídeo; não repetir o mesmo candidato em dois trechos. Lotes de até 15 trechos por chamada.
4. **Download:** só os escolhidos. Índice fora da faixa ou id desconhecido = nenhum. Trecho sem escolha
   segue para o próximo provedor.
5. **Erros:** trecho sem candidatos não entra na chamada. `LimiteUsoClaude` pausa a produção como hoje;
   outro erro do Claude vira erro da etapa (tenta de novo depois), porque sem curadoria não há garantia
   de relação com a fala.

`fontes_midia.json` registra a escolha e o motivo da curadoria para cada trecho.

### FLUX em lote (provedor `flux`)
- Projeto uv próprio em `servicos/flux/` (`pyproject.toml` + `gerar.py`); torch fica fora do ambiente do
  autocanal.
- Contrato: `uv run --project servicos/flux python servicos/flux/gerar.py ENTRADA.json SAIDA/`
  - entrada: `{"largura", "altura", "passos", "itens": [{"id", "prompt", "semente"}]}`
  - saída: `SAIDA/<id>.png` + `SAIDA/resultado.json` com `{"modelo", "itens": [{"id", "ok", "erro"}],
    "vram_pico_mb", "ram_pico_mb", "segundos"}`
  - código 0 = o lote rodou (itens podem falhar um a um); outro código = o lote inteiro falhou.
- Prompt final = `cena` + `estilo_ia` do canal (opcional) + sufixo do estilo visual.
- Semente = hash de (job, trecho, rodada de revisão): refazer os visuais gera imagens novas.
- **Memória, antes de cada lote:** libera o Whisper em cache no processo, para o Kokoro se estiver de pé
  e confere RAM livre ≥ `flux.ram_minima_gb` e VRAM livre ≥ `flux.vram_minima_gb`. Sem memória → pula o
  FLUX com aviso.
- Resolução: a escolhida no Passo 0 (padrão 1360×768 e 768×1360); o render já escala para o quadro.
- `config/autocanal.yaml` ganha `flux: {passos: 4, largura: 1360, altura: 768, ram_minima_gb: 6,
  vram_minima_gb: 7, timeout_base_s: 180, timeout_por_item_s: 45}`.
- Registro: `origem "flux"`, `licenca "gerado localmente (FLUX.2 klein 4B, Apache 2.0)"`, `autor
  "autocanal"`, `url "flux:<hash8>"`, `prompt`.

### Procedural
Continua existindo só para a etapa não quebrar. Qualquer trecho com origem `procedural` faz a revisão
reprovar os visuais (checagem determinística, sem gastar o Claude) e pedir para refazer a etapa
`visuais`; depois de 2 rodadas, quarentena.

## 7. Thumbnail (etapa nova, só no médio)

- Entrada: `metadados.json` com `texto_thumb` (já passa pelo linter do `title.py`) e o campo novo
  `cena_thumb` (inglês, mesmas regras de `cena`).
- Imagem-base 1280×720, nesta ordem: FLUX(`cena_thumb`) → visual escolhido pela curadoria (para vídeo, o
  quadro do meio) → procedural.
- Pillow escreve o `texto_thumb` em caixa alta, com a fonte do canal, até 2 linhas, na cor de destaque
  com contorno escuro grosso, no terço esquerdo.
- Saída `thumbnail.jpg` (JPEG qualidade 90, < 2 MB). O publicador passa a enviar `thumbnail.jpg`.

## 8. Metadados

- O schema ganha `cena_thumb` (obrigatório; ignorado no Short).
- A descrição final ganha um bloco de créditos antes de `Fontes:`: "Imagens: Pixabay" se alguma mídia
  veio do Pixabay; "Algumas imagens deste vídeo foram geradas por IA." se alguma veio do FLUX.
- `sintetico` = existe mídia do FLUX. A revisão pode ligar a marcação, nunca desligar.

## 9. Canal e upload

- `Canal` ganha `feito_para_criancas: bool = false`, `visuais: list["pixabay" | "flux"] = [pixabay]`,
  `estilo_visual: "realista" | "desenho" = realista` e `estilo_ia: str = ""`.
- `upload.montar_corpo` e o veto usam `selfDeclaredMadeForKids = canal.feito_para_criancas`.
- Grade do `conteudo`: acrescenta `{formato: medio, dias: [ter, qui], hora: "18:00"}`.
- `conteudo`: `visuais: [pixabay, flux]`, `estilo_visual: realista`. `infantil`: `estilo_visual: desenho`,
  `feito_para_criancas: true`, grade vazia até Laura decidir.
- Falha ao enviar a miniatura (canal sem verificação) → alerta no resumo diário pedindo
  youtube.com/verify; o upload do vídeo segue.

## 10. Revisão

- As checagens atuais passam a valer para o médio sem mudança.
- Novas checagens determinísticas: trecho com visual procedural → reprova `visuais`; no médio,
  `thumbnail.jpg` existe, tem 1280×720 e < 2 MB.
- O prompt do revisor põe a **relação entre visual e fala** como primeiro critério, recebe a thumbnail e
  a lista de origens das mídias, e pode pedir para refazer `visuais`, `thumbnail` ou `metadados`.

## 11. Erros

| Situação | Comportamento |
|---|---|
| Pixabay sem candidatos para um trecho | o trecho vai direto para o próximo provedor |
| Curadoria: `LimiteUsoClaude` / outro erro do Claude | pausa / erro da etapa com nova tentativa |
| FLUX não instalado, sem memória, timeout ou erro do lote | aviso no log; os trechos seguem a cadeia |
| Um item do FLUX falhou | só esse trecho segue a cadeia |
| Trecho terminou em procedural | a revisão reprova `visuais` |
| Thumbnail sem FLUX | visual da curadoria → procedural |
| Capítulos inválidos depois da fusão | vídeo sem capítulos |

## 12. Testes

- Unitários com dublês (sem GPU, sem rede, sem Claude real): candidatos do Pixabay (filtros de estilo,
  `isLowQuality`/`isAiGenerated`, orientação, exclusão), folha de contato, curadoria (lotes de 15, índice
  inválido, trecho sem candidato, erro do Claude), cadeia em lote, FLUX com `gerar.py` falso (timeout,
  item falho, memória), roteiro médio, capítulos, legendas por formato, thumbnail, créditos e
  `sintetico`, `selfDeclaredMadeForKids`, checagem de procedural na revisão.
- `@pytest.mark.real`: o FLUX gera 2 imagens reais (16:9 e 9:16).
- Provas finais, sem upload: um médio do começo ao fim com curadoria (antes do FLUX) e outro com FLUX.

## 13. O que a prova real do Short mostrou (2026-10-23)

O job `20261024-conteudo-short-2ea9` foi para quarentena depois de 2 rodadas:

- **Visuais sem relação com a fala** (Castelo de Osaka para "o vizinho", tomates no forno para "micro-
  ondas", lago com lua para "roteador do vizinho", radiotelescópio para "canais"), mais logotipo do
  Cartoon Network e aparelhos de marca. Feedback de Laura: "o vídeo deve ter imagens realistas ou vídeos
  satisfatórios ou o conteúdo em forma de desenho, mas sempre deve ter correlação com o conteúdo falado
  [...] o mais importante dos vídeos é o visual". → curadoria, estilo por canal, FLUX, procedural fora.
- **Legendas estouram o quadro:** grupos de 3 palavras longas em 88 px ("VELOCIDADE NUM COMPUTADOR")
  passam da largura com `WrapStyle: 2`. Correção (todos os formatos): `agrupar` também fecha o grupo
  quando a largura medida do texto em caixa alta (Pillow, fonte do canal no tamanho da legenda) passa da
  largura útil = `largura − MarginL − MarginR − 2 × Outline`; uma palavra sozinha mais larga fica sozinha.
- **Afirmações além da fonte:** o revisor pegou e pediu para refazer o roteiro; sem mudança aqui.
- A chave do Google TTS tem restrição de API sem o Cloud Text-to-Speech (`API_KEY_SERVICE_BLOCKED`);
  a narração caiu no Kokoro, como previsto.

## 14. Checklist de Laura

1. youtube.com/verify (miniatura personalizada).
2. País do canal = Brasil no YouTube Studio.
3. Olhar as 4 amostras do Passo 0 e dizer se a qualidade serve.
4. Google Cloud → APIs e serviços → Credenciais → a chave de API → Restrições de API: incluir
   **Cloud Text-to-Speech API** (e conferir que a API está ativada e o faturamento ligado).
5. Estar fora da rede da empresa quando for a hora da sonda e da instalação do FLUX.
