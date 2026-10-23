# Plano 1 — Núcleo, Short ponta a ponta, YouTube e agendador

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Um `autocanal` que, sozinho, planeja, produz (roteiro → narração → legendas → visuais → render → metadados → revisão) e envia Shorts agendados ao YouTube, com OAuth persistente e agendador por timer.

**Architecture:** Pacote Python local; SQLite guarda fila e checkpoints de etapas; cada etapa grava artefatos em `dados/jobs/<id>/`. Etapas criativas e a revisão chamam `claude -p` (plugin youtube-agent) com `--json-schema`. ffmpeg estático faz a mídia; Kokoro (container) e Google TTS fazem a voz; Pexels/Pixabay/procedural fazem os visuais.

**Tech Stack:** Python 3.12 (uv), pydantic 2, typer, jsonschema, requests, pillow, faster-whisper, google-api-python-client, google-auth-oauthlib, pytest; ffmpeg/ffprobe em `vendor/bin`; Docker para Kokoro-FastAPI.

**Spec:** `docs/superpowers/specs/2026-10-22-autocanal-design.md`

**Planos seguintes (fora deste):** Plano 2 — `slides` e `medio` (+ thumbnail). Plano 3 — `musica` e `compilacao` (ACE-Step, trava de GPU). Plano 4 — estratégia (nicho, branding, grade), Analytics e `ajustar`.

## Global Constraints

- Raiz do projeto: `/home/losawa/autocanal`. uv: `~/.local/share/autocanal-tools/bin/uv` (exporte `PATH="$HOME/.local/share/autocanal-tools/bin:$PATH"`). Testes: `uv run pytest -q`.
- Python `>=3.12,<3.13`. Pacote `autocanal/`, testes em `tests/`. Nomes de módulos, funções e mensagens em português.
- ffmpeg/ffprobe: `vendor/bin/ffmpeg` e `vendor/bin/ffprobe` (já instalados, com `ass`, `loudnorm`, `zoompan`). Sempre via `Config.ffmpeg`/`Config.ffprobe`, nunca do PATH.
- Datas no banco em ISO-8601 UTC (`2026-10-25T15:00:00+00:00`); fuso de negócio `America/Sao_Paulo` (`zoneinfo`).
- Testes padrão **sem rede, sem GPU, sem Claude real**. Testes que usam algo real levam `@pytest.mark.real` e ficam fora de `pytest -q` (`addopts = "-m 'not real'"`).
- **Nunca** fazer upload real, alterar contas Google, nem habilitar o timer systemd durante a implementação.
- Segredos só em `config/segredos.env` e `dados/tokens/` (chmod 600); nunca em logs, exceções ou commits.
- Limites do formato `short`: 1080×1920, 30–60 s, 75–150 palavras de narração. Loudness alvo −14 LUFS (tolerância ±1,5). Silêncio proibido: > 2 s abaixo de −40 dB. Similaridade máxima de roteiro: 0,35 (Jaccard de 5-gramas) contra os últimos 30 do canal. Nota mínima de título: 70 (`title.py`). Máximo de rodadas de revisão: 2. Folga da fila: 3 dias.
- YouTube: `privacyStatus=private` + `publishAt` (UTC, formato `YYYY-MM-DDTHH:MM:SSZ`), `selfDeclaredMadeForKids=false`, `containsSyntheticMedia` da revisão. Título ≤ 100 caracteres, descrição ≤ 5000 bytes, tags ≤ 15 e soma ≤ 500 caracteres, sem `<` nem `>` em título/descrição.
- Commits terminam com a linha `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. Notebook desligado por dias → jobs `aprovado` com `publish_at` no passado → reagendar para o próximo slot da grade ≥ agora + 1 h antes do upload (a API rejeita `publishAt` passado). Teste: Task 11 `test_publicar_reagenda_publish_at_passado`.
2. Refresh token revogado (`invalid_grant`) → canal marcado `auth_ok=0`, seus jobs continuam `aprovado`, outros canais seguem. Teste: Task 11 `test_publicar_auth_expirada_isola_canal`.
3. Claude atinge limite de uso do plano → etapa adiada sem contar tentativa nem falhar o job. Teste: Task 8 `test_produzir_limite_uso_nao_conta_tentativa`.
4. Timer dispara enquanto um render longo roda → o segundo `tick` sai na hora. Teste: Task 12 `test_tick_concorrente_sai_pela_trava`.
5. Texto com `<`, `>`, `{`, `}`, `\`, aspas e emoji → título/descrição sanitizados para a API; legendas ASS escapadas. Testes: Task 7 `test_escapar_ass`, Task 10 `test_sanitizar_texto`.

---

### Task 1: Esqueleto, configuração e diagnóstico

**Files:**
- Create: `pyproject.toml`, `autocanal/__init__.py`, `autocanal/config.py`, `autocanal/cli.py`, `config/autocanal.yaml`, `config/canais.yaml.exemplo`, `config/segredos.env.exemplo`, `config/vozes/exemplo.md`, `LICENCAS.md`, `tests/conftest.py`, `tests/test_config.py`

**Interfaces:**
- Produces:
  - `autocanal.config.Paleta(fundo: str, texto: str, destaque: str)` (hex `#RRGGBB`)
  - `ConfigTTS(provedor: Literal["google","kokoro","falso"], voz: str, reserva: Literal["kokoro","falso"] | None = "kokoro", voz_reserva: str = "pm_alex")`
  - `Slot(formato: str, dias: list[Literal["seg","ter","qua","qui","sex","sab","dom"]], hora: str)` — `hora` validado `HH:MM`
  - `Canal(slug: str, nome: str, nicho: str = "", idioma: str = "pt-BR", categoria_id: str, tts: ConfigTTS, paleta: Paleta, fonte_arquivo: Path, fonte_nome: str, perfil_voz: Path, grade: list[Slot])`
  - `ConfigClaude(bin: str = "claude", modelos: dict[str, str | None], max_turns: dict[str, int])`
  - `ConfigWhisper(modelo: str = "small", dispositivo: str = "cpu", compute_type: str = "int8")`
  - `ConfigRevisao(nota_titulo_minima: int = 70, similaridade_maxima: float = 0.35, max_rodadas: int = 2, supervisao_dias: int = 14)`
  - `Config(raiz: Path, dados: Path, fuso: str, folga_dias: int, ffmpeg: Path, ffprobe: Path, plugin_dir: Path, client_secret: Path, kokoro_url: str, claude: ConfigClaude, whisper: ConfigWhisper, revisao: ConfigRevisao, chaves: dict[str, str], canais: dict[str, Canal])`
  - `carregar(raiz: Path) -> Config` — lê `config/autocanal.yaml`, `config/canais.yaml`, `config/segredos.env` (opcional, formato `CHAVE=valor`); caminhos relativos resolvidos contra `raiz`, `~` expandido; `dados = raiz/"dados"`.
  - `autocanal.cli.app` (typer) com `--raiz` global (padrão: env `AUTOCANAL_RAIZ` ou `~/autocanal`) e o comando `diagnostico`.

