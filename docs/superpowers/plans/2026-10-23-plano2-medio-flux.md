# Plano 2 — Vídeo médio + imagens FLUX

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `autocanal` produz vídeos `medio` (16:9, 1–5 min) com capítulos, thumbnail e créditos, e gera localmente com o FLUX.2 [klein] 4B as imagens dos trechos que o banco de imagens não cobre bem.

**Architecture:** O `medio` é mais uma receita em `FORMATOS`; as etapas existentes passam a ler do `Formato` o que hoje é constante do Short. A cadeia de visuais vira uma lista por canal em que cada provedor recebe todos os trechos pendentes (o FLUX trabalha em lote). O FLUX roda num projeto uv separado (`servicos/flux/`), chamado como subprocesso com contrato JSON; o autocanal nunca importa torch.

**Tech Stack:** Python 3.12 (uv), pydantic 2, pillow, requests, ffmpeg (`vendor/bin`), pytest; serviço FLUX: torch CUDA (cu128, GPU sm_120), diffusers, transformers.

**Spec:** `docs/superpowers/specs/2026-10-23-plano2-medio-flux-design.md` (complementa `docs/superpowers/specs/2026-10-22-autocanal-design.md`)

## Global Constraints

- Raiz `/home/losawa/autocanal`, branch `feat/plano2` (criada a partir de `main`). `export PATH="$HOME/.local/share/autocanal-tools/bin:$PATH"`; testes `uv run pytest -q` (exclui `@pytest.mark.real`).
- Testes padrão **sem rede, sem GPU, sem Claude real**; o FLUX real só em `@pytest.mark.real`.
- **Nunca** upload real, `autocanal auth`, timer systemd, `git push`, nem mexer em contas Google. Segredos nunca em logs, exceções ou commits.
- **Nunca** desligar verificação TLS. Pesos do Hugging Face só se baixam fora da rede da empresa (o firewall Fortinet intercepta `huggingface.co`).
- `medio`: 1920×1080, 60–300 s, 150–750 palavras (prompt mira 450–600), 3–7 seções, 2–8 segmentos por seção, no máximo 40 segmentos.
- Legendas por formato — short: tamanho 88, margem_v 560, 3 palavras, 1,6 s; medio: 60, 70, 6, 2,5. Margens laterais 60 px e contorno 6 px (valores atuais do estilo ASS).
- Capítulos: o primeiro em 0, pelo menos 3, cada um com pelo menos 10 s.
- Thumbnail: `thumbnail.jpg`, 1280×720, JPEG qualidade 90, menos de 2 000 000 bytes.
- Visuais: nomes `flux`, `pixabay_video`, `pixabay_foto`, `procedural`; ordem padrão `[pixabay_video, pixabay_foto, procedural]`; `procedural` sempre por último.
- FLUX: só o **FLUX.2 [klein] 4B** (Apache 2.0), nunca o 9B. Licença registrada como `gerado localmente (FLUX.2 klein 4B, Apache 2.0)`, origem `flux`, autor `autocanal`, url `flux:<hash8>`. Padrões: 4 passos, 1360×768 (retrato inverte), RAM livre mínima 6 GB, VRAM livre mínima 7 GB, timeout 180 s + 45 s por item.
- Créditos na descrição, exatos: `Imagens: Pixabay` e `Algumas imagens deste vídeo foram geradas por IA.`
- Upload: `selfDeclaredMadeForKids = canal.feito_para_criancas`; `containsSyntheticMedia` ligado quando há mídia do FLUX (a revisão liga, nunca desliga).
- Commits terminam com `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. Lote do FLUX que estoura o timeout (40 trechos) → `ErroProvedor`, os trechos caem para o próximo provedor e a etapa não trava. Teste: Task 7 `test_timeout_vira_erro`.
2. Visuais refeitos depois da revisão → sementes novas, nenhuma mídia repetida da rodada anterior. Teste: Task 7 `test_semente_muda_com_rodada` e Task 3 `test_resolver_mantem_excluir`.
3. Primeira seção curta (gancho de 6 s) → capítulos continuam começando em 00:00, sem capítulo < 10 s. Teste: Task 5 `test_capitulos_funde_primeira_curta`.
4. Texto de thumbnail com palavra longa ("INTERFERÊNCIA") → cabe no quadro, a fonte encolhe. Teste: Task 6 `test_thumbnail_palavra_longa_cabe`.
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
- Consumes: nada novo.

- [ ] **Step 1: Testes que falham**
  - `test_formato_medio`: `FORMATOS["medio"]` com `(1920, 1080)`, `(60, 300)`, `(150, 750)`, `"paisagem"` e a tupla de etapas acima, exatamente nessa ordem.
  - `test_formato_short_legendas_inalteradas`: short com `(88, 560, 3, 1.6)`.
  - `test_agrupar_fecha_por_largura`: palavras `velocidade num computador` (tempos curtos), `medir=lambda t: 40 * len(t)`, `largura_max=600` → textos `["velocidade num", "computador"]`.
  - `test_agrupar_palavra_sozinha_larga`: `medir=lambda t: 10_000` → um grupo por palavra, sem laço infinito.
  - `test_agrupar_sem_medidor_igual_antes`: sem `medir`, mesmo resultado do comportamento atual para uma frase de 7 palavras.
  - `test_legendas_ass_usa_formato` (em `test_etapas_iniciais.py`, com `transcrever` substituído como em `test_legendas_usa_transcricao`): contexto `medio` → `legendas.ass` contém `Style: Legenda,DejaVu Sans,60,` e termina o estilo com `,60,60,70,1`.
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
  - `Canal.visuais: list[Literal["flux", "pixabay_video", "pixabay_foto", "procedural"]] = ["pixabay_video", "pixabay_foto", "procedural"]`
  - `Canal.estilo_ia: str = ""`
  - `upload.montar_corpo(meta, *, publish_at, categoria_id, idioma, sintetico, feito_para_criancas: bool) -> dict`
  - `upload.vetar(youtube, video_id, sintetico: bool = False, feito_para_criancas: bool = False) -> None`
- Consumes: nada novo.

- [ ] **Step 1: Testes que falham**
  - `test_canal_campos_padrao(cfg)`: canal `teste` → `feito_para_criancas is False`, `visuais == ["pixabay_video", "pixabay_foto", "procedural"]`, `estilo_ia == ""`.
  - `test_canal_visuais_invalido`: `visuais: [pexels]` em `canais.yaml` → `pytest.raises(ValidationError)` ao carregar.
  - `test_montar_corpo_feito_para_criancas`: `feito_para_criancas=True` → `status.selfDeclaredMadeForKids is True` (e o teste atual passa a mandar `False` e conferir `False`).
  - `test_vetar_repassa_feito_para_criancas`: `vetar(yt, "abc", False, True)` → corpo do `update` com `selfDeclaredMadeForKids is True`.
  - `test_publicar_repassa_feito_para_criancas` (publicador): canal com `feito_para_criancas=True` → `montar_corpo` recebe `feito_para_criancas=True`.
- [ ] **Step 2:** `uv run pytest tests/test_config.py tests/test_upload.py tests/test_publicador.py tests/test_cli_youtube.py -q` → FALHA.
- [ ] **Step 3:** Implementar; o `vetar` da CLI passa o campo do canal do job.
- [ ] **Step 4:** `uv run pytest -q` → PASSA.
- [ ] **Step 5:** Commit `feat: canal com feito_para_criancas, ordem de visuais e estilo de IA`.

---

### Task 3: Cadeia de visuais por canal, com provedores em lote

**Files:**
- Modify: `autocanal/provedores/imagens.py`, `autocanal/etapas/visuais.py`
- Test: `tests/test_imagens.py`, `tests/test_etapas_iniciais.py`

**Interfaces:**
- Consumes: `Canal.visuais` (Task 2).
- Produces:
  - `MidiaObtida` ganha `prompt: str = ""` (último campo; entra no `como_dict`).
  - `Pedido` (dataclass frozen): `id: str, consulta: str, cena: str, destino_sem_ext: Path`.
  - `PixabayVideos(api_key, sessao=requests)` — `nome = "pixabay_video"`, só vídeos (com o filtro de orientação atual); `PixabayFotos(api_key, sessao=requests)` — `nome = "pixabay_foto"`, só fotos. Ambos gravam `origem = "pixabay"`. `PixabayImagens` deixa de existir.
  - `ProvedorLote(Protocol)`: `nome: str`; `buscar_lote(pedidos: list[Pedido], orientacao: Orientacao) -> dict[str, MidiaObtida]` (ids sem mídia ficam de fora; `ErroProvedor` = o lote inteiro falhou).
  - `criar_cadeia(cfg, canal, largura, altura, *, flux: ProvedorLote | None = None) -> list[ProvedorImagens | ProvedorLote]` — segue `canal.visuais`; `pixabay_*` só com `PIXABAY_API_KEY`; `flux` só se `flux` não for `None`; `FundoProcedural` sempre por último, uma vez só.
  - `resolver(cadeia, pedidos: list[Pedido], orientacao, excluir: set[str]) -> dict[str, MidiaObtida]` — cada provedor recebe todos os pedidos ainda sem mídia (provedor com `buscar_lote`: uma chamada; os outros: um `buscar(p.consulta, …)` por pedido, atualizando `excluir` a cada mídia). `ErroProvedor` → aviso no log e segue. Pedido sem mídia no fim → `ErroProvedor`.
  - `buscar_em_cadeia` é removida (`resolver` a substitui).
  - `visuais.executar` monta `Pedido(f"seg_{i:02d}", seg["visual"], seg.get("cena", ""), pasta / f"seg_{i:02d}")` e grava `fontes_midia.json` na ordem dos segmentos.

- [ ] **Step 1: Testes que falham**
  - Adaptar os testes do Pixabay: `PixabayVideos` (vídeo e chave fora do erro; orientação) e `PixabayFotos` (foto).
  - `test_pixabay_videos_nao_busca_fotos`: sem vídeo na orientação → `None` e nenhuma chamada a `https://pixabay.com/api/` (fotos).
  - `test_criar_cadeia_segue_ordem_do_canal`: `visuais = ["flux", "pixabay_video", "procedural", "pixabay_foto"]`, chave definida, `flux=<dublê>` → tipos `[dublê, PixabayVideos, PixabayFotos, FundoProcedural]`.
  - `test_criar_cadeia_sem_flux_pula`: `flux=None` → sem dublê na lista.
  - `test_resolver_lote_recebe_so_pendentes`: provedor por trecho resolve `seg_01`; o dublê de lote registra que recebeu `["seg_02", "seg_03"]` numa chamada só.
  - `test_resolver_erro_do_lote_segue`: lote levanta `ErroProvedor` → todos resolvidos pelo procedural; um aviso no log com o nome do provedor.
  - `test_resolver_mantem_excluir`: o segundo `buscar` recebe `excluir` com a URL da primeira mídia.
