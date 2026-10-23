# Plano 2 — Vídeo médio, visuais relacionados à fala e imagens FLUX

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `autocanal` produz vídeos `medio` (16:9, 1–5 min) com capítulos, thumbnail e créditos, e todo visual que vai ao ar mostra o que está sendo falado: escolhido no Pixabay por uma curadoria do Claude ou gerado localmente pelo FLUX.2 [klein] 4B a partir da fala.

**Architecture:** O `medio` é mais uma receita em `FORMATOS`; as etapas existentes passam a ler do `Formato` o que hoje é constante do Short. A cadeia de visuais vira uma lista por canal de provedores em lote: a curadoria do Pixabay (candidatos → folha de contato → escolha do Claude) e o FLUX (subprocesso num projeto uv separado, contrato JSON). O procedural só existe para a etapa não quebrar; a revisão reprova qualquer vídeo que o use.

**Tech Stack:** Python 3.12 (uv), pydantic 2, pillow, requests, ffmpeg (`vendor/bin`), pytest; serviço FLUX: torch CUDA (cu128, GPU sm_120), diffusers, transformers.

**Spec:** `docs/superpowers/specs/2026-10-23-plano2-medio-flux-design.md` (complementa `docs/superpowers/specs/2026-10-22-autocanal-design.md`)

## Global Constraints

