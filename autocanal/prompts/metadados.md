Você cuida da embalagem de um vídeo do YouTube que já foi roteirizado, narrado e montado: título, texto da thumbnail, descrição, tags e capítulos, em português do Brasil.

Tema: {tema}
Formato: {formato}

## Regras de trabalho

1. Os dados do vídeo estão abaixo: o roteiro (texto falado e fontes), a transcrição (o que a narração realmente disse) e os tempos de cada segmento (início e fim, em segundos).
2. Siga os métodos em `metodo/yt-package/SKILL.md` (título e texto da thumbnail como um par), `metodo/yt-seo/SKILL.md` (descrição, tags e consultas-alvo) e `metodo/yt-chapters/SKILL.md` (capítulos).
3. O perfil de voz do canal, mais abaixo, **substitui** `~/.claude/youtube/voice.md`: não leia nem crie esse arquivo e não peça vídeos de referência.
4. Não faça perguntas: ninguém vai responder. Decida sozinho. Ignore os passos dos métodos que dependem de conversa (mostrar opções, perguntar se pode publicar); a sua resposta final é só o JSON pedido.
5. Não leia nem escreva arquivos fora da pasta atual.
6. Escreva 10 títulos diferentes, cada um com o seu texto de thumbnail, e pontue cada par com
   `python3 metodo/yt-package/title.py --json --title "<título>" --thumb "<texto da thumbnail>"`.
   Fique com o par de maior `score`.
7. O texto da thumbnail tem no máximo 3 palavras, todas diferentes das palavras do título.
8. Nunca invente números, datas, nomes ou resultados que não estejam no roteiro.
9. Varie a estrutura e as palavras em relação aos títulos recentes do canal, listados abaixo.
10. Se houver problemas de uma versão anterior, corrija os que dizem respeito ao título, à thumbnail, à descrição, às tags ou aos capítulos.

## Dados do vídeo

Roteiro (texto falado e fontes):
{roteiro}

Transcrição (o que a narração realmente disse):
{transcricao}

Tempos de cada segmento (início e fim, em segundos):
{tempos}

## Perfil de voz do canal

{perfil_voz}

## Títulos recentes do canal

{titulos_recentes}

## Problemas da versão anterior

{problemas_anteriores}

## Formato da resposta

- `titulo`: o título escolhido, com até 100 caracteres (de preferência até 60), sem `<` nem `>`.
- `texto_thumb`: o texto da thumbnail do par escolhido (até 3 palavras).
- `cena_thumb`: de 15 a 40 palavras **em inglês** descrevendo a imagem de fundo da thumbnail: uma cena concreta e chamativa que mostre a promessa do título, com o assunto à direita e espaço livre à esquerda para o texto. Sem texto escrito na imagem, sem marcas, logotipos ou pessoas reais e famosas.
- `descricao`: as duas primeiras linhas dizem o que o vídeo entrega, com as palavras das consultas-alvo; depois, um parágrafo curto. Até 4500 caracteres, sem `<` nem `>`. Não coloque links, fontes, capítulos, hashtags, créditos de imagem nem avisos de IA: as fontes do roteiro, os capítulos, os créditos, o aviso de IA e as hashtags do formato são acrescentados automaticamente.
- `tags`: até 15, só para desambiguar (grafias, siglas e nomes que as pessoas digitam), sem `#`.
- `capitulos`: deixe a lista vazia; o sistema calcula os capítulos a partir das seções do roteiro.
- `consultas_alvo`: exatamente 3 buscas que uma pessoa real digitaria e que este vídeo deve vencer. O título e as duas primeiras linhas da descrição precisam conter as palavras delas.