- [ ] **Step 2:** `uv run pytest tests/test_imagens.py tests/test_etapas_iniciais.py -q` → FALHA.
- [ ] **Step 3:** Implementar.
- [ ] **Step 4:** `uv run pytest -q` → PASSA.
- [ ] **Step 5:** Commit `refactor: cadeia de visuais por canal com provedores em lote`.

---

### Task 4: Roteiro do médio (seções) e `cena` em todos os formatos

**Files:**
- Create: `autocanal/prompts/roteiro_medio.md`
- Modify: `autocanal/prompts/roteiro_short.md`, `autocanal/etapas/roteiro.py`
- Test: `tests/test_etapas_iniciais.py`

**Interfaces:**
- Produces:
  - `ROTEIRO_SHORT`: segmento passa a exigir `cena` (string).
  - `ROTEIRO_MEDIO`: `{gancho, nota_gancho, secoes (3–7): {titulo, segmentos (2–8): {texto, visual, cena}}, fontes, resumo}`, `additionalProperties: false` em todos os níveis, como no Short.
  - `achatar(dados: dict) -> dict` — devolve `{gancho, nota_gancho, secoes: [títulos], segmentos: [{texto, visual, cena, secao: <índice>}], fontes, resumo}`; mais de 40 segmentos → `ErroEtapa`.
  - `executar(ctx)` escolhe prompt `roteiro_<formato>` e schema por formato (`{"short": ROTEIRO_SHORT, "medio": ROTEIRO_MEDIO}`); no médio, achata antes de contar palavras e gravar `roteiro.json`.
  - `roteiro_medio.md`: mesmas regras de trabalho 1–9 e os mesmos campos de formatação do `roteiro_short.md` (`{tema}`, `{perfil_voz}`, `{titulos_recentes}`, `{problemas_anteriores}`, `{palavras_min}`, `{palavras_max}`, `{dir_plugin}`); pede vídeo **horizontal de 3 a 4 minutos (450 a 600 palavras)**, seções com título curto (até 40 caracteres) que vira capítulo, e `cena` com 15 a 40 palavras em inglês, fotográfica e concreta, sem texto na imagem, marcas, logotipos ou pessoas reais.
  - `roteiro_short.md` ganha o campo `cena` com as mesmas regras.

