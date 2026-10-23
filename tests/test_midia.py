from pathlib import Path

import pytest

from autocanal.midia import ErroMidia


# sine do lavfi já tem pico de -18 dBFS; -2 dB leva o pico a -20 dBFS
def _seno(midia, destino: Path, dur: float = 3, vol_db: float = -2) -> Path:
    midia.rodar(
        ["-f", "lavfi", "-i", f"sine=f=440:d={dur}", "-af", f"volume={vol_db}dB", str(destino)]
    )
    return destino


def test_info_audio_e_video(midia, tmp_path):
    v = tmp_path / "v.mp4"
    midia.rodar(
        ["-f", "lavfi", "-i", "color=c=red:s=320x240:d=2:r=25",
         "-f", "lavfi", "-i", "sine=f=440:d=2",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(v)]
    )
    info = midia.info(v)
    assert info.largura == 320 and info.altura == 240
    assert info.tem_audio and info.tem_video
    assert abs(info.duracao - 2) < 0.1


def test_info_so_audio(midia, tmp_path):
    info = midia.info(_seno(midia, tmp_path / "a.wav", 1))
    assert info.tem_audio and not info.tem_video
    assert info.largura is None and info.altura is None


def test_loudness_seno(midia, tmp_path):
    assert -25 < midia.loudness(_seno(midia, tmp_path / "a.wav")) < -15


def test_silencios_detecta(midia, tmp_path):
    a = tmp_path / "s.wav"
    midia.rodar(
        ["-f", "lavfi", "-i", "sine=f=440:d=1",
         "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono:d=3",
         "-f", "lavfi", "-i", "sine=f=440:d=1",
         "-filter_complex", "[0:a]aformat=channel_layouts=mono[a];[2:a]aformat=channel_layouts=mono[c];[a][1:a][c]concat=n=3:v=0:a=1",
         str(a)]
    )
    s = midia.silencios(a)
    assert len(s) == 1
    assert s[0][1] - s[0][0] >= 2.5


def test_silencio_ate_o_fim_fecha_na_duracao(midia, tmp_path):
    a = tmp_path / "f.wav"
    midia.rodar(
        ["-f", "lavfi", "-i", "sine=f=440:d=1",
         "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono:d=3",
         "-filter_complex", "[0:a]aformat=channel_layouts=mono[a];[a][1:a]concat=n=2:v=0:a=1",
         str(a)]
    )
    s = midia.silencios(a)
    assert len(s) == 1
    assert abs(s[0][1] - midia.info(a).duracao) < 0.05


def test_concatenar_wav_com_pausa(midia, tmp_path):
    a = _seno(midia, tmp_path / "a.wav", 1)
    b = _seno(midia, tmp_path / "b.wav", 1)
    saida = midia.concatenar_wav([a, b], tmp_path / "c.wav", pausa_s=0.25)
    assert abs(midia.info(saida).duracao - 2.25) < 0.05


def test_extrair_frames(midia, tmp_path):
    v = tmp_path / "v.mp4"
    midia.rodar(
        ["-f", "lavfi", "-i", "color=c=red:s=320x240:d=2:r=25",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(v)]
    )
    frames = midia.extrair_frames(v, 6, tmp_path / "frames")
    assert len(frames) == 6
    assert all(f.exists() for f in frames)
    assert frames[0].name == "quadro_01.jpg"


def test_rodar_erro_tem_stderr(midia):
    with pytest.raises(ErroMidia) as e:
        midia.rodar(["-i", "/nao/existe.mp4", "-f", "null", "-"])
    assert "/nao/existe.mp4" in str(e.value)