- Raiz `/home/losawa/autocanal`, branch `feat/plano2` (criada a partir de `main`). `export PATH="$HOME/.local/share/autocanal-tools/bin:$PATH"`; testes `uv run pytest -q` (exclui `@pytest.mark.real`).
- Testes padrão **sem rede, sem GPU, sem Claude real**; o FLUX real só em `@pytest.mark.real`.
- **Nunca** upload real, `autocanal auth`, timer systemd, `git push`, nem mexer em contas Google. Segredos nunca em logs, exceções ou commits.
- **Nunca** desligar verificação TLS. Pesos do Hugging Face e pacotes grandes só fora da rede da empresa (o firewall Fortinet intercepta `huggingface.co` e derruba downloads grandes).
- Roteiro: um segmento = uma frase. Short: 6–16 segmentos. `medio`: 1920×1080, 60–300 s, 150–750 palavras (prompt mira 450–600), 3–7 seções, 2–10 segmentos por seção, no máximo 60 segmentos.
- Legendas por formato — short: tamanho 88, margem_v 560, 3 palavras, 1,6 s; medio: 60, 70, 6, 2,5. Margens laterais 60 px e contorno 6 px (valores atuais do estilo ASS).
- Capítulos: o primeiro em 0, pelo menos 3, cada um com pelo menos 10 s.
- Thumbnail: `thumbnail.jpg`, 1280×720, JPEG qualidade 90, menos de 2 000 000 bytes.
- Visuais: `Canal.visuais` aceita `pixabay` e `flux` (padrão `[pixabay]`); o procedural é sempre o último e a revisão reprova `visuais` quando ele aparece.
- Estilo visual — `realista`: `video_type=film`, `image_type=photo`, sufixo FLUX `photorealistic photo, natural light, no text, no watermark`; `desenho`: `video_type=animation`, `image_type=illustration`, sufixo `colorful 2D cartoon illustration, clean lines, flat shading, no text, no watermark`. `isLowQuality` sempre fora; `isAiGenerated` fora no `realista`.
- Curadoria: até 4 vídeos + até 4 imagens por trecho, no máximo 6 candidatos, vídeos primeiro; folha 3×2; modelo `claude.modelos.curadoria` (`sonnet`), ferramenta só `Read`, `max_turns.curadoria` 25; até 15 trechos por chamada.
- FLUX: só o **FLUX.2 [klein] 4B** (Apache 2.0), nunca o 9B. Registro: licença `gerado localmente (FLUX.2 klein 4B, Apache 2.0)`, origem `flux`, autor `autocanal`, url `flux:<hash8>`. Padrões: 4 passos, 1360×768 (retrato inverte), RAM livre mínima 6 GB, VRAM livre mínima 7 GB, timeout 180 s + 45 s por item.
- Créditos na descrição, exatos: `Imagens: Pixabay` e `Algumas imagens deste vídeo foram geradas por IA.`
- Upload: `selfDeclaredMadeForKids = canal.feito_para_criancas`; `containsSyntheticMedia` ligado quando há mídia do FLUX (a revisão liga, nunca desliga).
- Commits terminam com `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. Lote do FLUX que estoura o timeout (60 trechos) → `ErroProvedor`, os trechos seguem a cadeia e a etapa não trava. Teste: Task 8 `test_timeout_vira_erro`.
2. Curadoria devolve índice fora da faixa, id desconhecido ou o mesmo candidato para dois trechos → tratado como "nenhum", sem quebrar nem repetir mídia. Testes: Task 4 `test_curadoria_indice_invalido_vira_nenhum` e `test_curadoria_nao_repete_candidato`.
3. Primeira seção curta (gancho de 6 s) → capítulos continuam começando em 00:00, sem capítulo < 10 s. Teste: Task 6 `test_capitulos_funde_primeira_curta`.
4. Texto de thumbnail com palavra longa ("INTERFERÊNCIA") → cabe no quadro, a fonte encolhe. Teste: Task 7 `test_thumbnail_palavra_longa_cabe`.
5. Veto no canal infantil (`videos.update` substitui o `status` inteiro) → não pode voltar `selfDeclaredMadeForKids` para false. Teste: Task 2 `test_vetar_repassa_feito_para_criancas`.

---

### Task 1: Formato `medio` e legendas que cabem no quadro

**Files:**
- Modify: `autocanal/formatos.py`, `autocanal/legendas.py`, `autocanal/etapas/legendas.py`
- Test: `tests/test_formatos.py` (novo), `tests/test_legendas.py`, `tests/test_etapas_iniciais.py`

**Interfaces:**
- Produces:
  - `Formato` ganha campos obrigatórios por palavra-chave depois de `etapas`: `legenda_tamanho: int`, `legenda_margem_v: int`, `legenda_max_palavras: int`, `legenda_max_duracao_s: float`.
  - `FORMATOS["medio"] = Formato("medio", 1920, 1080, 60, 300, 150, 750, "paisagem", ("roteiro", "narracao", "legendas", "visuais", "render", "metadados", "thumbnail", "revisao"), legenda_tamanho=60, legenda_margem_v=70, legenda_max_palavras=6, legenda_max_duracao_s=2.5)`; o `short` recebe 88/560/3/1.6.
  - `legendas.MARGEM_LATERAL = 60`, `legendas.CONTORNO = 6` (usados no estilo do `gerar_ass`, que hoje tem esses números fixos).
  - `legendas.largura_util(largura: int) -> int` = `largura − 2·MARGEM_LATERAL − 2·CONTORNO`.
  - `legendas.medidor(fonte_arquivo: Path, tamanho: int) -> Callable[[str], float]` (Pillow `ImageFont.truetype(...).getlength`).
  - `legendas.agrupar(palavras, max_palavras=3, max_duracao_s=1.6, *, largura_max: float | None = None, medir: Callable[[str], float] | None = None)` — além das regras atuais, fecha o grupo antes de uma palavra que faria `medir(texto.upper()) > largura_max`; uma palavra sozinha mais larga fica sozinha no grupo.

- [ ] **Step 1: Testes que falham**
  - `test_formato_medio`: `FORMATOS["medio"]` com `(1920, 1080)`, `(60, 300)`, `(150, 750)`, `"paisagem"` e a tupla de etapas acima, exatamente nessa ordem.
  - `test_formato_short_legendas_inalteradas`: short com `(88, 560, 3, 1.6)`.
  - `test_agrupar_fecha_por_largura`: palavras `velocidade num computador` (tempos curtos), `medir=lambda t: 40 * len(t)`, `largura_max=600` → textos `["velocidade num", "computador"]`.
  - `test_agrupar_palavra_sozinha_larga`: `medir=lambda t: 10_000` → um grupo por palavra, sem laço infinito.
  - `test_agrupar_sem_medidor_igual_antes`: sem `medir`, mesmo resultado do comportamento atual para uma frase de 7 palavras.
  - `test_legendas_ass_usa_formato` (em `test_etapas_iniciais.py`, com `transcrever` substituído como em `test_legendas_usa_transcricao`): contexto `medio` → `legendas.ass` contém `Style: Legenda,DejaVu Sans,60,` e o estilo termina em `,60,60,70,1`.
- [ ] **Step 2:** `uv run pytest tests/test_formatos.py tests/test_legendas.py tests/test_etapas_iniciais.py -q` → FALHA nos testes novos.
- [ ] **Step 3:** Implementar. A etapa passa `largura_max=largura_util(fmt.largura)` e `medir=medidor(canal.fonte_arquivo, fmt.legenda_tamanho)`; remover `TAMANHO` e `MARGEM_V` de `etapas/legendas.py`.
- [ ] **Step 4:** `uv run pytest -q` → PASSA.
- [ ] **Step 5:** Commit `feat: formato medio e legendas limitadas pela largura do quadro`.

---

### Task 2: Campos novos do canal e "feito para crianças" no upload

**Files:**
- Modify: `autocanal/config.py`, `autocanal/youtube/upload.py`, `autocanal/publicador.py`, `autocanal/cli.py` (comando `vetar`)
- Test: `tests/test_config.py`, `tests/test_upload.py`, `tests/test_publicador.py`, `tests/test_cli_youtube.py`

**Interfaces:**
- Produces:
  - `Canal.feito_para_criancas: bool = False`
  - `Canal.visuais: list[Literal["pixabay", "flux"]] = ["pixabay"]`
  - `Canal.estilo_visual: Literal["realista", "desenho"] = "realista"`
  - `Canal.estilo_ia: str = ""`
  - `upload.montar_corpo(meta, *, publish_at, categoria_id, idioma, sintetico, feito_para_criancas: bool) -> dict`
  - `upload.vetar(youtube, video_id, sintetico: bool = False, feito_para_criancas: bool = False) -> None`

- [ ] **Step 1: Testes que falham**
  - `test_canal_campos_padrao(cfg)`: canal `teste` → `feito_para_criancas is False`, `visuais == ["pixabay"]`, `estilo_visual == "realista"`, `estilo_ia == ""`.
  - `test_canal_visuais_invalido`: `visuais: [pexels]` em `canais.yaml` → `pytest.raises(ValidationError)` ao carregar; idem `estilo_visual: aquarela`.
  - `test_montar_corpo_feito_para_criancas`: `feito_para_criancas=True` → `status.selfDeclaredMadeForKids is True` (o teste atual passa a mandar `False` e conferir `False`).
  - `test_vetar_repassa_feito_para_criancas`: `vetar(yt, "abc", False, True)` → corpo do `update` com `selfDeclaredMadeForKids is True`.
  - `test_publicar_repassa_feito_para_criancas` (publicador): canal com `feito_para_criancas=True` → `montar_corpo` recebe `feito_para_criancas=True`.
- [ ] **Step 2:** `uv run pytest tests/test_config.py tests/test_upload.py tests/test_publicador.py tests/test_cli_youtube.py -q` → FALHA.
- [ ] **Step 3:** Implementar; o `vetar` da CLI passa o campo do canal do job.
- [ ] **Step 4:** `uv run pytest -q` → PASSA.
- [ ] **Step 5:** Commit `feat: canal com feito_para_criancas, visuais, estilo visual e estilo de IA`.

---

### Task 3: Candidatos do Pixabay, folha de contato e cadeia em lote

**Files:**
- Modify: `autocanal/provedores/imagens.py`, `autocanal/etapas/visuais.py`
- Test: `tests/test_imagens.py`, `tests/test_etapas_iniciais.py`

**Interfaces:**
- Consumes: `Canal.visuais`, `Canal.estilo_visual` (Task 2).
- Produces:
  - `MidiaObtida` ganha `prompt: str = ""` e `motivo: str = ""` (últimos campos; entram no `como_dict`).
  - `Pedido` (dataclass frozen): `id: str, consulta: str, texto: str, cena: str, destino_sem_ext: Path`.
  - `Candidato` (dataclass frozen): `tipo: Literal["imagem", "video"], pagina: str, preview: str, download: str, autor: str`.
  - `PixabayCandidatos(api_key, sessao=requests)`:
    - `buscar(consulta, orientacao, estilo, excluir: set[str], maximo: int = 6) -> list[Candidato]` — vídeos (`video_type` do estilo, `per_page=20`, rendição pela regra de orientação atual, `preview` = `thumbnail` da rendição) até 4, depois imagens (`image_type` do estilo, `orientation`, `per_page=20`, `preview = webformatURL`, `download = largeImageURL`) até completar `maximo` (no máximo 4); descarta `isLowQuality`, `isAiGenerated` no `realista` e `pagina in excluir`. Erros com a mesma regra de hoje (sem vazar a chave).
    - `baixar(c: Candidato, destino_sem_ext: Path) -> MidiaObtida` (`.mp4` ou `.jpg`, `origem "pixabay"`, licença `Pixabay Content License`).
  - `folha_contato(previews: list[Path], tipos: list[str], saida: Path) -> Path` — JPEG 1200×600, grade 3×2 de células 400×300 (cobre e corta), número 1..n grande sobre fundo escuro no canto superior esquerdo de cada célula, faixa `VÍDEO` nas que são vídeo.
  - `ProvedorLote(Protocol)`: `nome: str`; `buscar_lote(pedidos: list[Pedido], orientacao: Orientacao, excluir: set[str]) -> dict[str, MidiaObtida]` (ids sem mídia ficam de fora; `ErroProvedor` = o lote inteiro falhou).
  - `criar_cadeia(cfg, canal, largura, altura, *, pixabay: ProvedorLote | None = None, flux: ProvedorLote | None = None) -> list` — segue `canal.visuais`, pula os `None`, e acrescenta `FundoProcedural` no fim.
  - `resolver(cadeia, pedidos, orientacao, excluir) -> dict[str, MidiaObtida]` — cada provedor recebe todos os pedidos ainda sem mídia (lote: uma chamada; `FundoProcedural`: um `buscar` por pedido); as URLs obtidas entram em `excluir`; `ErroProvedor` → aviso no log e segue; pedido sem mídia no fim → `ErroProvedor`.
  - `PixabayImagens`, `ProvedorImagens` (exceto o que o procedural precisa) e `buscar_em_cadeia` saem.
  - `visuais.executar` monta `Pedido(f"seg_{i:02d}", seg["visual"], seg["texto"], seg.get("cena", ""), pasta / f"seg_{i:02d}")` e grava `fontes_midia.json` na ordem dos segmentos. Até a Task 4 ligar a curadoria, chama `criar_cadeia` sem `pixabay` (só procedural).

- [ ] **Step 1: Testes que falham**
  - `test_candidatos_filtros_realista` e `test_candidatos_filtros_desenho`: parâmetros enviados `video_type`/`image_type` conforme a tabela das Global Constraints.
  - `test_candidatos_descarta_baixa_qualidade_e_ia`: hit `isLowQuality` sempre fora; hit `isAiGenerated` fora no `realista` e mantido no `desenho`.
  - `test_candidatos_ordem_e_limite`: 5 vídeos e 5 imagens válidos → 4 vídeos e depois 2 imagens.
  - `test_candidatos_respeita_orientacao_e_excluir`: vídeo deitado num pedido retrato e página em `excluir` ficam fora.
  - `test_candidatos_chave_fora_do_erro` (adaptado do teste atual).
  - `test_baixar_video_e_imagem`.
  - `test_folha_contato`: 6 previews → JPEG 1200×600; com 2 previews também gera.
  - `test_criar_cadeia_segue_ordem_do_canal`: `visuais = ["flux", "pixabay"]` com dois dublês → `[flux, pixabay, FundoProcedural]`; `test_criar_cadeia_sem_provedores_so_procedural`.
  - `test_resolver_lote_recebe_so_pendentes`, `test_resolver_erro_do_lote_segue` (aviso com o nome do provedor), `test_resolver_mantem_excluir` (o segundo lote recebe as URLs do primeiro).
- [ ] **Step 2:** `uv run pytest tests/test_imagens.py tests/test_etapas_iniciais.py -q` → FALHA.
- [ ] **Step 3:** Implementar.
- [ ] **Step 4:** `uv run pytest -q` → PASSA.
- [ ] **Step 5:** Commit `refactor: candidatos do Pixabay, folha de contato e cadeia de visuais em lote`.

---

### Task 4: Curadoria pelo Claude e procedural reprovado

**Files:**
- Create: `autocanal/provedores/curadoria.py`, `autocanal/prompts/curadoria.md`, `tests/test_curadoria.py`
- Modify: `autocanal/etapas/visuais.py`, `autocanal/etapas/revisao.py`, `autocanal/prompts/revisao.md`, `config/autocanal.yaml` (`modelos.curadoria: sonnet`, `max_turns.curadoria: 25`)
- Test: `tests/test_etapas_iniciais.py`, `tests/test_revisao.py`

**Interfaces:**
- Consumes: `Pedido`, `Candidato`, `PixabayCandidatos`, `folha_contato`, `ProvedorLote`, `criar_cadeia(..., pixabay=)` (Task 3); `claude.chamar`, `claude.registrar`, `ErroClaude`, `LimiteUsoClaude`.
- Produces:
  - `ItemCuradoria` (dataclass frozen): `id: str, texto: str, cena: str, folha: Path, n: int`.
  - `CuradoriaPixabay(fonte: PixabayCandidatos, escolher: Callable[[list[ItemCuradoria]], dict[str, tuple[int, str]]], estilo: str, pasta: Path, sessao=requests)` — `nome = "pixabay"`; `buscar_lote(pedidos, orientacao, excluir)`: para cada pedido busca candidatos (`ErroProvedor` num trecho → trecho sem candidatos, com aviso), baixa as previews em `pasta/curadoria/seg_XX_k.jpg`, monta `pasta/curadoria/seg_XX.jpg`; chama `escolher` só com os trechos que têm candidatos, em lotes de até 15; baixa os escolhidos (índice 1..n); índice 0, fora da faixa, id desconhecido ou candidato já usado em outro trecho → trecho fica de fora. A `MidiaObtida` leva o `motivo`.
  - `CURADORIA` (schema): `{escolhas: [{id: string, candidato: integer ≥ 0, motivo: string}]}`, `additionalProperties: false`.
  - `prompts/curadoria.md`: campos `{estilo}` e `{itens}`; pede para abrir cada folha com `Read`, e escolher o candidato que **mostra o que está sendo dito naquela frase** (metáfora só se óbvia); recusar logotipos e marcas visíveis, texto legível, pessoas famosas e conteúdo fora do estilo; entre dois igualmente bons, preferir vídeo; não repetir candidato; `0` quando nenhum serve; não fazer perguntas.
  - `visuais.escolher_com_claude(ctx) -> Callable[[list[ItemCuradoria]], dict[str, tuple[int, str]]]` — cada lote é uma chamada `claude.chamar(prompt, CURADORIA, ["Read"], ctx.pasta, modelo=modelos.get("curadoria"), max_turns=max_turns.get("curadoria", 25), …)` registrada com `claude.registrar(ctx.con, job, "visuais", r)`; `LimiteUsoClaude` propaga; outro `ErroClaude` vira `ErroEtapa`.
  - `visuais.executar` passa `pixabay=CuradoriaPixabay(...)` quando há `PIXABAY_API_KEY` e `"pixabay" in canal.visuais`.
  - `revisao.checagens`: cada trecho com origem `procedural` em `fontes_midia.json` → problema ``(f"trecho {n} sem imagem relacionada à fala (fundo procedural)", "visuais")``.
  - `prompts/revisao.md`: a relação entre o visual e a fala vem como primeiro critério.

- [ ] **Step 1: Testes que falham**
  - `test_curadoria_baixa_so_escolhidos`: 3 candidatos no `seg_01`, `escolher` devolve `(2, "mostra roteador")` → mídia do candidato 2 com `motivo == "mostra roteador"`; os outros não são baixados.
  - `test_curadoria_nenhum_fica_pendente`: candidato 0 → `seg_01` fora do resultado.
  - `test_curadoria_indice_invalido_vira_nenhum`: candidato 7 com 3 candidatos, e um id que não foi pedido → ambos ignorados.
  - `test_curadoria_trecho_sem_candidatos_nao_vai_ao_claude`.
  - `test_curadoria_lotes_de_15`: 20 trechos com candidatos → `escolher` chamado com 15 e depois 5.
  - `test_curadoria_nao_repete_candidato`: dois trechos escolhem a mesma página → só o primeiro recebe.
  - `test_escolher_com_claude_usa_modelo_e_read` (Claude falso): modelo `sonnet`, ferramentas `["Read"]`, prompt contém a fala, a `cena` e o caminho da folha.
  - `test_escolher_erro_claude_vira_erro_etapa` e `test_escolher_limite_uso_propaga`.
  - `test_revisao_reprova_procedural`: `fontes_midia.json` com o trecho 2 procedural → problema com etapa `visuais` e o número do trecho.
- [ ] **Step 2:** `uv run pytest tests/test_curadoria.py tests/test_etapas_iniciais.py tests/test_revisao.py -q` → FALHA.
- [ ] **Step 3:** Implementar.
- [ ] **Step 4:** `uv run pytest -q` → PASSA.
- [ ] **Step 5:** Commit `feat: curadoria dos visuais pelo Claude e procedural reprovado na revisão`.

---

### Task 5: Roteiro — uma frase por segmento, seções no médio e `cena`

**Files:**
- Create: `autocanal/prompts/roteiro_medio.md`
- Modify: `autocanal/prompts/roteiro_short.md`, `autocanal/etapas/roteiro.py`
- Test: `tests/test_etapas_iniciais.py`

**Interfaces:**
- Produces:
  - `ROTEIRO_SHORT`: `segmentos` com 6–16 itens, cada um exigindo `texto`, `visual` e `cena`.
  - `ROTEIRO_MEDIO`: `{gancho, nota_gancho, secoes (3–7): {titulo, segmentos (2–10): {texto, visual, cena}}, fontes, resumo}`, `additionalProperties: false` em todos os níveis.
  - `achatar(dados: dict) -> dict` — devolve `{gancho, nota_gancho, secoes: [títulos], segmentos: [{texto, visual, cena, secao: <índice>}], fontes, resumo}`; mais de 60 segmentos → `ErroEtapa`.
  - `executar(ctx)` escolhe o prompt `roteiro_<formato>` e o schema por formato (`{"short": ROTEIRO_SHORT, "medio": ROTEIRO_MEDIO}`); no médio, achata antes de contar palavras e gravar `roteiro.json`.
  - Os dois prompts: **cada segmento é uma única frase** (o visual troca a cada frase); `cena` com 15 a 40 palavras em inglês, descrevendo concretamente o que a imagem deve mostrar para ilustrar aquela frase, sem texto na imagem, marcas, logotipos ou pessoas reais. `roteiro_medio.md` tem as mesmas regras de trabalho 1–9 e os mesmos campos de formatação do `roteiro_short.md` e pede vídeo **horizontal de 3 a 4 minutos (450 a 600 palavras)**, com seções de título curto (até 40 caracteres) que viram capítulos.

- [ ] **Step 1: Testes que falham**
  - `test_achatar_secoes`: seções com 2, 3 e 2 segmentos → 7 segmentos com `secao == [0, 0, 1, 1, 1, 2, 2]` e `secoes` = os 3 títulos.
  - `test_achatar_mais_de_60_falha`: 7 seções × 9 segmentos → `ErroEtapa`.
  - `test_roteiro_medio_usa_prompt_e_schema`: Claude falso, contexto `medio` → prompt contém `3 a 4 minutos`, schema `ROTEIRO_MEDIO`, `roteiro.json` com `secoes` e `segmentos[*].secao`.
  - `test_schema_short_exige_cena_e_ate_16`: `required` do item contém `"cena"`; `maxItems == 16` e `minItems == 6`.
  - `test_prompts_pedem_uma_frase`: os dois prompts contêm `uma única frase`.
- [ ] **Step 2:** `uv run pytest tests/test_etapas_iniciais.py -q` → FALHA.
- [ ] **Step 3:** Implementar (os testes atuais do Short que montam roteiros falsos ganham `cena` e passam a ter 6 segmentos).
- [ ] **Step 4:** `uv run pytest -q` → PASSA.
- [ ] **Step 5:** Commit `feat: roteiro com uma frase por visual, seções no médio e cena`.

---

### Task 6: Metadados — capítulos calculados, créditos, `cena_thumb` e mídia sintética

**Files:**
- Modify: `autocanal/etapas/metadados.py`, `autocanal/prompts/metadados.md`, `autocanal/etapas/revisao.py`
- Test: `tests/test_metadados.py`, `tests/test_revisao.py`

**Interfaces:**
- Consumes: `roteiro.json` com `secoes`/`segmentos[*].secao` (Task 5); `fontes_midia.json` com `origem` (Tasks 3–4).
- Produces:
  - `METADADOS` exige `cena_thumb` (string; o prompt pede 15–40 palavras em inglês, mesmas regras da `cena`, pensada como fundo de thumbnail com espaço livre à esquerda).
  - `capitulos_calculados(roteiro: dict, narracao: dict, minimo_s: float = 10.0) -> list[dict]` — cada seção começa no `inicio` do seu primeiro segmento; a última vai até `narracao["duracao"]`. Seção mais curta que `minimo_s` é fundida à anterior; se for a primeira, funde-se à seguinte e o capítulo começa em 0 com o título da seguinte. Menos de 3 → `[]`.
  - `creditos(fontes: list[dict]) -> str` — `Imagens: Pixabay` se alguma origem é `pixabay`; `Algumas imagens deste vídeo foram geradas por IA.` se alguma é `flux`; juntos por `\n`; vazio se nenhuma.
  - `descricao_final(descricao, capitulos, urls, formato, creditos: str = "")` — o bloco de créditos entra antes de `Fontes:`.
  - `metadados.json` ganha `cena_thumb`; no médio, `capitulos` vêm de `capitulos_calculados` (os do Claude são ignorados); `sintetico` = existe mídia `flux`.
  - `revisao.executar`: `meta["sintetico"] = bool(meta.get("sintetico")) or resultado["contains_synthetic_media"]`.

- [ ] **Step 1: Testes que falham**
  - `test_capitulos_basico`: 3 seções em 0, 40 e 95 s, duração 150 → tempos `[0, 40, 95]` com os títulos das seções.
  - `test_capitulos_funde_curta_do_meio`: seções em 0, 40, 45, 100 (duração 160) → a seção de 40–45 s (5 s) funde-se à anterior → tempos `[0, 45, 100]`, com os títulos das seções 0, 2 e 3.
  - `test_capitulos_funde_primeira_curta`: seções em 0, 6, 60, 120 (duração 180) → tempos `[0, 60, 120]`, o primeiro com o título da segunda seção.
  - `test_capitulos_menos_de_3_vazio`: 2 seções → `[]`.
  - `test_creditos`: só pixabay → `"Imagens: Pixabay"`; só flux → a frase de IA; ambos → as duas linhas; só procedural → `""`.
  - `test_descricao_creditos_antes_de_fontes`.
  - `test_metadados_medio_calcula_capitulos_e_sintetico`: Claude falso com `capitulos` próprios e `fontes_midia.json` com uma mídia `flux` → capítulos calculados, `sintetico is True`, `cena_thumb` gravado.
  - `test_revisao_nao_desliga_sintetico`.
- [ ] **Step 2:** `uv run pytest tests/test_metadados.py tests/test_revisao.py -q` → FALHA.
- [ ] **Step 3:** Implementar.
- [ ] **Step 4:** `uv run pytest -q` → PASSA.
- [ ] **Step 5:** Commit `feat: capítulos calculados, créditos de imagem e marcação de mídia sintética`.

---

### Task 7: Etapa thumbnail, envio em JPEG e alerta de miniatura recusada

**Files:**
- Create: `autocanal/etapas/thumbnail.py`, `tests/test_thumbnail.py`
- Modify: `autocanal/etapas/__init__.py`, `autocanal/publicador.py`, `autocanal/relatorio.py`, `autocanal/etapas/revisao.py`, `autocanal/prompts/revisao.md`
- Test: `tests/test_publicador.py`, `tests/test_relatorio.py`, `tests/test_revisao.py`

**Interfaces:**
- Consumes: `metadados.json` (`texto_thumb`, `cena_thumb`, `titulo`), `fontes_midia.json`, `FundoProcedural`, `Midia`.
- Produces:
  - `layout_texto(texto: str, fonte_arquivo: Path, largura_caixa: int, altura_caixa: int) -> tuple[int, list[str]]` — caixa alta, até 2 linhas equilibradas; o tamanho começa em 160 e desce de 8 em 8 até toda linha caber na largura e o bloco na altura.
  - `compor_thumbnail(base: Path, texto: str, canal: Canal, saida: Path) -> Path` — base em 1280×720 (cobre e corta ao centro); caixa de texto no terço esquerdo (margens de 48 px, largura 45% e altura 60% do quadro); texto na cor `destaque` com contorno preto de 10 px; JPEG qualidade 90, baixando de 10 em 10 (mínimo 60) se passar de 2 000 000 bytes.
  - `imagem_base(ctx, meta: dict) -> tuple[Path, str]` (caminho, origem) — primeiro visual não procedural de `fontes_midia.json` (vídeo → quadro do meio via ffmpeg); senão `FundoProcedural` 1280×720 com a consulta = título. (A Task 8 põe o FLUX antes.)
  - `executar(ctx)` grava `thumbnail.jpg` e `thumbnail.json` `{"base_origem": ...}`; `REGISTRO["thumbnail"]`.
  - `publicador` envia `thumbnail.jpg` e grava `thumbnail_envio.json` `{"ok": bool, "erro": str}` (erro cortado em 200 caracteres) depois de tentar.
  - `relatorio.alertas`: job com `thumbnail_envio.json` `ok: false` → ``Miniatura de `<id>` não foi enviada: verifique o telefone do canal em youtube.com/verify.``
  - `revisao.checagens`: formato com etapa `thumbnail` → problema `("thumbnail …", "thumbnail")` se `thumbnail.jpg` faltar, não tiver 1280×720 ou tiver 2 000 000 bytes ou mais. O prompt do revisor pede para abrir `thumbnail.jpg` com `Read` quando existir.

- [ ] **Step 1: Testes que falham**
  - `test_compor_thumbnail_dimensoes_e_peso`.
  - `test_thumbnail_palavra_longa_cabe`: `layout_texto("INTERFERÊNCIA NO WI-FI", fonte, 576, 432)` → toda linha medida ≤ 576 e o tamanho < 160.
  - `test_thumbnail_base_usa_visual`: `fontes_midia.json` com procedural e depois uma imagem pixabay → origem `pixabay`.
  - `test_thumbnail_base_video_extrai_quadro`: vídeo de 2 s gerado com o ffmpeg do projeto (`testsrc`) → quadro extraído existe.
  - `test_thumbnail_base_procedural_sem_visuais`.
  - `test_publicar_envia_thumbnail_jpg` (adaptar a fixture que hoje cria `thumbnail.png`) e `test_thumbnail_recusada_grava_envio`: `definir_thumbnail` levanta `ErroUpload` → `thumbnail_envio.json` com `ok: false`, vídeo segue `agendado`.
  - `test_alerta_thumbnail_nao_enviada`.
  - `test_revisao_reprova_thumbnail_errada`: médio com `thumbnail.jpg` 640×360 → problema com etapa `thumbnail`.
- [ ] **Step 2:** `uv run pytest tests/test_thumbnail.py tests/test_publicador.py tests/test_relatorio.py tests/test_revisao.py -q` → FALHA.
- [ ] **Step 3:** Implementar.
- [ ] **Step 4:** `uv run pytest -q` → PASSA.
- [ ] **Step 5:** Commit `feat: etapa thumbnail, envio em JPEG e alerta de miniatura recusada`.

---

### Task 8: Cliente do FLUX (subprocesso em lote) com portões de memória

**Files:**
- Create: `autocanal/provedores/flux.py`, `tests/test_flux.py`, `tests/fakes/flux_falso.py`
- Modify: `autocanal/config.py`, `config/autocanal.yaml`, `autocanal/legendas.py`, `autocanal/etapas/visuais.py`, `autocanal/etapas/thumbnail.py`, `autocanal/cli.py` (diagnóstico)
- Test: `tests/test_etapas_iniciais.py`, `tests/test_thumbnail.py`, `tests/test_cli_operacao.py`

**Interfaces:**
- Consumes: `Pedido`, `MidiaObtida`, `ProvedorLote`, `criar_cadeia(..., flux=)` (Task 3); `Canal.visuais`, `Canal.estilo_visual`, `Canal.estilo_ia` (Task 2); `imagem_base` (Task 7).
- Produces:
  - `ConfigFlux` (pydantic) em `Config.flux`: `projeto: Path = Path("servicos/flux")` (relativo à raiz), `uv: Path` (em `autocanal.yaml`: `/home/losawa/.local/share/autocanal-tools/bin/uv`), `passos: int = 4`, `largura: int = 1360`, `altura: int = 768`, `ram_minima_gb: float = 6.0`, `vram_minima_gb: float = 7.0`, `timeout_base_s: int = 180`, `timeout_por_item_s: int = 45`, `sufixos: dict[str, str]` com as chaves `realista` e `desenho` (textos das Global Constraints).
  - `flux.instalado(cfg) -> bool` — existem `<projeto>/pyproject.toml` e `<projeto>/gerar.py`.
  - `flux.memoria_livre() -> tuple[float, float]` — (RAM livre em GB, de `MemAvailable` em `/proc/meminfo`; VRAM livre em GB, de `nvidia-smi --query-gpu=memory.free`; 0 se falhar).
  - `flux.prompt_final(cena: str, estilo_ia: str, sufixo: str) -> str` — junta as partes não vazias com `", "`.
  - `flux.semente(job_id: str, item_id: str, rodada: int) -> int` — sha256 → inteiro < 2³¹.
  - `GeradorFlux(cfg, *, job_id: str, rodada: int, estilo_visual: str, estilo_ia: str = "", comando_base: list[str] | None = None, executar=subprocess.run, memoria=memoria_livre)` — `nome = "flux"`; `buscar_lote(pedidos, orientacao, excluir)`: confere memória (abaixo do mínimo → `ErroProvedor("memória insuficiente …")` sem rodar nada); grava `entrada.json` (`largura`/`altura` trocadas no retrato, `passos`, `itens` com `id`, prompt final da `cena` e `semente`) numa pasta `flux_lote/` ao lado dos destinos; roda `comando_base + [entrada, saida]` (padrão `[uv, "run", "--project", <projeto>, "python", <projeto>/gerar.py]`) com `timeout = timeout_base_s + timeout_por_item_s × itens`; código ≠ 0 ou timeout → `ErroProvedor`; lê `resultado.json`, move `<id>.png` de cada item `ok` para `destino_sem_ext + ".png"` e devolve as `MidiaObtida` (registro das Global Constraints, `prompt` preenchido).
  - `legendas.liberar_modelos() -> None` — esvazia o cache de modelos do Whisper e chama `gc.collect()`.
  - `visuais.executar`: com `"flux" in canal.visuais` e `instalado(cfg)`, antes de resolver chama `legendas.liberar_modelos()` e para o Kokoro (`servicos.parar("kokoro", raiz)` quando o compose existe; falha só vira aviso), e passa `GeradorFlux(...)` (rodada = `ctx.job.tentativas_revisao`) para `criar_cadeia`; com o FLUX na ordem mas não instalado, registra um aviso e segue sem ele.
  - `thumbnail.imagem_base`: com o FLUX na ordem e instalado, tenta antes um lote de 1 item com `cena_thumb` em paisagem.
  - Diagnóstico: `[OK   ] flux (servicos/flux)` ou `[FALHA] flux: não instalado (opcional)`.
  - `tests/fakes/flux_falso.py`: lê `entrada.json`, grava um PNG por item e o `resultado.json`; prompt com `FALHAR` → item `ok: false`; com `TRAVAR` → dorme 5 s; com `QUEBRAR` → sai com código 3.

- [ ] **Step 1: Testes que falham** (com `comando_base=[sys.executable, "tests/fakes/flux_falso.py"]` e `memoria=lambda: (16.0, 8.0)`, salvo indicação)
  - `test_lote_gera_midias`: 2 pedidos → 2 `MidiaObtida` com PNG no destino, `origem == "flux"`, `prompt` com o sufixo do `realista`.
  - `test_sufixo_desenho`: `estilo_visual="desenho"` → prompt com `2D cartoon illustration`.
  - `test_item_falhou_fica_de_fora`, `test_timeout_vira_erro` (`timeout_base_s=1`, `timeout_por_item_s=0`, `TRAVAR`), `test_saida_nao_zero_vira_erro` (`QUEBRAR`).
  - `test_sem_memoria_pula_sem_rodar`: `memoria=lambda: (2.0, 8.0)` → `ErroProvedor` e `executar` nunca chamado.
  - `test_retrato_inverte_dimensoes`: `entrada.json` com `largura == 768` e `altura == 1360`.
  - `test_semente_muda_com_rodada`: determinística e diferente entre as rodadas 0 e 1.
  - `test_visuais_libera_memoria_antes_do_flux`, `test_visuais_flux_nao_instalado_segue`, `test_thumbnail_usa_flux_primeiro`, `test_diagnostico_mostra_flux`.
- [ ] **Step 2:** `uv run pytest tests/test_flux.py tests/test_etapas_iniciais.py tests/test_thumbnail.py tests/test_cli_operacao.py -q` → FALHA.
- [ ] **Step 3:** Implementar.
- [ ] **Step 4:** `uv run pytest -q` → PASSA.
- [ ] **Step 5:** Commit `feat: cliente do FLUX em lote com portões de memória`.

---

### Task 9: Configuração, documentação e prova real do médio com curadoria

**Files:**
- Modify: `config/canais.yaml`, `config/canais.yaml.exemplo`, `config/autocanal.yaml`, `README.md`, `CLAUDE.md`

- [ ] **Step 1:** `config/canais.yaml`: `conteudo` com `visuais: [pixabay, flux]`, `estilo_visual: realista` e a grade acrescida de `{formato: medio, dias: [ter, qui], hora: "18:00"}`; `infantil` com `estilo_visual: desenho` e `feito_para_criancas: true` (grade continua vazia). O exemplo documenta os campos novos.
- [ ] **Step 2:** README: formato `medio` (`autocanal novo <canal> medio "<tema>"`), curadoria e estilo visual, campos novos do canal; no checklist de Laura: restrição de API da chave do TTS, youtube.com/verify, país do canal, sonda e instalação do FLUX fora da rede da empresa. `CLAUDE.md`: formato médio, curadoria, etapa thumbnail.
- [ ] **Step 3:** `uv run pytest -q` → PASSA; `autocanal diagnostico` → `flux` em FALHA (ainda não instalado), resto como antes.
- [ ] **Step 4: Prova real (custa uso do Claude; nunca enviar):** `autocanal novo conteudo medio "<tema do nicho>" --quando "<data ≥ 7 dias à frente> 18:00"` e `autocanal produzir <id>`. Conferir: `video.mp4` 1920×1080 e 60–300 s (ffprobe); um mosaico de quadros mostrando que cada visual combina com a frase falada; legendas dentro do quadro; folhas em `visuais/curadoria/`; `thumbnail.jpg` 1280×720 < 2 MB; descrição com capítulos e `Imagens: Pixabay`; estado final `aprovado` ou `quarentena` com motivos legíveis (quarentena por procedural é esperada nos trechos que o Pixabay não cobre, enquanto o FLUX não estiver instalado). Registrar o resultado e o mosaico no ledger. Defeito de código → corrigir com teste antes de seguir.
- [ ] **Step 5:** Commit `chore: médio na grade do conteudo, estilos dos canais e docs`.

---

### Task 10: Passo 0 — sonda do FLUX (portão)

Precisa de rede fora da empresa. Se `echo | openssl s_client -connect huggingface.co:443 -servername huggingface.co 2>/dev/null | grep issuer` mostrar Fortinet, registre `Task 10: bloqueada (rede)` no ledger e pare aqui: as Tasks 10 e 11 ficam para quando Laura estiver em outra rede (as Tasks 1–9 já entregam o médio com curadoria).

**Files:**
- Create (descartável, fora do git): `dados/sonda-flux/` (projeto uv, já iniciado), `dados/amostras/flux/`

- [ ] **Step 1:** Listar os repositórios oficiais (`https://huggingface.co/api/models?author=black-forest-labs&search=klein`) e confirmar no model card do 4B a licença Apache 2.0; anotar o id do repositório e se há pesos fp8 em formato diffusers.
- [ ] **Step 2:** `uv sync` do projeto da sonda (torch cu128, diffusers com `Flux2KleinPipeline`, transformers, accelerate, bitsandbytes, psutil).
- [ ] **Step 3:** Script da sonda em duas fases (codifica os prompts → libera o encoder → carrega o transformer e gera). Tentar nesta ordem até uma passar: (a) encoder em 4 bits na GPU + transformer em fp8 (pesos fp8 ou `enable_layerwise_casting` com armazenamento fp8); (b) a mesma com `enable_model_cpu_offload`. Medir pico de VRAM, pico de RAM do processo, carga e segundos por imagem em 1360×768 e 768×1360 (e 1920×1088 se couber).
- [ ] **Step 4:** Critérios: VRAM ≤ 7,5 GB, RAM do processo ≤ 6 GB, ≤ 30 s por imagem. Salvar 4 amostras (2 `realista`, 2 `desenho`, com `cena`s de um roteiro real) em `dados/amostras/flux/` e a tabela em `dados/sonda-flux/resultado.md`. Passou → ledger `Task 10: go (<configuração vencedora>)`; não passou → ledger `Task 10: no-go (<motivo>)`, tirar `flux` do `conteudo` (commit), pular a Task 11 e avisar Laura no fim.