- [ ] **Step 1: Testes que falham**
  - `test_achatar_secoes`: seções com 2, 3 e 2 segmentos → 7 segmentos com `secao == [0, 0, 1, 1, 1, 2, 2]` e `secoes` = os 3 títulos.
  - `test_achatar_mais_de_40_falha`: 7 seções × 6 segmentos → `ErroEtapa`.
  - `test_roteiro_medio_usa_prompt_e_schema`: com o Claude falso (como os testes de roteiro atuais), contexto `medio` → o prompt contém `3 a 4 minutos`, o schema é `ROTEIRO_MEDIO` e `roteiro.json` tem `secoes` e `segmentos[*].secao`.
  - `test_schema_short_exige_cena`: `ROTEIRO_SHORT["properties"]["segmentos"]["items"]["required"]` contém `"cena"`.
- [ ] **Step 2:** `uv run pytest tests/test_etapas_iniciais.py -q` → FALHA.
- [ ] **Step 3:** Implementar.
- [ ] **Step 4:** `uv run pytest -q` → PASSA.
- [ ] **Step 5:** Commit `feat: roteiro do médio com seções e cena para gerar imagem`.

---

### Task 5: Metadados — capítulos calculados, créditos, `cena_thumb` e mídia sintética

**Files:**
- Modify: `autocanal/etapas/metadados.py`, `autocanal/prompts/metadados.md`, `autocanal/etapas/revisao.py`
- Test: `tests/test_metadados.py`, `tests/test_revisao.py`

