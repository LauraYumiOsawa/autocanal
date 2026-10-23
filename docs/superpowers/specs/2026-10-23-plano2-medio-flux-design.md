# Plano 2 — Vídeo médio + imagens FLUX (design)

Data: 2026-10-23. Complementa `2026-10-22-autocanal-design.md` (o "spec principal"); onde os dois
divergem, este vale para o escopo dele. Desenho aprovado por Laura em 2026-10-23 (opção A para o FLUX).

## 1. Objetivo e escopo

**Por quê:** o caminho para o Programa de Parcerias passa por 4.000 h assistidas em vídeos longos, e
views de Shorts não contam. O formato `medio` (16:9, 1–5 min) é o que gera essas horas.

**Entra:** formato `medio` completo (roteiro com seções, legendas 16:9, capítulos, thumbnail, upload com
miniatura); imagens geradas localmente pelo FLUX.2 [klein] 4B a partir do trecho narrado (também nos
Shorts, quando o canal ligar); crédito "Imagens: Pixabay" e aviso de IA na descrição; campo
`feito_para_criancas` por canal.

**Fica para depois:** slides, canal em inglês, música/compilação, estratégia/Analytics, trilha sonora
de fundo (a biblioteca ainda está vazia).

### Critérios de sucesso
1. `autocanal novo conteudo medio "<tema>"` + `autocanal produzir` geram, sem intervenção, um MP4
   1920×1080 de 60–300 s a −14 LUFS, com legendas 16:9, capítulos válidos, `thumbnail.jpg` 1280×720,
   metadados e relatório de revisão.
2. Todo trecho tem mídia. Quando não há vídeo do Pixabay, o FLUX gera a imagem a partir da `cena` do
   trecho; se o FLUX falhar ou não couber na memória, o trecho cai para foto do Pixabay/procedural e o
   job **não** falha.
3. Vídeo com mídia do FLUX sobe com `containsSyntheticMedia=true`; a descrição credita o Pixabay e
   avisa sobre IA conforme as origens reais das mídias.
4. Canal com `feito_para_criancas: true` sobe com `selfDeclaredMadeForKids=true` (upload e veto).
5. O FLUX nunca passa de 8 GB de VRAM e não roda quando a RAM livre não comporta (protege o desktop).

## 2. Passo 0 — teste de viabilidade do FLUX (antes das tarefas que dependem do FLUX)

**Pergunta:** o FLUX.2 [klein] 4B roda na RTX PRO 1000 (8 GB) com a RAM livre típica do notebook,
gerando em 16:9 e 9:16?

**Sonda:** projeto uv separado em `servicos/flux/` (torch CUDA + diffusers), transformer em fp8 e
encoder de texto (Qwen3-4B) quantizado ou com offload. Execução em **duas fases**: codifica todos os
prompts → libera o encoder → carrega o transformer e gera todas as imagens. Mede pico de VRAM, pico de
RAM do processo, tempo de carga e segundos por imagem em 1360×768 e 768×1360 (e 1920×1088 se couber);
salva 4 amostras em `dados/amostras/flux/` para Laura ver.

**Vai em frente se:** pico de VRAM ≤ 7,5 GB; pico de RAM do processo ≤ 6 GB; ≤ 30 s por imagem; Laura
acha as amostras aceitáveis. **Se não:** o Plano 2 segue sem FLUX (as tarefas do FLUX saem do plano, a
ordem de visuais fica sem `flux`) e o motivo vai para o ledger e para a memória.

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

## 4. Roteiro do médio

- Prompt novo `prompts/roteiro_medio.md`, com as mesmas regras de trabalho do `roteiro_short.md`
  (yt-script como método, perfil de voz do canal, 5 ganchos pontuados pelo `hookscore.py`, fonte
  verificada para toda afirmação, nada inventado, variação frente aos títulos recentes, correção dos
  problemas da rodada anterior).
- Schema `ROTEIRO_MEDIO`: `{gancho, nota_gancho, secoes[3..7]: {titulo, segmentos[2..8]: {texto, visual,
  cena}}, fontes, resumo}`, com no máximo 40 segmentos no total.
- `cena`: 15 a 40 palavras **em inglês**, descrição fotográfica concreta de uma cena para o gerador de
  imagens; sem texto escrito na imagem, marcas, logotipos ou pessoas reais/famosas.
- O `roteiro_short.md` e o schema do Short também ganham `cena` (mesmas regras), para o FLUX servir aos
  Shorts quando o canal o colocar na ordem de visuais.
- A etapa grava `roteiro.json` com `secoes` (títulos) e `segmentos` **achatados**, cada um com `secao`
  (índice). Narração, legendas e render continuam lendo `segmentos` sem mudança.

## 5. Capítulos

