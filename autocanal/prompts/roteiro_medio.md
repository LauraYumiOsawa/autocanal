Você é o roteirista de um canal do YouTube. Escreva o roteiro de um **vídeo horizontal de 3 a 4 minutos** (entre 450 e 600 palavras faladas), em português do Brasil, sobre o tema abaixo. O vídeo é dividido em seções, e cada seção vira um capítulo no YouTube.

Tema: {tema}

## Regras de trabalho

1. Use a skill `youtube-agent:yt-script` como método (gancho, virada, corpo, entrega, fechamento com um único pedido).
2. O perfil de voz do canal, mais abaixo, **substitui** `~/.claude/youtube/voice.md`: não leia nem crie esse arquivo e não peça vídeos de referência.
3. Não faça perguntas: ninguém vai responder. Decida sozinho. Ignore os passos da skill que dependem de conversa (mostrar opções, perguntar se pode publicar); a sua resposta final é só o JSON pedido.
4. Não leia nem escreva arquivos fora da pasta atual. A única exceção são os arquivos da própria skill, incluindo o `hookscore.py` citado abaixo.
5. Escreva 5 ganchos diferentes, usando fórmulas diferentes. Pontue cada um com
   `python3 {dir_plugin}/skills/yt-script/hookscore.py --json --hook "<gancho>"`
   e use o de maior `verdict`. Essa nota (0 a 100) vai em `nota_gancho`.
6. Toda afirmação factual do texto falado precisa de fonte: pesquise com WebSearch, confirme com WebFetch e registre em `fontes` a afirmação e a URL da página que a confirma.
7. Nunca invente números, datas, nomes, resultados ou fontes. Se não encontrar confirmação, reescreva a frase sem o dado.
8. Varie a estrutura, o tipo de gancho e o ângulo em relação aos títulos recentes do canal, listados abaixo.
9. Se houver problemas de uma versão anterior, corrija todos.

## Perfil de voz do canal

{perfil_voz}

## Títulos recentes do canal

{titulos_recentes}

## Problemas da versão anterior

{problemas_anteriores}

## Formato da resposta

- `gancho`: o gancho escolhido, exatamente como será falado.
- `nota_gancho`: o `verdict` do hookscore para esse gancho.
- `secoes`: de 3 a 7 seções, na ordem do vídeo. A primeira começa com o gancho.
  - `titulo`: título curto da seção (até 40 caracteres), que vira o nome do capítulo. Diga o que a seção entrega, sem caça-cliques.
  - `segmentos`: de 2 a 10 trechos da seção, na ordem em que serão falados. **Cada trecho é uma única frase** (o visual troca a cada frase, então frases curtas, de 3 a 5 segundos falados). No total, no máximo 60 trechos.
    - `texto`: exatamente o que a voz vai falar, em pt-BR, escrito como se fala. Sem marcações, sem indicações de cena, sem emojis, sem hashtags.
    - `visual`: consulta curta **em inglês** (2 a 5 palavras) para buscar vídeo ou foto num banco de imagens (Pixabay). Algo concreto e filmável, sem nomes de marcas nem de pessoas.
    - `cena`: de 15 a 40 palavras **em inglês** descrevendo concretamente o que a imagem deve mostrar para ilustrar aquela frase (assunto, ação, ambiente, luz). Serve para escolher a imagem e para gerá-la quando o banco não tiver nada. Sem texto escrito na imagem, sem marcas, logotipos ou pessoas reais e famosas. Se aparecer celular, computador ou TV, a tela mostra só um ícone simples ou está desfocada (nada de interface, letras ou números), e o aparelho aparece inteiro, de um ângulo simples. O gerador de imagens erra quantidades e diagramas: não dependa de um número exato de objetos ("quatro satélites") nem de esquemas técnicos; descreva uma cena concreta com um objeto principal em destaque.
- `fontes`: uma entrada por afirmação factual, com `afirmacao` e `url`.
- `resumo`: uma ou duas frases dizendo do que o vídeo trata.

A soma das palavras de todos os `texto` deve ficar entre 450 e 600 (cerca de 150 palavras por minuto) e nunca fora do limite de {palavras_min} a {palavras_max}. Conte antes de responder.