**Interfaces:**
- Consumes: `roteiro.json` com `secoes`/`segmentos[*].secao` (Task 4); `fontes_midia.json` com `origem` (Task 3).
- Produces:
  - `METADADOS` exige `cena_thumb` (string; o prompt pede 15–40 palavras em inglês, mesmas regras da `cena`, pensada como fundo de thumbnail com espaço livre à esquerda).
  - `capitulos_calculados(roteiro: dict, narracao: dict, minimo_s: float = 10.0) -> list[dict]` — cada seção começa no `inicio` do seu primeiro segmento; a duração da última vai até `narracao["duracao"]`. Seção mais curta que `minimo_s` é fundida à anterior; se for a primeira, funde-se à seguinte, e o capítulo resultante começa em 0 com o título da seguinte. Menos de 3 capítulos → `[]`.
  - `creditos(fontes: list[dict]) -> str` — `Imagens: Pixabay` se alguma origem é `pixabay`; `Algumas imagens deste vídeo foram geradas por IA.` se alguma é `flux`; juntos por `\n`; vazio se nenhuma.
  - `descricao_final(descricao, capitulos, urls, formato, creditos: str = "")` — o bloco de créditos entra antes de `Fontes:`.
  - `metadados.json` ganha `cena_thumb`; `capitulos` no médio vêm de `capitulos_calculados` (os do Claude são ignorados); `sintetico` = existe mídia `flux`.
  - `revisao.executar`: `meta["sintetico"] = bool(meta.get("sintetico")) or resultado["contains_synthetic_media"]`.

- [ ] **Step 1: Testes que falham**
  - `test_capitulos_basico`: 3 seções começando em 0, 40 e 95 s, duração 150 → `[{tempo: 0, …}, {tempo: 40, …}, {tempo: 95, …}]` com os títulos das seções.
  - `test_capitulos_funde_curta_do_meio`: seções em 0, 40, 45, 100 (duração 160) → a seção de 40–45 s (5 s) funde-se à anterior → capítulos em `0, 45, 100`, com os títulos das seções 0, 2 e 3.
  - `test_capitulos_funde_primeira_curta`: seções em 0, 6, 60, 120 (duração 180) → capítulos em `0, 60, 120`, o primeiro com o título da segunda seção.
  - `test_capitulos_menos_de_3_vazio`: 2 seções → `[]`.
  - `test_creditos`: só pixabay → `"Imagens: Pixabay"`; só flux → a frase de IA; ambos → as duas linhas; só procedural → `""`.
  - `test_descricao_creditos_antes_de_fontes`: o índice de `Imagens: Pixabay` é menor que o de `Fontes:`.
  - `test_metadados_medio_calcula_capitulos_e_sintetico`: Claude falso devolvendo `capitulos` próprios; `fontes_midia.json` com uma mídia `flux` → `metadados.json` com os capítulos calculados, `sintetico is True` e `cena_thumb` gravado.
  - `test_revisao_nao_desliga_sintetico`: `metadados.json` com `sintetico: true`, revisor falso com `contains_synthetic_media: false` → continua `true`.