---

### Task 11: Serviço FLUX real e prova com FLUX

**Files:**
- Create: `servicos/flux/pyproject.toml`, `servicos/flux/gerar.py`, `servicos/flux/uv.lock`, `tests/test_flux_real.py`
- Modify: `LICENCAS.md`, `.gitignore` (se o serviço criar `.venv` dentro de `servicos/flux/`)

**Interfaces:**
- Consumes: o contrato do `GeradorFlux` (Task 8): `gerar.py ENTRADA.json SAIDA/`, `entrada = {largura, altura, passos, itens: [{id, prompt, semente}]}`, saída `SAIDA/<id>.png` + `SAIDA/resultado.json = {modelo, itens: [{id, ok, erro}], vram_pico_mb, ram_pico_mb, segundos}`, código 0 = lote rodou.
- Produces: o serviço com a configuração vencedora da Task 10 (dependências fixadas).

- [ ] **Step 1: Teste real que falha:** `test_flux_real_gera_16x9_e_9x16` (`@pytest.mark.real`): `GeradorFlux` com o comando padrão gera 1 imagem paisagem e 1 retrato; confere dimensões e `vram_pico_mb ≤ 7680` no `resultado.json`.
- [ ] **Step 2:** `uv run pytest -m real tests/test_flux_real.py -q` → FALHA (serviço ausente).
- [ ] **Step 3:** Implementar `gerar.py` com a configuração vencedora da sonda; erro de um item vira `ok: false` sem derrubar o lote; erro de carga do modelo sai com código ≠ 0 e mensagem curta no stderr. `LICENCAS.md` ganha a linha do FLUX.2 [klein] 4B (Apache 2.0, link do model card, data).
- [ ] **Step 4:** `uv run pytest -m real tests/test_flux_real.py -q` → PASSA; `uv run pytest -q` → PASSA; `autocanal diagnostico` → `flux` OK.
- [ ] **Step 5: Prova real com FLUX (nunca enviar):** repetir a prova da Task 9 com outro tema; conferir que os trechos sem candidato aprovado ficaram com origem `flux` (nenhum procedural), `metadados.json` com `sintetico: true`, a frase de IA na descrição, e o pico de VRAM do lote ≤ 7,5 GB. Registrar no ledger com o mosaico.
- [ ] **Step 6:** Commit `feat: serviço local do FLUX.2 klein 4B em lote`.