- [ ] **Step 1:** Criar `pyproject.toml` (nome `autocanal`, `requires-python = ">=3.12,<3.13"`, dependências do Tech Stack + `pyyaml`, script `autocanal = "autocanal.cli:app"`, `[tool.pytest.ini_options] markers = ["real: usa rede/GPU/Claude"]`, `addopts = "-m 'not real'"`). Rodar `uv sync --python 3.12`. Esperado: `.venv/` criado sem erro.
- [ ] **Step 2: Testes que falham** em `tests/test_config.py`:
  - `test_carregar_resolve_caminhos(tmp_path)`: monta raiz com `config/autocanal.yaml` (`ffmpeg: vendor/bin/ffmpeg`) e `config/canais.yaml` com canal `teste`; `cfg = carregar(tmp_path)`; `assert cfg.ffmpeg == tmp_path/"vendor/bin/ffmpeg"`, `cfg.dados == tmp_path/"dados"`, `cfg.canais["teste"].slug == "teste"`.
  - `test_segredos_opcionais(tmp_path)`: sem `segredos.env` → `cfg.chaves == {}`; com `PEXELS_API_KEY=abc` → `cfg.chaves["PEXELS_API_KEY"] == "abc"`.
  - `test_slot_hora_invalida`: `Slot(formato="short", dias=["seg"], hora="25:00")` levanta `ValidationError`.
  - `test_canal_sem_nicho_padrao_vazio`: canal sem `nicho` → `""`.
  - `tests/conftest.py` fornece fixture `raiz_teste(tmp_path)` que cria uma raiz válida mínima (canal `teste`, `tts.provedor: falso`, grade `[{formato: short, dias: [seg,qua,sex], hora: "12:00"}]`, `ffmpeg`/`ffprobe` apontando para os binários reais de `/home/losawa/autocanal/vendor/bin`) e devolve o `Path`; fixture `cfg(raiz_teste)` devolve `carregar(raiz_teste)`.
- [ ] **Step 3:** Rodar `uv run pytest tests/test_config.py -q` → FALHA (módulo inexistente).
- [ ] **Step 4:** Implementar `autocanal/config.py` (pydantic v2). Criar `config/autocanal.yaml` real com os padrões: `fuso: America/Sao_Paulo`, `folga_dias: 3`, `ffmpeg: vendor/bin/ffmpeg`, `ffprobe: vendor/bin/ffprobe`, `plugin_dir: ~/.claude/plugins/cache/youtube-agent-skill/youtube-agent/1.0.0`, `client_secret: config/client_secret.json`, `kokoro_url: http://127.0.0.1:8880`, `claude.modelos: {roteiro: null, metadados: sonnet, revisao: null, temas: sonnet}`, `claude.max_turns: {roteiro: 30, metadados: 20, revisao: 30, temas: 15}`. `canais.yaml.exemplo` com os canais `conteudo` (categoria `"27"`, paleta `#0F172A/#F8FAFC/#FACC15`, `tts: {provedor: google, voz: pt-BR-Chirp3-HD-Charon, reserva: kokoro, voz_reserva: pm_alex}`, grade de Shorts: `{dias: [seg,qua,sex,dom], hora: "12:00"}` e `{dias: [ter,qui,sab], hora: "19:00"}`) e `musica` (categoria `"10"`, grade vazia até o Plano 3); `fonte_arquivo: /usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf`, `fonte_nome: DejaVu Sans`. `segredos.env.exemplo` com `PEXELS_API_KEY=`, `PIXABAY_API_KEY=`, `GOOGLE_TTS_API_KEY=`, `YOUTUBE_API_KEY=`. `LICENCAS.md`: tabela ferramenta | licença | uso comercial | link | verificado em — preenchida com as linhas do spec §2 (ACE-Step, Google TTS, Kokoro, Pexels, Pixabay, ffmpeg GPL build, faster-whisper MIT) com data 2026-10-22.
- [ ] **Step 5:** Implementar `cli.py` com `diagnostico`: imprime OK/FALHA para ffmpeg e ffprobe executáveis, `claude --version`, `nvidia-smi`, `docker info`, existência de `client_secret.json`, presença de cada chave de `segredos.env` (só "definida"/"ausente", nunca o valor), tokens em `dados/tokens/`. Copiar `/home/losawa/Downloads/client_secret_1062179375800-eklraj7te0vgql32gnv90ikhhfqc6c6b.apps.googleusercontent.com.json` para `config/client_secret.json` com `chmod 600` (já ignorado pelo git).
- [ ] **Step 6:** `uv run pytest -q` → PASSA. `uv run autocanal diagnostico` → mostra ffmpeg OK.
- [ ] **Step 7:** Commit `feat: esqueleto, configuração e diagnóstico`.

---

### Task 2: Banco e fila

**Files:**
- Create: `autocanal/db.py`, `autocanal/fila.py`, `tests/test_fila.py`

**Interfaces:**
- Consumes: nada.
- Produces:
  - `db.conectar(caminho: Path) -> sqlite3.Connection` — cria diretório e schema (tabelas do spec §4: `jobs`, `etapas`, `revisoes`, `canais_estado`, `cota`, `metricas`, `temas`, mais `chamadas_claude(id, job_id, etapa, custo_usd, turnos, duracao_s, criado_em)`), `row_factory = sqlite3.Row`, `PRAGMA journal_mode=WAL`, `foreign_keys=ON`. Idempotente.
  - `fila.Estado(StrEnum)`: `planejado, produzindo, em_revisao, aprovado, agendado, publicado, quarentena, falhou, vetado, descartado`.
  - `fila.TRANSICOES: dict[Estado, frozenset[Estado]]` — exatamente: planejado→{produzindo, descartado}; produzindo→{em_revisao, falhou, descartado}; em_revisao→{aprovado, produzindo, quarentena, falhou}; aprovado→{agendado, falhou, descartado}; agendado→{publicado, vetado}; quarentena→{aprovado, produzindo, descartado}; falhou→{produzindo, descartado}; vetado→{descartado}; publicado→{}; descartado→{}.
  - `fila.Job` (dataclass frozen): `id: str, canal: str, formato: str, tema: str, briefing: dict, estado: Estado, publish_at: datetime (UTC, aware), tentativas_revisao: int, youtube_video_id: str | None, erro: str | None, criado_em: datetime`.
  - `fila.TransicaoInvalida(Exception)`
  - `criar_job(con, canal: str, formato: str, tema: str, publish_at: datetime, briefing: dict | None = None) -> Job` — id `AAAAMMDD-<canal>-<formato>-<4 hex>` (data de `publish_at` no fuso UTC).
  - `obter(con, job_id: str) -> Job` (`KeyError` se não existir); `listar(con, estados: set[Estado] | None = None, canal: str | None = None) -> list[Job]` ordenado por `publish_at`.
  - `mudar_estado(con, job_id: str, novo: Estado, erro: str | None = None) -> Job`; `atualizar(con, job_id: str, **campos) -> Job` (aceita `publish_at`, `youtube_video_id`, `tentativas_revisao`, `briefing`, `erro`).
  - `proximo_para_produzir(con) -> Job | None` — estado `planejado` ou `produzindo`, menor `publish_at`.
  - `estado_etapa(con, job_id, nome) -> str | None` (`"rodando" | "ok" | "erro"`), `marcar_etapa(con, job_id, nome, estado: str, erro: str | None = None) -> None` (estado `erro` incrementa `tentativas`), `tentativas_etapa(con, job_id, nome) -> int`, `resetar_etapas(con, job_id, nomes: list[str]) -> None`.

- [ ] **Step 1: Testes que falham** em `tests/test_fila.py`:
  - `test_criar_e_obter`: cria job `("teste","short","céu azul", datetime(2026,10,25,15,tzinfo=UTC))`; `obter` devolve `estado == Estado.PLANEJADO`, `publish_at` igual e aware, `id` casa `^20261025-teste-short-[0-9a-f]{4}$`.
  - `test_transicao_valida_e_invalida`: planejado→produzindo ok; produzindo→publicado levanta `TransicaoInvalida`.
  - `test_todas_as_transicoes_declaradas`: `set(TRANSICOES) == set(Estado)`.
  - `test_proximo_para_produzir_ordem`: dois jobs planejados com `publish_at` diferentes → devolve o mais cedo; um `aprovado` mais cedo é ignorado.
  - `test_etapas_checkpoint`: `marcar_etapa(...,"roteiro","erro","x")` duas vezes → `tentativas_etapa == 2`; depois `"ok"` → `estado_etapa == "ok"`; `resetar_etapas(..., ["roteiro"])` → `estado_etapa is None` e tentativas 0.
  - `test_conectar_idempotente(tmp_path)`: `conectar` duas vezes no mesmo arquivo não falha.