- [ ] **Step 2:** `uv run pytest tests/test_metadados.py tests/test_revisao.py -q` → FALHA.
- [ ] **Step 3:** Implementar.
- [ ] **Step 4:** `uv run pytest -q` → PASSA.
- [ ] **Step 5:** Commit `feat: capítulos calculados, créditos de imagem e marcação de mídia sintética`.

---

### Task 6: Etapa thumbnail, envio em JPEG e alerta de miniatura recusada

**Files:**
- Create: `autocanal/etapas/thumbnail.py`, `tests/test_thumbnail.py`
- Modify: `autocanal/etapas/__init__.py`, `autocanal/publicador.py`, `autocanal/relatorio.py`, `autocanal/etapas/revisao.py`, `autocanal/prompts/revisao.md`
- Test: `tests/test_publicador.py`, `tests/test_relatorio.py`, `tests/test_revisao.py`

**Interfaces:**
- Consumes: `metadados.json` (`texto_thumb`, `cena_thumb`, `titulo`), `fontes_midia.json`, `FundoProcedural`, `Midia`.
- Produces:
  - `layout_texto(texto: str, fonte_arquivo: Path, largura_caixa: int, altura_caixa: int) -> tuple[int, list[str]]` — caixa alta, até 2 linhas equilibradas; tamanho de fonte começa em 160 e desce de 8 em 8 até toda linha caber na largura e o bloco na altura.
  - `compor_thumbnail(base: Path, texto: str, canal: Canal, saida: Path) -> Path` — base em 1280×720 (cobre e corta ao centro); caixa de texto no terço esquerdo (margens de 48 px, largura 45% e altura 60% do quadro); texto na cor `destaque` com contorno preto de 10 px; JPEG qualidade 90, baixando de 10 em 10 (mínimo 60) se passar de 2 000 000 bytes.
  - `imagem_base(ctx, meta: dict) -> tuple[Path, str]` (caminho, origem) — primeiro visual não procedural de `fontes_midia.json` (vídeo → quadro do meio via ffmpeg); senão `FundoProcedural` 1280×720 com a consulta = título. (A Task 7 põe o FLUX antes disso.)
  - `executar(ctx)` grava `thumbnail.jpg` e `thumbnail.json` `{"base_origem": ...}`; `REGISTRO["thumbnail"]`.
  - `publicador` envia `thumbnail.jpg` (não mais `.png`) e grava `thumbnail_envio.json` `{"ok": bool, "erro": str}` (erro cortado em 200 caracteres) depois de tentar.
  - `relatorio.alertas`: job com `thumbnail_envio.json` `ok: false` → ``Miniatura de `<id>` não foi enviada: verifique o telefone do canal em youtube.com/verify.``
  - `revisao.checagens`: formato com etapa `thumbnail` → problema `("thumbnail …", "thumbnail")` se `thumbnail.jpg` faltar, não tiver 1280×720 ou tiver 2 000 000 bytes ou mais. O prompt do revisor pede para abrir `thumbnail.jpg` com `Read` quando existir.

- [ ] **Step 1: Testes que falham**
  - `test_compor_thumbnail_dimensoes_e_peso`: base 1920×1080 qualquer → JPEG 1280×720 com menos de 2 000 000 bytes.
  - `test_thumbnail_palavra_longa_cabe`: `layout_texto("INTERFERÊNCIA NO WI-FI", fonte, 576, 432)` → toda linha medida ≤ 576 e o tamanho < 160.
  - `test_thumbnail_base_usa_visual`: `fontes_midia.json` com procedural e depois uma imagem pixabay → origem `pixabay`.
  - `test_thumbnail_base_video_extrai_quadro`: vídeo de 2 s gerado com o ffmpeg do projeto (`testsrc`) → quadro extraído existe.
  - `test_thumbnail_base_procedural_sem_visuais`: só procedural → origem `procedural`.
  - `test_publicar_envia_thumbnail_jpg` (adaptar a fixture que hoje cria `thumbnail.png`) e `test_thumbnail_recusada_grava_envio`: `definir_thumbnail` levanta `ErroUpload` → `thumbnail_envio.json` com `ok: false`, vídeo segue `agendado`.
  - `test_alerta_thumbnail_nao_enviada`.
  - `test_revisao_reprova_thumbnail_errada`: médio com `thumbnail.jpg` 640×360 → problema com etapa `thumbnail`.
