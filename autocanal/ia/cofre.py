"""Caminhos que nenhuma IA headless pode ler nem alterar (segredos e dados pessoais)."""
from __future__ import annotations

from pathlib import Path

# Credenciais e dados pessoais fora do projeto que a IA headless nunca precisa ler.
PASTAS_PESSOAIS = (".claude", ".codex", ".ssh", ".config", ".aws", ".gnupg", ".docker", ".kube",
                   ".local/share", "Downloads", "Documents", "Desktop")
ARQUIVOS_PESSOAIS = (".netrc", ".git-credentials", ".bash_history")


def caminhos_proibidos(raiz: Path) -> list[str]:
    """Caminhos absolutos (com globs) proibidos; cada adaptador traduz para o seu provedor."""
    raiz = Path(raiz).resolve()
    casa = Path.home()
    return [
        f"{raiz}/dados/tokens/**",
        f"{raiz}/config/**",
        f"{raiz}/dados/*.db",
        *(f"{casa}/{pasta}/**" for pasta in PASTAS_PESSOAIS),
        *(f"{casa}/{arquivo}" for arquivo in ARQUIVOS_PESSOAIS),
    ]
