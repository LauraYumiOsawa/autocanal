from autocanal.formatos import FORMATOS


def test_formato_medio():
    f = FORMATOS["medio"]
    assert (f.largura, f.altura) == (1920, 1080)
    assert (f.duracao_min_s, f.duracao_max_s) == (60, 300)
    assert (f.palavras_min, f.palavras_max) == (150, 750)
    assert f.orientacao == "paisagem"
    assert f.etapas == (
        "roteiro", "narracao", "legendas", "visuais", "render", "metadados", "thumbnail", "revisao"
    )
    assert (f.legenda_tamanho, f.legenda_margem_v, f.legenda_max_palavras,
            f.legenda_max_duracao_s) == (60, 70, 6, 2.5)


def test_formato_short_legendas_inalteradas():
    f = FORMATOS["short"]
    assert (f.legenda_tamanho, f.legenda_margem_v, f.legenda_max_palavras,
            f.legenda_max_duracao_s) == (88, 560, 3, 1.6)
