# Retomar: adaptador do Codex (subprojeto 1, Tasks 7 a 10)

Pausado em 2026-10-26 a pedido de Laura, que vai fazer o login do Codex depois.

## Onde parou

- Branch `feat/ia-neutra` (ainda não mesclada no `main`, sem push).
- Plano: `docs/superpowers/plans/2026-10-26-ia-neutra.md`. Spec: `docs/superpowers/specs/2026-10-26-ia-neutra-design.md`.
- **Concluídas (Tasks 1 a 6):**
  - núcleo `autocanal/ia/` (pedido, erros e cofre);
  - schema estrito;
  - adaptador do Claude;
  - troca das etapas para `ia.chamar`: config `ia:`, pausa por provedor e tabela `chamadas_ia`, com o histórico migrado;
  - prompts sem dialeto do Claude, com os métodos copiados para `<pasta>/metodo/`;
  - consumo e diagnóstico por provedor.

  São 6 commits, de `c0e0de5` a `f2c20cd`, e 440 testes passando.
- **Faltam:**
  - Task 7: sondagem do Codex, com resultado registrado na spec §2.1;
  - Task 8: adaptador `ia/codex_cli.py`;
  - Task 9: testes `real` (prova do cofre e chamada mínima);
  - Task 10: os dois shorts de validação, sem publicar;
  - depois disso, a revisão final da branch inteira.
- O ledger com as decisões tomadas fica em `.superpowers/sdd/2026-10-26-ia-neutra/progress.md`. Ele está no `.gitignore`, então só existe nesta máquina.

## Por que parou

- O `codex` que estava instalado era um snap de terceiros (publicador `jcat (jcat-nysasounds)`, não a OpenAI), na versão 0.114.0. A proteção de leitura proibida, exigida pelo cofre, parece ter chegado só por volta da 0.125.
- Em 2026-10-26 foi instalado o oficial: `@openai/codex@0.164.0` (repositório `github.com/openai/codex`), em `~/.local/bin/codex`. Ele vem antes de `/snap/bin` no PATH.
- O login atual (`~/.codex/auth.json`) foi criado pelo snap de terceiros.

## O que Laura faz antes de retomar

1. Remover o snap de terceiros: `sudo snap remove codex`.
2. Apagar o login antigo: `~/.local/bin/codex logout`.
3. Por precaução, encerrar as sessões antigas do Codex nas configurações de segurança da conta do ChatGPT.
4. Entrar com o oficial: `codex login`.
5. Conferir: `command -v codex` deve mostrar `/home/losawa/.local/bin/codex`, e `codex --version` deve ser 0.164.0 ou mais nova.

## Frase para retomar

> Retome o subprojeto 1 (núcleo de IA neutro) a partir da Task 7 do plano
> `docs/superpowers/plans/2026-10-26-ia-neutra.md`. Leia antes
> `docs/superpowers/plans/2026-10-26-ia-neutra-retomar.md`. O Codex oficial já está logado.

## Lembretes para quem retomar

- Retomar com a skill `superpowers:executing-plans`, a partir do ledger: as Tasks 1 a 6 já têm a linha `complete`.
- A sondagem roda numa pasta do scratchpad, nunca no projeto, e nunca lê `~/.codex/auth.json`.
- Se a leitura proibida não funcionar na versão oficial, o adaptador não é entregue (spec §2), e o desenho volta para a Laura.
- Os testes usam `/home/losawa/.local/share/autocanal-tools/bin/uv run pytest -q` (o `uv` não está no PATH).
- O que veio depois desta pausa (por exemplo, a economia de tokens) pode ter mexido nos mesmos arquivos: antes da Task 8, rodar a suíte e conferir o `git log`.