- [ ] **Step 2:** `uv run pytest tests/test_thumbnail.py tests/test_publicador.py tests/test_relatorio.py tests/test_revisao.py -q` → FALHA.
- [ ] **Step 3:** Implementar.
- [ ] **Step 4:** `uv run pytest -q` → PASSA.
- [ ] **Step 5:** Commit `feat: etapa thumbnail, envio em JPEG e alerta de miniatura recusada`.

---

### Task 7: Cliente do FLUX (subprocesso em lote) com portões de memória

**Files:**
- Create: `autocanal/provedores/flux.py`, `tests/test_flux.py`, `tests/fakes/flux_falso.py`
- Modify: `autocanal/config.py`, `config/autocanal.yaml`, `autocanal/legendas.py`, `autocanal/etapas/visuais.py`, `autocanal/etapas/thumbnail.py`, `autocanal/cli.py` (diagnóstico)
- Test: `tests/test_etapas_iniciais.py`, `tests/test_thumbnail.py`, `tests/test_cli_operacao.py`

**Interfaces:**
- Consumes: `Pedido`, `MidiaObtida`, `ProvedorLote`, `criar_cadeia(..., flux=)` (Task 3); `Canal.visuais`, `Canal.estilo_ia` (Task 2); `imagem_base` (Task 6).
- Produces:
  - `ConfigFlux` (pydantic) em `Config.flux`: `projeto: Path = Path("servicos/flux")` (relativo à raiz), `uv: Path` (em `autocanal.yaml`: `/home/losawa/.local/share/autocanal-tools/bin/uv`), `passos: int = 4`, `largura: int = 1360`, `altura: int = 768`, `ram_minima_gb: float = 6.0`, `vram_minima_gb: float = 7.0`, `timeout_base_s: int = 180`, `timeout_por_item_s: int = 45`, `sufixo: str = "photorealistic photo, natural light, no text, no watermark"`.
  - `flux.instalado(cfg) -> bool` — existem `<projeto>/pyproject.toml` e `<projeto>/gerar.py`.
  - `flux.memoria_livre() -> tuple[float, float]` — (RAM livre em GB, de `MemAvailable` em `/proc/meminfo`; VRAM livre em GB, de `nvidia-smi --query-gpu=memory.free`; 0 se falhar).
  - `flux.prompt_final(cena: str, estilo_ia: str, sufixo: str) -> str` — junta as partes não vazias com `", "`.
  - `flux.semente(job_id: str, item_id: str, rodada: int) -> int` — sha256 → inteiro < 2³¹.
  - `GeradorFlux(cfg, *, job_id: str, rodada: int, estilo_ia: str = "", comando_base: list[str] | None = None, executar=subprocess.run, memoria=memoria_livre)` — `nome = "flux"`; `buscar_lote(pedidos, orientacao)`: confere memória (abaixo do mínimo → `ErroProvedor("memória insuficiente …")` sem rodar nada); grava `entrada.json` (`largura`/`altura` trocadas no retrato, `passos`, `itens` com `id`, `prompt` final e `semente`) numa pasta `flux_lote/` ao lado dos destinos; roda `comando_base + [entrada, saida]` (padrão: `[uv, "run", "--project", <projeto>, "python", <projeto>/gerar.py]`) com `timeout = timeout_base_s + timeout_por_item_s × itens`; código ≠ 0 ou timeout → `ErroProvedor`; lê `resultado.json`, move `<id>.png` de cada item `ok` para `destino_sem_ext + ".png"` e devolve as `MidiaObtida` (`origem "flux"`, licença e autor das Global Constraints, `url "flux:<hash8 de prompt+semente>"`, `prompt` preenchido).
  - `legendas.liberar_modelos() -> None` — esvazia o cache de modelos do Whisper e chama `gc.collect()`.
  - `visuais.executar`: se `"flux" in canal.visuais` e `instalado(cfg)`, antes de resolver chama `legendas.liberar_modelos()` e para o Kokoro (`servicos.parar("kokoro", raiz)` quando o compose existe; falha só vira aviso), e passa `GeradorFlux(cfg, job_id=..., rodada=ctx.job.tentativas_revisao, estilo_ia=canal.estilo_ia)` para `criar_cadeia`; se o FLUX está na ordem mas não instalado, registra um aviso e segue sem ele.
  - `thumbnail.imagem_base`: com o FLUX na ordem e instalado, tenta antes um lote de 1 item com `cena_thumb` em paisagem.
  - Diagnóstico: linha `flux` → `[OK   ] flux (servicos/flux)` ou `[FALHA] flux: não instalado (opcional)`.
  - `tests/fakes/flux_falso.py`: lê `entrada.json`, grava um PNG por item e o `resultado.json`; prompt contendo `FALHAR` → item `ok: false`; contendo `TRAVAR` → dorme 5 s; contendo `QUEBRAR` → sai com código 3.

