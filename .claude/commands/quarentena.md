---
description: Explica os jobs em quarentena e oferece o próximo passo
---
1. Rode `uv run autocanal status` e identifique os jobs em `quarentena`.
2. Para cada um, leia os `revisao_*.json` da pasta do job (em `dados/`), explique em português o veredito e os problemas.
3. Ofereça as opções: `autocanal aprovar <job>`, `autocanal descartar <job>` ou `autocanal revisar <job>`. Só execute uma delas se Laura escolher.
