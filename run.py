"""Inicia o Cortaí em http://127.0.0.1:8765"""
import os
import webbrowser

import uvicorn

if __name__ == "__main__":
    port = int(os.environ.get("CORTAI_PORT", "8765"))
    if os.environ.get("CORTAI_NO_BROWSER") != "1":
        webbrowser.open(f"http://127.0.0.1:{port}")
    uvicorn.run("cortai.server:app", host="127.0.0.1", port=port)
