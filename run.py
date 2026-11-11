"""Abre o Cortaí na janela própria; também é o ponto de entrada do Cortai.exe.

CORTAI_NO_BROWSER=1 sobe só o servidor em http://127.0.0.1:8765, sem janela (para testes ou
para usar pelo navegador).
"""
import os
import sys
import traceback

from cortai import paths


def main() -> None:
    if paths.FROZEN and sys.argv[1:2] == ["--run-yt-dlp"]:
        from cortai import desktop

        desktop.attach_child_output()
        desktop.hide_child_consoles()
        import yt_dlp

        yt_dlp.main(sys.argv[2:])
        return

    port = int(os.environ.get("CORTAI_PORT", "8765"))
    paths.expose_tools()
    if paths.FROZEN:  # .exe sem console: sem isto, qualquer print/log derruba o app
        from cortai import desktop

        log = desktop.log_to_file()
        desktop.hide_child_consoles()
    if os.environ.get("CORTAI_NO_BROWSER") == "1":
        import uvicorn

        from cortai.server import app

        uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning" if paths.FROZEN else "info")
        return

    from cortai import desktop

    if not paths.FROZEN:
        desktop.run_app(port)
        return
    try:
        desktop.run_app(port)
    except Exception as exc:
        traceback.print_exc()
        desktop.message(f"O Cortaí não conseguiu iniciar:\n\n{exc}\n\nDetalhes em {log}")


if __name__ == "__main__":
    main()
