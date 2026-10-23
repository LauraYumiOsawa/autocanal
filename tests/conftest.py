from pathlib import Path

import pytest

from autocanal.config import carregar

VENDOR_BIN = Path("/home/losawa/autocanal/vendor/bin")
PLUGIN_DIR = Path(
    "/home/losawa/.claude/plugins/cache/youtube-agent-skill/youtube-agent/1.0.0"
)


@pytest.fixture
def raiz_teste(tmp_path: Path) -> Path:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "autocanal.yaml").write_text(
        f"""\
fuso: America/Sao_Paulo
folga_dias: 3
ffmpeg: {VENDOR_BIN / "ffmpeg"}
ffprobe: {VENDOR_BIN / "ffprobe"}
plugin_dir: {PLUGIN_DIR}
client_secret: config/client_secret.json
kokoro_url: http://127.0.0.1:8880
claude:
  bin: claude
  modelos: {{roteiro: null, metadados: sonnet, revisao: null, temas: sonnet}}
  max_turns: {{roteiro: 30, metadados: 20, revisao: 30, temas: 15}}
""",
        encoding="utf-8",
    )
    (tmp_path / "config" / "canais.yaml").write_text(
        """\
teste:
  nome: Canal de Teste
  categoria_id: "27"
  tts: {provedor: falso, voz: qualquer}
  paleta: {fundo: "#0F172A", texto: "#F8FAFC", destaque: "#FACC15"}
  fonte_arquivo: /usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf
  fonte_nome: DejaVu Sans
  perfil_voz: config/vozes/exemplo.md
  grade:
    - {formato: short, dias: [seg, qua, sex], hora: "12:00"}
""",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def cfg(raiz_teste: Path):
    return carregar(raiz_teste)
