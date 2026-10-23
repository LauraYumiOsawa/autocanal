---
description: Cria um job (e opcionalmente produz) - /novo-video <canal> <formato> <tema>
argument-hint: <canal> <formato> <tema>
---
Argumentos: $ARGUMENTS (canal, formato, tema).
1. Rode `uv run autocanal novo <canal> <formato> "<tema>"` e informe o id do job e o horário.
2. Só rode `uv run autocanal produzir <job>` se Laura pediu para produzir agora; depois informe o estado final.
Nunca rode `publicar`, `vetar`, `auth` nem `instalar-timer --ativar`.