- [ ] **Step 1: Testes que falham** (todos com `comando_base=[sys.executable, "tests/fakes/flux_falso.py"]` e `memoria=lambda: (16.0, 8.0)` salvo indicação)
  - `test_lote_gera_midias`: 2 pedidos → 2 `MidiaObtida` com PNG no destino, `origem == "flux"` e `prompt` contendo o sufixo.
  - `test_item_falhou_fica_de_fora`: um prompt com `FALHAR` → só o outro id no resultado.
  - `test_timeout_vira_erro`: `timeout_base_s=1`, `timeout_por_item_s=0`, prompt `TRAVAR` → `ErroProvedor`.
  - `test_saida_nao_zero_vira_erro`: prompt `QUEBRAR` → `ErroProvedor`.
  - `test_sem_memoria_pula_sem_rodar`: `memoria=lambda: (2.0, 8.0)` → `ErroProvedor` e `executar` nunca chamado.
  - `test_retrato_inverte_dimensoes`: `entrada.json` com `largura == 768` e `altura == 1360`.
  - `test_semente_muda_com_rodada`: `semente("j", "seg_01", 0) != semente("j", "seg_01", 1)` e é determinística.
  - `test_visuais_libera_memoria_antes_do_flux`: canal com `flux` na ordem e projeto falso instalado → `liberar_modelos` e `servicos.parar` chamados antes do lote.
  - `test_visuais_flux_nao_instalado_segue`: ordem com `flux`, projeto ausente → visuais resolvidos sem FLUX e aviso no log.
  - `test_thumbnail_usa_flux_primeiro`.
  - `test_diagnostico_mostra_flux`.
- [ ] **Step 2:** `uv run pytest tests/test_flux.py tests/test_etapas_iniciais.py tests/test_thumbnail.py tests/test_cli_operacao.py -q` → FALHA.
- [ ] **Step 3:** Implementar.
- [ ] **Step 4:** `uv run pytest -q` → PASSA.
- [ ] **Step 5:** Commit `feat: cliente do FLUX em lote com portões de memória`.

---

### Task 8: Configuração, documentação e prova real do médio (sem FLUX)

**Files:**
- Modify: `config/canais.yaml`, `config/canais.yaml.exemplo`, `config/autocanal.yaml`, `README.md`, `CLAUDE.md`

**Interfaces:**
- Consumes: tudo das Tasks 1–7.

- [ ] **Step 1:** `config/canais.yaml`: `conteudo` com `visuais: [flux, pixabay_video, pixabay_foto, procedural]` e a grade acrescida de `{formato: medio, dias: [ter, qui], hora: "18:00"}`; `infantil` com `feito_para_criancas: true` (grade continua vazia). O exemplo documenta os três campos novos.
- [ ] **Step 2:** README: formato `medio` (`autocanal novo <canal> medio "<tema>"`), campos novos do canal, e no checklist de Laura: restrição de API da chave do TTS, youtube.com/verify, país do canal e baixar os pesos do FLUX fora da rede da empresa. `CLAUDE.md`: o formato médio e a etapa thumbnail.
- [ ] **Step 3:** `uv run pytest -q` → PASSA; `autocanal diagnostico` → linha `flux` em FALHA (ainda não instalado), resto como antes.
- [ ] **Step 4: Prova real (custa uso do Claude; nunca enviar):** `autocanal novo conteudo medio "<tema do nicho>" --quando "<data ≥ 7 dias à frente> 18:00"` e `autocanal produzir <id>`. Conferir: `video.mp4` 1920×1080 e 60–300 s (ffprobe); legendas dentro do quadro (quadros extraídos); `thumbnail.jpg` 1280×720 < 2 MB; descrição com capítulos e `Imagens: Pixabay`; estado final `aprovado` ou `quarentena` com motivos legíveis. Registrar o resultado no ledger. Defeito de código encontrado → corrigir com teste antes de seguir.
- [ ] **Step 5:** Commit `chore: médio na grade do conteudo, canal infantil feito para crianças, docs`.

---

### Task 9: Passo 0 — sonda do FLUX (portão)