- No médio, os capítulos são **calculados**, não escritos pelo Claude: cada seção começa no `inicio` do
  seu primeiro segmento em `narracao.json`, e o título é o da seção.
- Regras do YouTube: o primeiro em 00:00, pelo menos 3, cada um com pelo menos 10 s. Uma seção com
  menos de 10 s é fundida à anterior; se sobrarem menos de 3, o vídeo sai sem capítulos e a revisão
  registra o motivo.
- O Short continua como hoje.

## 6. Visuais

### Provedores e ordem
- `PixabayImagens` é dividido em `pixabay_video` e `pixabay_foto` (a origem continua `"pixabay"`).
- Novo provedor `flux`, que trabalha em lote.
- `procedural` é sempre o último; ele é acrescentado se o canal o omitir.
- Ordem por canal em `canais.yaml`, por exemplo `visuais: [flux, pixabay_video, pixabay_foto, procedural]`. O padrão,
  quando o campo é omitido, é `[pixabay_video, pixabay_foto, procedural]`: nada muda para canais que não
  pedirem o FLUX. O canal `conteudo` passa a usar a ordem com `flux` primeiro (ver seção 13).

### Algoritmo
Os provedores são percorridos na ordem; cada um recebe **todos** os trechos ainda sem mídia. Provedores
por trecho (`pixabay_*`, `procedural`) resolvem um a um, com o conjunto `excluir` como hoje; o `flux`
recebe todos de uma vez. `ErroProvedor` → aviso no log e os trechos seguem para o próximo provedor.

`fontes_midia.json` registra, para mídia do FLUX: `origem: "flux"`, `licenca: "gerado localmente
(FLUX.2 klein 4B, Apache 2.0)"`, `autor: "autocanal"`, `url: "flux:<hash8>"` e o `prompt` usado.

### Serviço FLUX em lote (`servicos/flux/`)
- Projeto uv próprio (`pyproject.toml` + `gerar.py`) com dependências fixadas; pesos no cache do HF.
  As dependências pesadas (torch) não entram no ambiente do autocanal.
- Contrato: `uv run --project servicos/flux python servicos/flux/gerar.py ENTRADA.json SAIDA/`
  - entrada: `{"largura", "altura", "passos", "itens": [{"id", "prompt", "semente"}]}`
  - saída: `SAIDA/<id>.png` + `SAIDA/resultado.json` com `{"modelo", "itens": [{"id", "ok", "erro"}],
    "vram_pico_mb", "ram_pico_mb", "segundos"}`
  - código 0 = o lote rodou (itens podem falhar um a um); outro código = o lote inteiro falhou (stderr
    curto, sem caminhos de segredo).
- Lado do autocanal: `provedores/flux.py` monta a entrada, roda o subprocesso com timeout
  (`timeout_base_s + timeout_por_item_s × itens`), lê o resultado e devolve uma `MidiaObtida` por item.
  Falha do subprocesso ou timeout → `ErroProvedor` para o lote todo.
- **Memória, antes de cada lote:** (a) libera o modelo do Whisper que fica em cache no processo
  (`legendas.liberar_modelos()`); (b) para o Kokoro se estiver de pé; (c) confere `MemAvailable` ≥
  `flux.ram_minima_gb` e VRAM livre (nvidia-smi) ≥ `flux.vram_minima_gb`. Sem memória → pula o FLUX com
  aviso; o job não falha.
- Prompt final = `cena` + `estilo_ia` do canal (opcional) + sufixo fixo pedindo foto realista sem texto
  nem marca d'água.
- Semente = hash de (job, trecho, rodada de revisão): refazer os visuais gera imagens novas.
- Resolução: a escolhida no Passo 0 (padrão 1360×768 e 768×1360, múltiplos de 16); o render já escala
  para o quadro.
- `config/autocanal.yaml` ganha `flux: {passos: 4, largura: 1360, altura: 768, ram_minima_gb: 6,
  vram_minima_gb: 7, timeout_base_s: 180, timeout_por_item_s: 45}` (retrato inverte largura e altura).

## 7. Thumbnail (etapa nova, só no médio)

- Entrada: `metadados.json` com `texto_thumb` (já passa pelo linter do `title.py`) e o campo novo
  `cena_thumb` (inglês, mesmas regras de `cena`).
- Imagem-base 1280×720, nesta ordem: FLUX(`cena_thumb`) → primeiro visual não procedural do vídeo (para
  vídeo, o quadro do meio) → procedural.
- Pillow escreve o `texto_thumb` em caixa alta, com a fonte do canal, até 2 linhas, na cor de destaque
  com contorno escuro grosso, no terço esquerdo.
- Saída `thumbnail.jpg` (JPEG qualidade 90, < 2 MB, o limite do YouTube). O publicador passa a enviar
  `thumbnail.jpg` (hoje procura `thumbnail.png`).

## 8. Metadados