- [ ] **Step 2:** `uv run pytest tests/test_fila.py -q` → FALHA.
- [ ] **Step 3:** Implementar `db.py` e `fila.py`.
- [ ] **Step 4:** `uv run pytest -q` → PASSA.
- [ ] **Step 5:** Commit `feat: banco SQLite e fila com máquina de estados`.

---

### Task 3: Wrapper do Claude headless

**Files:**
- Create: `autocanal/claude.py`, `tests/fakes/claude_falso.py`, `tests/test_claude.py`

**Interfaces:**
- Consumes: `db.conectar` (tabela `chamadas_claude`).
- Produces:
  - `ResultadoClaude` (dataclass frozen): `dados: dict, custo_usd: float, turnos: int, duracao_s: float`
  - `ErroClaude(Exception)`, `LimiteUsoClaude(ErroClaude)`
  - `montar_argv(prompt: str, schema: dict, ferramentas: list[str], *, modelo: str | None, max_turns: int, claude_bin: str) -> list[str]` — exatamente `[claude_bin, "-p", prompt, "--output-format", "json", "--json-schema", json.dumps(schema), "--no-session-persistence", "--max-turns", str(max_turns)] + (["--model", modelo] if modelo else []) + ["--allowedTools", *ferramentas]` (lista variádica por último).
  - `chamar(prompt: str, schema: dict, ferramentas: list[str], cwd: Path, *, modelo: str | None = None, max_turns: int = 20, claude_bin: str = "claude", tentativas: int = 3, timeout_s: int = 900, executar=subprocess.run) -> ResultadoClaude` — lê o JSON do stdout; usa `structured_output` (fallback: `json.loads(result)`); valida com `jsonschema.validate`; falha de parse/validação/`is_error` → nova tentativa até `tentativas`, depois `ErroClaude` com o motivo. Se `is_error` e o texto de `result` casar `(?i)usage limit|rate limit|limit reached|hour limit|weekly limit` → `LimiteUsoClaude` imediatamente, sem novas tentativas.
  - `registrar(con, job_id: str | None, etapa: str, r: ResultadoClaude) -> None`
- `tests/fakes/claude_falso.py`: executável Python que grava `sys.argv` em `$CLAUDE_FALSO_LOG` (JSON) e imprime o conteúdo de `$CLAUDE_FALSO_SAIDA` (arquivo); se `$CLAUDE_FALSO_SAIDAS` (diretório) existir, consome `01.json`, `02.json`… em ordem a cada chamada. Fixture `claude_falso(tmp_path, monkeypatch)` em `conftest.py` que devolve um objeto com `.responder(*dicts)` (escreve as saídas) e `.chamadas()` (lê os argv) e o `claude_bin` a usar.

- [ ] **Step 1: Testes que falham** em `tests/test_claude.py`:
  - `test_montar_argv_ordem`: `--allowedTools` é o penúltimo bloco e as ferramentas vêm por último; `"--model"` ausente quando `modelo=None`.
  - `test_chamar_structured_output`: falso responde `{"type":"result","subtype":"success","is_error":false,"num_turns":3,"total_cost_usd":0.1,"duration_ms":1200,"structured_output":{"x":1}}` → `dados == {"x":1}`, `turnos == 3`, `duracao_s == 1.2`.
  - `test_chamar_invalido_tenta_de_novo`: 1ª saída viola o schema (`{"y":1}` com `required:["x"]`), 2ª válida → sucesso e 2 chamadas registradas.
  - `test_chamar_esgota_tentativas`: 3 saídas inválidas → `ErroClaude`.
  - `test_limite_de_uso`: `is_error:true, result:"Claude usage limit reached"` → `LimiteUsoClaude` após 1 chamada.
  - `test_registrar(tmp_path)`: grava linha em `chamadas_claude`.
- [ ] **Step 2:** `uv run pytest tests/test_claude.py -q` → FALHA.
- [ ] **Step 3:** Implementar.
- [ ] **Step 4:** `uv run pytest -q` → PASSA.
- [ ] **Step 5:** Commit `feat: wrapper do Claude headless com schema e retentativas`.

---

### Task 4: Utilitários de mídia

**Files:**
- Create: `autocanal/midia.py`, `tests/test_midia.py`

**Interfaces:**
- Produces:
  - `ErroMidia(Exception)`; `InfoMidia` (dataclass frozen): `duracao: float, largura: int | None, altura: int | None, tem_audio: bool, tem_video: bool`
  - `class Midia(ffmpeg: Path, ffprobe: Path)`:
    - `rodar(args: list[str], *, cwd: Path | None = None, timeout_s: int = 1800) -> str` — executa `ffmpeg -hide_banner -y *args`, devolve stderr; erro → `ErroMidia` com os últimos 2000 caracteres do stderr.
    - `info(arquivo: Path) -> InfoMidia` (ffprobe JSON)
    - `loudness(arquivo: Path) -> float` — LUFS integrado via `loudnorm=print_format=json`
    - `silencios(arquivo: Path, limiar_db: float = -40.0, minimo_s: float = 2.0) -> list[tuple[float, float]]` — via `silencedetect`
    - `extrair_frames(video: Path, n: int, destino: Path) -> list[Path]` — `n` quadros uniformes, `quadro_01.jpg`…
    - `concatenar_wav(wavs: list[Path], destino: Path, pausa_s: float = 0.25) -> Path` — 24 kHz mono, pausa de silêncio entre cada par.
- Fixture `midia(cfg)` em `conftest.py`.

- [ ] **Step 1: Testes que falham** (geram mídia com `lavfi`: `sine=f=440:d=3`, `color=c=red:s=320x240:d=2`):
  - `test_info_audio_e_video`: vídeo 320×240 com áudio → `largura == 320`, `tem_audio`, `abs(duracao-2) < 0.1`.
  - `test_loudness_seno`: seno de 3 s a −20 dBFS → `-25 < loudness < -15`.
  - `test_silencios_detecta`: 1 s de tom + 3 s de silêncio + 1 s de tom → um intervalo com duração ≥ 2,5 s.
  - `test_concatenar_wav_com_pausa`: dois WAV de 1 s, pausa 0,25 → `abs(duracao-2.25) < 0.05`.
  - `test_extrair_frames`: `n=6` → 6 arquivos existentes.
  - `test_rodar_erro_tem_stderr`: argumentos inválidos → `ErroMidia` cuja mensagem contém texto do stderr.
- [ ] **Step 2:** `uv run pytest tests/test_midia.py -q` → FALHA.
- [ ] **Step 3:** Implementar.
- [ ] **Step 4:** `uv run pytest -q` → PASSA.
- [ ] **Step 5:** Commit `feat: utilitários ffmpeg/ffprobe`.

---

### Task 5: Provedores de voz (TTS) e serviço Kokoro

**Files:**
- Create: `autocanal/provedores/__init__.py` (com `ErroProvedor(Exception)`), `autocanal/provedores/tts.py`, `autocanal/servicos.py`, `servicos/kokoro/docker-compose.yml`, `tests/test_tts.py`