Precisa de rede fora da empresa. Se `echo | openssl s_client -connect huggingface.co:443 -servername huggingface.co 2>/dev/null | grep issuer` mostrar Fortinet, registre `Task 9: bloqueada (rede)` no ledger e pare aqui: as Tasks 9 e 10 ficam para quando Laura estiver em outra rede (as Tasks 1–8 já entregam o médio funcionando com Pixabay).

**Files:**
- Create (descartável, fora do git): `dados/sonda-flux/` (projeto uv), `dados/amostras/flux/`

- [ ] **Step 1:** Listar os repositórios oficiais (`https://huggingface.co/api/models?author=black-forest-labs&search=klein`) e confirmar no model card do 4B a licença Apache 2.0; anotar o id do repositório e se há pesos fp8 em formato diffusers.
- [ ] **Step 2:** `uv sync` do projeto da sonda (torch cu128, diffusers com `Flux2KleinPipeline`, transformers, accelerate, bitsandbytes, psutil).
- [ ] **Step 3:** Script da sonda em duas fases (codifica os prompts → libera o encoder → carrega o transformer e gera). Tentar nesta ordem até uma passar nos critérios: (a) encoder em 4 bits na GPU + transformer em fp8 (pesos fp8 ou `enable_layerwise_casting` com armazenamento fp8); (b) a mesma com `enable_model_cpu_offload`. Medir pico de VRAM (`torch.cuda.max_memory_allocated` e nvidia-smi), pico de RAM do processo (psutil), carga e segundos por imagem em 1360×768 e 768×1360 (e 1920×1088 se couber).
- [ ] **Step 4:** Critérios (do spec): VRAM ≤ 7,5 GB, RAM do processo ≤ 6 GB, ≤ 30 s por imagem. Salvar 4 amostras em `dados/amostras/flux/` e uma tabela em `dados/sonda-flux/resultado.md`. Passou → ledger `Task 9: go (<configuração vencedora>)`; não passou → ledger `Task 9: no-go (<motivo>)`, tirar `flux` da ordem do `conteudo` (commit), pular a Task 10 e avisar Laura no fim.

---

### Task 10: Serviço FLUX real e prova com FLUX

**Files:**
- Create: `servicos/flux/pyproject.toml`, `servicos/flux/gerar.py`, `tests/test_flux_real.py`
- Modify: `LICENCAS.md`, `.gitignore` (se o projeto do serviço criar `.venv` dentro de `servicos/flux/`)

**Interfaces:**
- Consumes: o contrato do `GeradorFlux` (Task 7): `gerar.py ENTRADA.json SAIDA/`, `entrada = {largura, altura, passos, itens: [{id, prompt, semente}]}`, saída `SAIDA/<id>.png` + `SAIDA/resultado.json = {modelo, itens: [{id, ok, erro}], vram_pico_mb, ram_pico_mb, segundos}`, código 0 = lote rodou.
- Produces: o serviço com a configuração vencedora da Task 9 (dependências fixadas no `pyproject.toml`, `uv.lock` versionado).

- [ ] **Step 1: Teste real que falha:** `test_flux_real_gera_16x9_e_9x16` (`@pytest.mark.real`): `GeradorFlux` com o comando padrão gera 1 imagem paisagem e 1 retrato; confere dimensões e que `resultado.json` tem `vram_pico_mb ≤ 7680`.
- [ ] **Step 2:** `uv run pytest -m real tests/test_flux_real.py -q` → FALHA (serviço ausente).
- [ ] **Step 3:** Implementar `gerar.py` portando a configuração vencedora da sonda; erro de um item vira `ok: false` sem derrubar o lote; erro de carga do modelo sai com código ≠ 0 e mensagem curta no stderr. `LICENCAS.md` ganha a linha do FLUX.2 [klein] 4B (Apache 2.0, link do model card, data).
- [ ] **Step 4:** `uv run pytest -m real tests/test_flux_real.py -q` → PASSA; `uv run pytest -q` → PASSA; `autocanal diagnostico` → `flux` OK.
- [ ] **Step 5: Prova real com FLUX (nunca enviar):** repetir a prova da Task 8 com outro tema; conferir que `fontes_midia.json` tem origem `flux`, que `metadados.json` tem `sintetico: true`, que a descrição tem a frase de IA, e que o pico de VRAM do lote ficou ≤ 7,5 GB. Registrar no ledger.
- [ ] **Step 6:** Commit `feat: serviço local do FLUX.2 klein 4B em lote`.