- O schema ganha `cena_thumb` (obrigatório; ignorado no Short).
- A descrição final ganha um bloco de créditos antes de `Fontes:`: "Imagens: Pixabay" se alguma mídia
  veio do Pixabay; "Algumas imagens deste vídeo foram geradas por IA." se alguma veio do FLUX.
- `sintetico` = existe mídia do FLUX. A revisão pode ligar a marcação, nunca desligar (vale também para
  `_declarar_sintetico` na aprovação manual).

## 9. Canal e upload

- `canais.yaml` (modelo `Canal`) ganha `feito_para_criancas: bool = false`, `visuais: list[str]` e
  `estilo_ia: str = ""`.
- `upload.montar_corpo` e o veto usam `selfDeclaredMadeForKids = canal.feito_para_criancas`.
- Grade do `conteudo`: acrescenta `{formato: medio, dias: [ter, qui], hora: "18:00"}`.
- O canal `infantil` recebe `feito_para_criancas: true`; a grade dele continua vazia até Laura decidir.
- Falha ao enviar a miniatura por canal sem verificação → alerta no resumo diário pedindo
  youtube.com/verify (o upload do vídeo segue, como hoje).

## 10. Revisão

- As checagens atuais (duração pelo formato, legendas, repetição) passam a valer para o médio sem
  mudança.
- Novas checagens determinísticas no médio: `thumbnail.jpg` existe, tem 1280×720 e < 2 MB.
- O prompt do revisor recebe a thumbnail (o Claude a abre com `Read`) e a lista de origens das mídias;
  pode pedir para refazer `thumbnail` ou `metadados`.

## 11. Erros

| Situação | Comportamento |
|---|---|
| FLUX não instalado, sem memória, timeout ou erro do lote | aviso no log; os trechos seguem a cadeia |
| Um item do FLUX falhou | só esse trecho segue a cadeia |
| Thumbnail sem FLUX | cai para visual do vídeo → procedural |
| Capítulos inválidos depois da fusão | vídeo sem capítulos; motivo registrado na revisão |

## 12. Testes

- Unitários com dublês (sem GPU): ordem e lote da cadeia de visuais, quedas, portões de memória,
  subprocesso do FLUX com `gerar.py` falso, roteiro médio (achatamento e validação), capítulos (fusão e
  limites), parâmetros de legenda por formato, composição da thumbnail (tamanho, peso, texto dentro do
  quadro), créditos e `sintetico` nos metadados, `selfDeclaredMadeForKids` no upload e no veto, campos
  novos do canal, planejamento da grade com `medio`.
- `@pytest.mark.real`: o FLUX gera 2 imagens reais (16:9 e 9:16).
- Prova final, sem upload: `autocanal novo conteudo medio "<tema>"` → `produzir` → conferir vídeo,
  thumbnail e descrição.

## 13. O que a prova real do Short mostrou (2026-10-23)

O job `20261024-conteudo-short-2ea9` foi para quarentena depois de 2 rodadas. A revisão funcionou e
apontou três classes de problema que este plano trata:

- **Legendas estouram o quadro:** grupos de 3 palavras longas em 88 px ("VELOCIDADE NUM COMPUTADOR")
  passam da largura com `WrapStyle: 2`. Correção (vale para todos os formatos): `agrupar` fecha o grupo
  também quando a largura medida do texto em caixa alta (Pillow, fonte do canal no tamanho da legenda)
  passar da largura útil = `largura − MarginL − MarginR − 2 × Outline`. Uma palavra sozinha mais larga
  que isso fica sozinha no grupo.
- **Pixabay erra o assunto:** consultas de 2–5 palavras trouxeram Castelo de Osaka, tomates no forno,
  logotipo do Cartoon Network e aparelhos de marca. Isso reforça o FLUX: no canal `conteudo` a ordem
  passa a ser `[flux, pixabay_video, pixabay_foto, procedural]` (o padrão dos outros canais não muda).
- **Afirmações além da fonte:** o revisor pegou frases que extrapolam a fonte citada; o mecanismo já
  existe (refazer roteiro), sem mudança neste plano.

Também: a chave do Google TTS tem restrição de API que não inclui o Cloud Text-to-Speech
(`API_KEY_SERVICE_BLOCKED`); a narração caiu no Kokoro, como previsto. Ação de Laura no checklist.

## 14. Checklist de Laura

1. youtube.com/verify (miniatura personalizada).
2. País do canal = Brasil no YouTube Studio.
3. Olhar as 4 amostras do Passo 0 e dizer se a qualidade serve.
4. Google Cloud → APIs e serviços → Credenciais → a chave de API → Restrições de API: incluir
   **Cloud Text-to-Speech API** (e conferir que a API está ativada e o faturamento ligado).
5. Baixar os pesos do FLUX fora da rede da empresa (o firewall intercepta o huggingface.co).