**Interfaces:**
- Consumes: `Config`, `Canal`, `ConfigTTS`.
- Produces:
  - `ProvedorTTS(Protocol)`: `nome: str`; `sintetizar(texto: str, destino: Path) -> Path` (WAV).
  - `TTSFalso(segundos_por_palavra: float = 0.4, taxa: int = 24000)` — WAV de tom 220 Hz (módulo `wave`), duração `max(0.5, palavras*segundos_por_palavra)`. `nome = "falso"`.
  - `KokoroTTS(url: str, voz: str, velocidade: float = 1.0, sessao=requests)` — `POST {url}/v1/audio/speech` JSON `{"model":"kokoro","input":texto,"voice":voz,"response_format":"wav","speed":velocidade}`; HTTP ≠ 200 ou conexão recusada → `ErroProvedor`. `nome = "kokoro"`.
  - `GoogleTTS(api_key: str, voz: str, idioma: str = "pt-BR", sessao=requests)` — `POST https://texttospeech.googleapis.com/v1/text:synthesize?key=<api_key>` com `{"input":{"text":texto},"voice":{"languageCode":idioma,"name":voz},"audioConfig":{"audioEncoding":"LINEAR16","sampleRateHertz":24000}}`; grava `base64` de `audioContent`. Mensagem de erro **sem** a chave. `nome = "google"`.
  - `TTSComReserva(principal: ProvedorTTS, reserva: ProvedorTTS | None)` — tenta o principal; `ErroProvedor` → reserva; expõe `usou_reserva: bool` e `nome` do que efetivamente sintetizou por último.
  - `criar_tts(cfg: Config, canal: Canal) -> TTSComReserva` — `google` sem `GOOGLE_TTS_API_KEY` → usa a reserva como principal; `falso` → `TTSFalso`. Kokoro chama `servicos.garantir("kokoro", ...)` antes da primeira síntese.
  - `servicos.garantir(nome: str, raiz: Path, url_saude: str, timeout_s: int = 180, executar=subprocess.run, obter=requests.get) -> None` — se `GET url_saude` falhar, roda `docker compose -f servicos/<nome>/docker-compose.yml up -d` e espera ficar saudável; timeout → `ErroProvedor`. `servicos.parar(nome: str, raiz: Path, executar=subprocess.run) -> None`.
- `servicos/kokoro/docker-compose.yml`: imagem `ghcr.io/remsky/kokoro-fastapi-cpu` com **tag fixa** (escolha uma existente via `docker pull` e registre em `LICENCAS.md`), porta `127.0.0.1:8880:8880`, sem `restart`.

- [ ] **Step 1: Testes que falham** em `tests/test_tts.py` (sessão HTTP simulada, sem rede):
  - `test_tts_falso_duracao(midia, tmp_path)`: 10 palavras → `abs(duracao-4.0) < 0.05`.
  - `test_kokoro_corpo_da_requisicao`: sessão falsa captura `json` com `voice == "pf_dora"` e `response_format == "wav"`; bytes devolvidos gravados.
  - `test_google_decodifica_base64_e_esconde_chave`: resposta `{"audioContent": base64(b"RIFF...")}` → arquivo com esses bytes; resposta 403 → `ErroProvedor` cuja mensagem **não** contém a chave.
  - `test_reserva_usada_quando_principal_falha`: principal levanta `ErroProvedor` → `usou_reserva is True`.
  - `test_criar_tts_google_sem_chave_vira_reserva(cfg)`: canal com `provedor: google`, sem chave → principal efetivo é Kokoro.
  - `test_garantir_sobe_compose`: `obter` falha 1× e depois responde 200 → `executar` chamado com `docker compose -f .../servicos/kokoro/docker-compose.yml up -d`.
- [ ] **Step 2:** `uv run pytest tests/test_tts.py -q` → FALHA.
- [ ] **Step 3:** Implementar. Descobrir a rota de saúde do Kokoro-FastAPI (ex.: `/health`) e confirmar que as vozes pt-BR (`pf_dora`, `pm_alex`, `pm_santa`) existem com `GET /v1/audio/voices`.
- [ ] **Step 4:** `uv run pytest -q` → PASSA. Teste `@pytest.mark.real` `test_kokoro_real_pt_br`: sobe o serviço e sintetiza "Olá, isto é um teste." → WAV com duração > 1 s. Rodar com `uv run pytest -m real tests/test_tts.py -q`.
- [ ] **Step 5:** Commit `feat: provedores TTS (Google, Kokoro, falso) com reserva`.

---

### Task 6: Provedores de imagens e b-roll

**Files:**
- Create: `autocanal/provedores/imagens.py`, `tests/test_imagens.py`

**Interfaces:**
- Consumes: `Config`, `Canal`, `Paleta`, `ErroProvedor`.
- Produces:
  - `MidiaObtida` (dataclass frozen): `caminho: Path, tipo: Literal["imagem","video"], origem: str, url: str, licenca: str, autor: str`; método `como_dict() -> dict` (caminho como str).
  - `ProvedorImagens(Protocol)`: `nome: str`; `buscar(consulta: str, orientacao: Literal["retrato","paisagem"], destino_sem_ext: Path, excluir: set[str]) -> MidiaObtida | None` (`excluir` = URLs já usadas no job).
  - `PexelsImagens(api_key: str, sessao=requests)` — tenta vídeos (`https://api.pexels.com/videos/search`, `orientation=portrait|landscape`, `per_page=8`; escolhe o arquivo com altura ≥ 1080 mais próxima de 1920 no retrato), depois fotos (`/v1/search`, tamanho `large2x`); header `Authorization: <key>`; licença `"Pexels License"`.
  - `PixabayImagens(api_key: str, sessao=requests)` — `https://pixabay.com/api/videos/` e depois `https://pixabay.com/api/` (`orientation=vertical|horizontal`, `safesearch=true`); licença `"Pixabay Content License"`.
  - `FundoProcedural(paleta: Paleta, fonte_arquivo: Path, largura: int, altura: int)` — PNG determinístico: gradiente entre `fundo` e um tom derivado de `destaque`, ângulo e formas derivados de `sha256(consulta)`; `origem="procedural"`, `licenca="gerado localmente"`, `url="procedural:<sha8>"`, `autor="autocanal"`. Nunca devolve `None`.
  - `criar_cadeia(cfg: Config, canal: Canal, largura: int, altura: int) -> list[ProvedorImagens]` — Pexels se `PEXELS_API_KEY`, Pixabay se `PIXABAY_API_KEY`, sempre `FundoProcedural` por último.
  - `buscar_em_cadeia(cadeia, consulta, orientacao, destino_sem_ext, excluir) -> MidiaObtida` — `ErroProvedor` ou `None` passa para o próximo.

- [ ] **Step 1: Testes que falham** (sessões simuladas):
  - `test_procedural_deterministico(tmp_path)`: mesma consulta → mesmos bytes; consultas diferentes → bytes diferentes; imagem 1080×1920.
  - `test_pexels_prefere_video_retrato`: resposta com arquivos 720p e 1080×1920 → baixa o de 1080×1920; `licenca == "Pexels License"`.
  - `test_pexels_respeita_excluir`: único resultado com URL em `excluir` → cai para fotos.
  - `test_cadeia_cai_para_procedural`: Pexels levanta `ErroProvedor`, Pixabay devolve `None` → `origem == "procedural"`.
  - `test_criar_cadeia_sem_chaves(cfg)`: só `FundoProcedural`.
- [ ] **Step 2:** `uv run pytest tests/test_imagens.py -q` → FALHA.
- [ ] **Step 3:** Implementar.
- [ ] **Step 4:** `uv run pytest -q` → PASSA.
- [ ] **Step 5:** Commit `feat: provedores de imagens (Pexels, Pixabay, procedural)`.

---

### Task 7: Legendas (whisper + ASS)

**Files:**
- Create: `autocanal/legendas.py`, `tests/test_legendas.py`

