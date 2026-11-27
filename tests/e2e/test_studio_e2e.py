"""
Especificação E2E com Playwright (Onda 4 — Qualidade Contínua de Navegador).
Executa sob demanda com `pytest -m e2e` quando Playwright está instalado;
ignorado automaticamente na execução rápida offline (`pytest` padrão).
"""

import threading
import time

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api", reason="Playwright não instalado neste ambiente.")
uvicorn = pytest.importorskip("uvicorn", reason="Uvicorn necessário para servidor E2E.")


@pytest.mark.e2e
def test_studio_e2e_navigation_modals_and_zero_console_errors(isolated_env):
    """Valida carregamento real do SPA V4.0, troca de abas, modais Pro/Settings e zero erros JS."""
    import server

    config = uvicorn.Config(server.app, host="127.0.0.1", port=8599, log_level="error")
    srv = uvicorn.Server(config)
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    time.sleep(0.8)

    console_errors = []
    try:
        with playwright_sync.sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.on("pageerror", lambda err: console_errors.append(str(err)))
            page.goto("http://127.0.0.1:8599/", wait_until="networkidle")

            assert "EasyKut Studio V4.0" in page.title()

            # Abre modal de Configurações e verifica saúde /api/health
            page.click("#btn-settings-modal")
            page.wait_for_selector("#cfg-status-db")
            page.click("#modal-close-btn")

            # Abre Hub Pro (Brand Kit, Trilhas, B-Roll)
            page.click("#btn-pro-hub-modal")
            page.wait_for_selector("#bk-name-input")
            page.click("#pro-hub-close-btn")

            # Troca para aba Biblioteca e volta para Estúdio
            page.click("#tab-btn-library")
            page.wait_for_selector("#library-cards-container")
            page.click("#tab-btn-studio")

            browser.close()
    finally:
        srv.should_exit = True
        t.join(timeout=3)

    assert console_errors == [], f"Erros JS no console durante E2E: {console_errors}"