**Interfaces:**
- Produces:
  - `Palavra(texto: str, inicio: float, fim: float)`, `Legenda(texto: str, inicio: float, fim: float)` (dataclasses frozen)
  - `transcrever(wav: Path, *, modelo: str = "small", dispositivo: str = "cpu", compute_type: str = "int8", idioma: str = "pt") -> list[Palavra]` — faster-whisper com `word_timestamps=True`, `vad_filter=False`.
  - `agrupar(palavras: list[Palavra], max_palavras: int = 3, max_duracao_s: float = 1.6) -> list[Legenda]` — fecha o grupo também em pontuação final (`.`, `!`, `?`).
  - `cor_ass(hex_rgb: str) -> str` — `"#FACC15"` → `"&H0015CCFA"`.
  - `escapar_ass(texto: str) -> str` — `\` → `\\`, `{` → `\{`, `}` → `\}`, quebras de linha → espaço.
  - `gerar_ass(legendas: list[Legenda], destino: Path, *, largura: int, altura: int, fonte_nome: str, tamanho: int, cor_texto: str, cor_destaque: str, margem_v: int) -> Path` — estilo com contorno 6, sombra 0, alinhamento 2 (inferior centro); texto em maiúsculas; tempos `H:MM:SS.cc`.

- [ ] **Step 1: Testes que falham:**
  - `test_agrupar_por_tamanho_e_pontuacao`: 7 palavras com "fim." na 2ª → grupos `["olá fim.", ...]` e nenhum grupo com > 3 palavras.
  - `test_agrupar_respeita_duracao`: 3 palavras abrangendo 2,0 s → divididas.
  - `test_cor_ass`: valor exato acima.
  - `test_escapar_ass`: `escapar_ass('a{b}c\\d\n"e" 🚀') == 'a\\{b\\}c\\\\d "e" 🚀'`.
  - `test_gerar_ass_cabecalho_e_tempo(tmp_path)`: contém `PlayResX: 1080`, `PlayResY: 1920` e `Dialogue: 0,0:00:01.50,0:00:02.00`.
  - `@pytest.mark.real test_transcrever_kokoro`: (depois da Task 5) sintetiza frase pt-BR e transcreve → contém ≥ 50% das palavras.
- [ ] **Step 2:** `uv run pytest tests/test_legendas.py -q` → FALHA.
- [ ] **Step 3:** Implementar.
- [ ] **Step 4:** `uv run pytest -q` → PASSA.
- [ ] **Step 5:** Commit `feat: legendas com faster-whisper e ASS`.

---

### Task 8: Contexto de etapas, formato `short`, produtor e etapas de roteiro/narração/legendas/visuais

**Files:**
- Create: `autocanal/formatos.py`, `autocanal/texto.py`, `autocanal/etapas/__init__.py`, `autocanal/etapas/base.py`, `autocanal/etapas/roteiro.py`, `autocanal/etapas/narracao.py`, `autocanal/etapas/legendas.py`, `autocanal/etapas/visuais.py`, `autocanal/prompts/roteiro_short.md`, `autocanal/produtor.py`, `tests/test_etapas_iniciais.py`, `tests/test_produtor.py`

**Interfaces:**
- Consumes: Tasks 1–7.
- Produces:
  - `formatos.Formato(nome, largura, altura, duracao_min_s, duracao_max_s, palavras_min, palavras_max, orientacao, etapas: tuple[str, ...])`; `FORMATOS = {"short": Formato("short", 1080, 1920, 30, 60, 75, 150, "retrato", ("roteiro","narracao","legendas","visuais","render","metadados","revisao"))}`.
  - `texto.contar_palavras(s: str) -> int`; `texto.sanitizar_texto(s: str) -> str` (troca `<`→`‹`, `>`→`›`, remove caracteres de controle exceto `\n`).
  - `etapas.base.Contexto` (dataclass): `cfg: Config, con, job: Job, canal: Canal, formato: Formato, pasta: Path, midia: Midia`; `contexto_para(cfg, con, job) -> Contexto` (cria `dados/jobs/<id>/`); `ler_json(ctx, nome) -> dict`; `gravar_json(ctx, nome, dados) -> Path`; `ErroEtapa(Exception)`; `titulos_recentes(ctx, n=15) -> list[str]` (de `metadados.json` dos jobs do canal); `perfil_voz(ctx) -> str` (conteúdo de `canal.perfil_voz` ou texto padrão).
  - `etapas.REGISTRO: dict[str, Callable[[Contexto], None]]` com as etapas desta task + as das Tasks 9 e 10 conforme forem criadas.
  - Artefatos: `roteiro.json` = saída do schema `ROTEIRO_SHORT` (abaixo); `narracao.json` = `{"segmentos":[{"arquivo","inicio","fim"}], "duracao", "provedor", "usou_reserva"}` e `narracao.wav`; `transcricao.json` = `{"texto", "palavras":[{"texto","inicio","fim"}]}` e `legendas.ass`; `visuais/seg_NN.<ext>` e `fontes_midia.json` = lista de `MidiaObtida.como_dict()`.
  - `ROTEIRO_SHORT` (em `etapas/roteiro.py`): objeto com `required ["gancho","nota_gancho","segmentos","fontes","resumo"]`; `segmentos`: 3–12 itens `{texto: string, visual: string}` (`visual` = consulta curta **em inglês** para banco de imagens); `fontes`: itens `{afirmacao: string, url: string}`.
  - `produtor.MAX_TENTATIVAS_ETAPA = 3`; `produtor.produzir(cfg, con, job_id: str, *, registro: dict | None = None) -> Estado`.

- [ ] **Step 1: Prompt** `autocanal/prompts/roteiro_short.md` com placeholders `{tema}`, `{perfil_voz}`, `{titulos_recentes}`, `{palavras_min}`, `{palavras_max}`, `{problemas_anteriores}`, `{dir_plugin}`. Instruções: usar a skill `youtube-agent:yt-script`; gerar 5 ganchos, pontuar com `python3 {dir_plugin}/skills/yt-script/hookscore.py --json --hook "<gancho>"` e usar o melhor; perfil de voz fornecido substitui `~/.claude/youtube/voice.md`; não fazer perguntas; não ler nem escrever fora da pasta atual; toda afirmação factual tem fonte via WebSearch; nunca inventar números; variar estrutura em relação aos títulos recentes; pt-BR; texto falado entre os limites de palavras; corrigir os problemas anteriores quando houver.
- [ ] **Step 2: Testes que falham** em `tests/test_etapas_iniciais.py` (usar `claude_falso`, `TTSFalso`, cadeia só procedural):
  - `test_roteiro_grava_json_e_valida_palavras`: Claude falso devolve roteiro com 100 palavras → `roteiro.json` gravado; com 20 palavras → `ErroEtapa`.
  - `test_roteiro_inclui_problemas_anteriores`: com `problemas_revisao.json` = `{"problemas":["muito longo"]}` no job → o prompt (argv registrado) contém "muito longo".
  - `test_narracao_tempos_por_segmento`: 3 segmentos → `narracao.json` com inícios crescentes e `duracao` ≈ soma + 2 pausas de 0,25 s.
  - `test_legendas_usa_transcricao` (monkeypatch `legendas.transcrever` devolvendo palavras fixas) → `legendas.ass` e `transcricao.json` existem.
  - `test_visuais_um_por_segmento_sem_repetir`: 3 segmentos → 3 arquivos e 3 entradas em `fontes_midia.json` com URLs distintas.
- [ ] **Step 3: Testes que falham** em `tests/test_produtor.py` (registro com etapas falsas):
  - `test_produzir_executa_em_ordem_e_pula_concluidas`: etapa "roteiro" já `ok` → não é chamada; as demais rodam em ordem; estado final vem da etapa `revisao` falsa (`aprovado`).
  - `test_produzir_falha_apos_tres_tentativas`: etapa que sempre levanta → após 3 chamadas de `produzir`, job `falhou` com `erro` contendo o nome da etapa.
  - `test_produzir_limite_uso_nao_conta_tentativa`: etapa levanta `LimiteUsoClaude` → `tentativas_etapa == 0`, estado continua `produzindo`.
  - `test_produzir_refazer_volta_a_produzir`: revisão falsa reprova na 1ª e aprova na 2ª → estado final `aprovado` numa única chamada.
- [ ] **Step 4:** Rodar ambos → FALHA.
- [ ] **Step 5:** Implementar `produzir`: `planejado → produzindo`; percorre `formato.etapas` pulando `ok`; antes de `revisao` muda para `em_revisao`; marca `rodando`/`ok`/`erro`; `LimiteUsoClaude` → retorna o estado atual sem marcar erro; outra exceção → `marcar_etapa(erro)` e, com `tentativas ≥ 3`, `falhou` (erro `"<etapa>: <mensagem>"`), retornando; repete o percurso enquanto o estado voltar a `produzindo` depois da revisão (a revisão limita as rodadas). Implementar as quatro etapas.
- [ ] **Step 6:** `uv run pytest -q` → PASSA.
- [ ] **Step 7:** Commit `feat: produtor e etapas iniciais do short`.

---

### Task 9: Render do short e integração offline ponta a ponta

**Files:**
- Create: `autocanal/etapas/render.py`, `tests/test_render.py`, `tests/test_integracao_short.py`

**Interfaces:**
- Consumes: `Contexto`, artefatos da Task 8, `Midia`.
- Produces:
  - `comando_segmento(entrada: Path, tipo: Literal["imagem","video"], duracao: float, largura: int, altura: int, saida: Path, fps: int = 30) -> list[str]` — imagem: `-loop 1 -t <dur> -i <img>` com `scale=<L>:<A>:force_original_aspect_ratio=increase,crop=<L>:<A>,zoompan=z='min(zoom+0.0007,1.12)':d=<frames>:s=<L>x<A>:fps=<fps>`; vídeo: `-stream_loop -1 -t <dur> -i <vid>` com `scale…increase,crop…,fps=<fps>,setsar=1`; saída sem áudio, `libx264 -preset veryfast -crf 20 -pix_fmt yuv420p`.
  - `comando_final(base: Path, narracao: Path, legendas_nome: str, trilha: Path | None, saida: Path) -> list[str]` — vídeo `ass=<legendas_nome>` (nome relativo; rodar com `cwd` = pasta do job); áudio narração + (trilha com `volume=-20dB`, `amix=duration=first`) → `loudnorm=I=-14:TP=-1.5:LRA=11`; `aac -b:a 192k`; `-shortest`; `-movflags +faststart`.
  - `executar(ctx)` — gera `render/seg_NN.mp4` (duração = fim−início do segmento; o último absorve o restante até `narracao.duracao`), concatena (demuxer concat) em `render/base.mp4`, aplica o final em `video.mp4`. Trilha: arquivo aleatório (semente = `job.id`) de `dados/biblioteca/musica/*.{wav,mp3}`, ou nenhuma.

- [ ] **Step 1: Testes que falham** em `tests/test_render.py`:
  - `test_comando_segmento_imagem`: contém `"-loop"`, `zoompan`, `s=1080x1920`, `"-an"` e termina no caminho de saída.
  - `test_comando_final_sem_trilha`: contém `ass=legendas.ass` e `loudnorm=I=-14` e não contém `amix`.
  - `test_comando_final_com_trilha`: contém `volume=-20dB` e `amix=`.
- [ ] **Step 2: Teste de integração que falha** `tests/test_integracao_short.py::test_short_offline_gera_mp4_valido`: config de teste (`tts: falso`, sem chaves); `claude_falso` responde roteiro (≈ 100 palavras em 4 segmentos, para o TTS falso dar 30–60 s), metadados e revisão aprovando; `legendas.transcrever` monkeypatched para palavras sintéticas; `produzir` → estado `aprovado`; `info(video.mp4)`: 1080×1920, `tem_audio`, `30 <= duracao <= 60`; `-15.5 <= loudness <= -12.5`. (Depende das Tasks 10; marque com `xfail(strict=False)` até lá ou ordene a execução 9→10 e rode no fim da Task 10.)
- [ ] **Step 3:** `uv run pytest tests/test_render.py -q` → FALHA.
- [ ] **Step 4:** Implementar `render.py` e registrar `render` em `etapas.REGISTRO`.
- [ ] **Step 5:** `uv run pytest tests/test_render.py -q` → PASSA; renderizar manualmente um job de teste com `TTSFalso` e conferir com `ffprobe`.
- [ ] **Step 6:** Commit `feat: render do short com zoompan, legendas e loudnorm`.

---

### Task 10: Metadados e revisão automática

**Files:**
- Create: `autocanal/etapas/metadados.py`, `autocanal/etapas/revisao.py`, `autocanal/ferramentas_plugin.py`, `autocanal/prompts/metadados.md`, `autocanal/prompts/revisao.md`, `tests/test_metadados.py`, `tests/test_revisao.py`

**Interfaces:**
- Consumes: Tasks 2–9.
- Produces:
  - `ferramentas_plugin.nota_titulo(cfg, titulo: str, thumb: str | None) -> tuple[int, list[str]]` — roda `python3 <plugin_dir>/skills/yt-package/title.py --title … [--thumb …] --json` e devolve (`score`, `issues`) do primeiro item.
  - `METADADOS` schema: `required ["titulo","texto_thumb","descricao","tags","consultas_alvo"]`; `titulo` ≤ 100; `descricao` ≤ 4500; `tags` ≤ 15 itens; `capitulos` opcional `[{tempo, titulo}]`; `consultas_alvo` exatamente 3.
  - `metadados.json` = `{titulo, texto_thumb, descricao_final, tags, capitulos, consultas_alvo, nota_titulo, problemas_titulo, sintetico: false}`; `descricao_final` = descrição + capítulos (`MM:SS Título`, se houver) + bloco `Fontes:` com as URLs do roteiro + `#shorts` para o formato short; tudo passado por `sanitizar_texto`; tags cortadas até a soma de caracteres + vírgulas ≤ 500.
  - `revisao.similaridade(a: str, b: str, n: int = 5) -> float` (Jaccard de n-gramas de palavras minúsculas, sem pontuação; textos com < n palavras → 0.0).
  - `revisao.checagens(ctx) -> list[tuple[str, str]]` — lista de `(problema, etapa_para_refazer)`; mapeamento: duração → `roteiro`; resolução/áudio/loudness/silêncio → `render`; cobertura de legendas (último fim < duração da narração − 1 s) → `legendas`; nota de título < mínimo → `metadados`; mídia sem licença ou contagem ≠ segmentos → `visuais`; similaridade ≥ máximo → `roteiro`.
  - `REVISAO` schema: `{aprovado: boolean, nota: number, problemas: string[], etapa_para_refazer: string|null ∈ {roteiro, narracao, visuais, render, metadados, null}, contains_synthetic_media: boolean}`.
  - `revisao.executar(ctx)` — rodada = `tentativas_revisao + 1`; checagens com problema → não chama o Claude; senão extrai 6 quadros em `revisao/quadros/` e chama o revisor (ferramentas `Read, WebFetch, WebSearch, Bash(python3:*)`). Grava `revisao_<rodada>.json` e linha em `revisoes`. Aprovado → `metadados.json.sintetico = contains_synthetic_media`, estado `aprovado`. Reprovado: rodada ≥ `max_rodadas` → `quarentena`; senão grava `problemas_revisao.json`, `resetar_etapas` da etapa de refazer (a mais cedo, na ordem do formato) até `revisao`, estado `produzindo`. Atualiza `tentativas_revisao`.
- Prompts: `metadados.md` (skills `youtube-agent:yt-package`, `yt-seo`, `yt-chapters`; gerar 10 títulos, rodar `title.py`, escolher o melhor; ≤ 3 palavras na thumb, diferentes do título; 3 consultas-alvo; sem perguntas). `revisao.md` (papel de revisor rigoroso e independente; lista do spec §7 itens 1–5; ler `roteiro.json`, `transcricao.json`, `metadados.json`, `fontes_midia.json`, quadros e os últimos 15 títulos fornecidos; abrir cada URL de fonte; afirmação sem fonte = reprovação).

- [ ] **Step 1: Testes que falham** em `tests/test_metadados.py`:
  - `test_sanitizar_texto`: `sanitizar_texto('A <b> & "c" 🚀\x07') == 'A ‹b› & "c" 🚀'`.
  - `test_descricao_final_com_fontes_e_shorts`: contém `Fontes:`, as URLs do roteiro e termina com `#shorts`.
  - `test_tags_cortadas_em_500`: 15 tags de 40 caracteres → soma + vírgulas ≤ 500.
  - `test_nota_titulo_real_do_plugin(cfg)`: `nota_titulo(cfg, "Como a IA vai mudar seu trabalho em 2027", "ROBÔ CHEFE")[0] == 100` (script local, sem rede).
- [ ] **Step 2: Testes que falham** em `tests/test_revisao.py`:
  - `test_similaridade`: textos iguais → 1.0; disjuntos → 0.0.
  - `test_checagem_duracao_manda_para_roteiro`: vídeo de 75 s → contém `("…duração…", "roteiro")`.
  - `test_checagens_ok_chama_claude_e_aprova`: tudo dentro dos limites + revisor falso aprovando com `contains_synthetic_media: true` → estado `aprovado`, `metadados.json["sintetico"] is True`.
  - `test_reprovado_primeira_rodada_refaz`: revisor reprova com `etapa_para_refazer: "visuais"` → estado `produzindo`, etapas `visuais`…`revisao` resetadas, `roteiro` continua `ok`, `problemas_revisao.json` gravado.
  - `test_segunda_reprovacao_quarentena`: `tentativas_revisao=1` e reprovação → `quarentena`.
  - `test_checagem_falha_nao_chama_claude`: com problema determinístico, o `claude_falso` registra 0 chamadas.
- [ ] **Step 3:** Rodar → FALHA.
- [ ] **Step 4:** Implementar e registrar `metadados` e `revisao` em `etapas.REGISTRO`.
- [ ] **Step 5:** `uv run pytest -q` → PASSA, incluindo `test_short_offline_gera_mp4_valido` da Task 9 (retire o `xfail`).
- [ ] **Step 6:** Commit `feat: metadados com plugin e revisão automática em duas camadas`.

---

### Task 11: YouTube — OAuth, upload, veto e publicação

**Files:**
- Create: `autocanal/youtube/__init__.py`, `autocanal/youtube/oauth.py`, `autocanal/youtube/upload.py`, `autocanal/grade.py`, `autocanal/publicador.py`, `tests/test_oauth.py`, `tests/test_upload.py`, `tests/test_grade.py`, `tests/test_publicador.py`
- Modify: `autocanal/cli.py` (comandos `auth`, `publicar`, `vetar`)

**Interfaces:**
- Consumes: `Config`, `fila`, `metadados.json`, `video.mp4`.
- Produces:
  - `oauth.ESCOPOS = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube", "https://www.googleapis.com/auth/yt-analytics.readonly"]`; `AuthExpirada(Exception)`.
  - `oauth.autorizar(client_secret: Path, destino_token: Path, porta: int = 8080, fluxo_factory=InstalledAppFlow.from_client_secrets_file) -> Credentials` — `run_local_server(port=porta, access_type="offline", prompt="consent")`; salva com `salvar`.
  - `oauth.salvar(creds, token: Path) -> None` (cria diretório, `chmod 600`); `oauth.carregar(token: Path, requisicao=None) -> Credentials` (renova se expirado e salva; `RefreshError` → `AuthExpirada`); `oauth.cliente(creds)` → `build("youtube", "v3", credentials=creds, cache_discovery=False)`; `oauth.canal_autenticado(youtube) -> tuple[str, str]` (`channels().list(part="snippet", mine=True)`; sem canal → `AuthExpirada("conta sem canal")`).
  - `upload.CotaEsgotada(Exception)`, `upload.ErroUpload(Exception)`.
  - `upload.formatar_publish_at(dt: datetime) -> str` — UTC `YYYY-MM-DDTHH:MM:SSZ`.
  - `upload.montar_corpo(meta: dict, *, publish_at: datetime, categoria_id: str, idioma: str, sintetico: bool) -> dict` — `snippet{title, description=descricao_final, tags, categoryId, defaultLanguage, defaultAudioLanguage}`, `status{privacyStatus:"private", publishAt, selfDeclaredMadeForKids: False, containsSyntheticMedia}`.
  - `upload.enviar(youtube, video: Path, corpo: dict, *, chunk_bytes: int = 8*1024*1024, max_tentativas: int = 5, dormir=time.sleep, media_factory=MediaFileUpload) -> str` — `videos().insert(part="snippet,status", body=corpo, media_body=…resumable=True)`, laço `next_chunk()`; `HttpError` 500/502/503/504 e `OSError` → backoff `2**n` s até `max_tentativas`; 403 com `quotaExceeded`/`uploadLimitExceeded` → `CotaEsgotada`; outros → `ErroUpload`.
  - `upload.definir_thumbnail(youtube, video_id: str, imagem: Path) -> None`; `upload.vetar(youtube, video_id: str) -> None` (`videos().update(part="status", body={"id":…, "status":{"privacyStatus":"private"}})`); `upload.status(youtube, ids: list[str]) -> dict[str, str]`.
  - `grade.DIAS = {"seg":0,"ter":1,"qua":2,"qui":3,"sex":4,"sab":5,"dom":6}`; `grade.slots(canal: Canal, formato: str, inicio: datetime, fim: datetime, fuso: str) -> list[datetime]` (UTC aware, ordenados, em `[inicio, fim)`); `grade.proximo_slot(canal, formato, depois_de: datetime, ocupados: set[datetime], fuso: str) -> datetime` (busca até 60 dias; sem slot → `ValueError`).
  - `publicador.publicar_aprovados(cfg, con, agora: datetime, *, youtube_por_canal: Callable[[str], object]) -> list[str]` — para cada job `aprovado` (ordem de `publish_at`): pula canais com `canais_estado.auth_ok = 0`; se `publish_at < agora + 30 min` → `proximo_slot(depois_de=agora + 1 h, ocupados=publish_at dos jobs não descartados do canal/formato)` e `atualizar`; envia; thumbnail se `formato != "short"` e existir `thumbnail.png`; `agendado` + `youtube_video_id`; soma 100 unidades em `cota` (bucket `upload`). `AuthExpirada` → `auth_ok=0`, `ultimo_erro`, segue para outros canais. `CotaEsgotada` → para tudo e retorna. `ErroUpload` → job `falhou`. Retorna ids enviados.
  - `publicador.sincronizar_publicados(cfg, con, agora, *, youtube_por_canal) -> list[str]` — `agendado` com `publish_at <= agora` e status `public` → `publicado`.
  - `publicador.youtube_real(cfg) -> Callable[[str], object]` — carrega `dados/tokens/<canal>.json`.
  - CLI: `autocanal auth <canal>` (autoriza, confere o canal, grava `canais_estado` com `auth_ok=1` e `youtube_channel_id`; imprime o título do canal); `autocanal publicar [--job ID]`; `autocanal vetar <job>` (só `agendado`; chama `upload.vetar` e muda para `vetado`).

- [ ] **Step 1: Testes que falham** (`googleapiclient` simulado com `unittest.mock`, sem rede):
  - `tests/test_oauth.py`: `test_salvar_chmod_600`; `test_carregar_refresh_error_vira_auth_expirada` (creds expiradas cujo `refresh` levanta `RefreshError("invalid_grant")`); `test_autorizar_pede_offline_e_consent` (fluxo falso registra `access_type="offline"`, `prompt="consent"`, `port=8080`).
  - `tests/test_upload.py`: `test_formatar_publish_at` (`datetime(2026,10,25,15,0,tzinfo=ZoneInfo("America/Sao_Paulo"))` → `"2026-10-25T18:00:00Z"`); `test_montar_corpo_campos` (privado, `publishAt`, `selfDeclaredMadeForKids is False`, `containsSyntheticMedia` repassado); `test_enviar_retenta_503` (1º `next_chunk` levanta `HttpError` 503, 2º devolve `(None, {"id":"abc"})` → `"abc"`, `dormir` chamado com 1); `test_enviar_cota_esgotada` (403 `quotaExceeded` → `CotaEsgotada`).
  - `tests/test_grade.py`: `test_slots_semana` (grade seg/qua/sex 12:00, semana de 2026-10-26 → 3 slots, primeiro `2026-10-26T15:00Z`); `test_proximo_slot_pula_ocupado`.
  - `tests/test_publicador.py`: `test_publicar_envia_e_agenda`; `test_publicar_reagenda_publish_at_passado` (`publish_at` = ontem → corpo enviado com `publishAt` ≥ agora + 1 h e `publish_at` atualizado no banco); `test_publicar_auth_expirada_isola_canal` (canal A levanta `AuthExpirada`, canal B envia; A fica `aprovado` e `auth_ok=0`); `test_publicar_para_na_cota`; `test_sincronizar_marca_publicado`.
- [ ] **Step 2:** Rodar → FALHA.
- [ ] **Step 3:** Implementar os módulos e os comandos da CLI.
- [ ] **Step 4:** `uv run pytest -q` → PASSA. **Não** rodar `autocanal auth` (precisa de Laura e do redirect URI cadastrado).
- [ ] **Step 5:** Commit `feat: OAuth persistente, upload agendado, veto e publicação`.

---

### Task 12: Planejador, agendador (tick), resumo diário e CLI de operação

**Files:**
- Create: `autocanal/planejador.py`, `autocanal/agendador.py`, `autocanal/relatorio.py`, `autocanal/prompts/temas.md`, `tests/test_planejador.py`, `tests/test_agendador.py`, `tests/test_relatorio.py`
- Modify: `autocanal/cli.py`

**Interfaces:**
- Consumes: `grade`, `fila`, `produtor.produzir`, `publicador.*`, `claude.chamar`.
- Produces:
  - `planejador.planejar(cfg, con, agora: datetime, *, sugerir_temas: Callable[[Canal, str, int], list[str]]) -> list[Job]` — para cada canal com `nicho` não vazio e cada formato em `FORMATOS` presente na grade: slots em `[agora + 2 h, agora + (folga_dias + 7) dias)` sem job não descartado no mesmo `publish_at` → cria jobs com os temas sugeridos (um pedido de N temas por canal/formato); grava os temas em `temas`. Canal sem nicho → ignorado com aviso.
  - `planejador.sugerir_temas_claude(cfg, con) -> Callable[[Canal, str, int], list[str]]` — Claude com `youtube-agent:yt-plan` (e WebSearch para tendências do nicho), schema `{"temas":[{"tema": string, "angulo": string}]}` com exatamente N itens; evita repetir `temas` já usados e títulos recentes.
  - `agendador.TravaOcupada(Exception)`; `agendador.trava(caminho: Path)` (context manager, `fcntl.flock` `LOCK_EX | LOCK_NB`).
  - `agendador.ResumoTick` (dataclass): `pulou_por_trava: bool, planejados: int, produzido: str | None, enviados: list[str], publicados: list[str], resumo: Path | None`.
  - `agendador.tick(cfg, con, agora: datetime, *, planejar_fn, produzir_fn, publicar_fn, sincronizar_fn, resumo_fn) -> ResumoTick` — dentro da trava `dados/tick.lock`: (1) `planejar_fn` se houver menos jobs não finais do que slots nos próximos `folga_dias` dias; (2) `produzir_fn` no `proximo_para_produzir`; (3) `publicar_fn`; (4) `sincronizar_fn`; (5) `resumo_fn` se ainda não existe resumo de hoje e a hora local ≥ 07:00.
  - `relatorio.gerar_resumo(cfg, con, dia: date) -> Path` — `dados/relatorios/AAAA-MM-DD.md` com seções: Produzidos, Agendados (com link `https://youtu.be/<id>` e horário local), Publicados, Quarentena (motivos), Falhas, Alertas (canais com `auth_ok=0`, cota), Supervisão (lista aprovados enquanto `hoje < primeiro upload + supervisao_dias`, com o comando `autocanal vetar <job>`), Consumo do Claude (soma `custo_usd` e chamadas do dia). `relatorio.notificar(titulo: str, texto: str) -> None` (`notify-send` se existir; silencioso caso contrário).
  - CLI: `status` (contagem por estado + próximos 10 jobs), `novo <canal> <formato> <tema> [--quando "AAAA-MM-DD HH:MM"]` (sem `--quando` → próximo slot), `planejar`, `produzir [JOB]` (sem JOB → próximo), `revisar <job>` (reseta `revisao` e roda), `aprovar <job>`, `descartar <job>`, `tick`, `instalar-timer [--ativar]` (escreve `~/.config/systemd/user/autocanal.service` com `ExecStart=<raiz>/.venv/bin/autocanal --raiz <raiz> tick` e `autocanal.timer` com `OnCalendar=*:0/15`, `Persistent=true`; **só** roda `systemctl --user enable --now autocanal.timer` com `--ativar`).

- [ ] **Step 1: Testes que falham:**
  - `tests/test_planejador.py`: `test_planejar_preenche_slots` (grade seg/qua/sex, `folga_dias=3`, agora = segunda 2026-10-26 08:00 local → jobs criados para todos os slots da janela, temas na ordem devolvida); `test_planejar_nao_duplica` (2ª chamada cria 0); `test_planejar_ignora_canal_sem_nicho`.
  - `tests/test_agendador.py`: `test_tick_ordem_das_fases` (funções falsas registram a ordem planejar→produzir→publicar→sincronizar); `test_tick_concorrente_sai_pela_trava` (trava já segura em outro descritor → `pulou_por_trava is True` e nenhuma fase chamada); `test_tick_resumo_uma_vez_por_dia`.
  - `tests/test_relatorio.py`: `test_resumo_tem_secoes_e_links` (job `agendado` com `youtube_video_id="abc"` → arquivo contém `https://youtu.be/abc` e `## Quarentena`).
- [ ] **Step 2:** Rodar → FALHA.
- [ ] **Step 3:** Implementar e ligar à CLI.
- [ ] **Step 4:** `uv run pytest -q` → PASSA. `uv run autocanal instalar-timer` (sem `--ativar`) → arquivos escritos, timer **não** habilitado (`systemctl --user is-enabled autocanal.timer` ≠ `enabled`).
- [ ] **Step 5:** Commit `feat: planejador, tick com trava, resumo diário e CLI de operação`.

---

### Task 13: Integração com o Claude do projeto, documentação e prova real

**Files:**
- Create: `CLAUDE.md`, `.claude/commands/fila.md`, `.claude/commands/novo-video.md`, `.claude/commands/quarentena.md`, `README.md`, `config/canais.yaml` (cópia do exemplo + canal `teste` com nicho provisório "curiosidades de ciência", `tts.provedor: kokoro`, voz `pf_dora`), `config/vozes/teste.md`
- Modify: `LICENCAS.md`

**Interfaces:**
- Consumes: CLI completa.
- Produces: documentação de operação e um short real produzido localmente (não enviado).

- [ ] **Step 1:** `CLAUDE.md`: o que é o projeto, link para o spec e os planos, comandos da CLI, regras (nunca publicar/vetar/autenticar sem pedido explícito de Laura; nunca imprimir segredos; testes com `uv run pytest -q`). Comandos: `/fila` → roda `autocanal status` e resume; `/novo-video <canal> <formato> <tema>` → roda `autocanal novo …` e, se pedido, `produzir`; `/quarentena` → lê `revisao_*.json` dos jobs em quarentena, explica e oferece `aprovar`/`descartar`/`revisar`.
- [ ] **Step 2:** `README.md` com instalação, o checklist do spec §14 e o primeiro uso (`diagnostico` → `auth` → `planejar` → `tick`).
- [ ] **Step 3: Prova real** (marcada `real`, roda Claude, Kokoro e whisper de verdade, sem upload): `uv run autocanal novo teste short "por que o céu é azul"` e `uv run autocanal produzir <job>`. Esperado: estado `aprovado` ou `quarentena` com motivo claro; `video.mp4` 1080×1920 entre 30 e 60 s; `revisao_1.json` presente. Anotar duração total, chamadas e custo equivalente do Claude no relatório final.
- [ ] **Step 4:** `uv run pytest -q` → PASSA.
- [ ] **Step 5:** Commit `docs: CLAUDE.md, comandos do projeto, README e prova real do short`.
