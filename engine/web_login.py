"""Login REAL no instagram.com via navegador (Playwright).

Por que existir: a API privada do instagrapi devolve `bad_password` genérico
quando o Instagram não confia no login (IP/dispositivo), mesmo com a senha
certa, e não distingue os casos. O login web é a fonte da verdade — ele diz
claramente se é senha errada, 2FA ou checkpoint — e, no sucesso, nos dá o
cookie `sessionid`, que importamos no instagrapi (login_by_sessionid).

Fluxo para o usuário: ele só digita usuário e senha. Se cair em 2FA, o app
mostra o campo de código (o mesmo card que já existe) e nós o consumimos aqui.

Uso principal (ver apps/instagram/tasks.py::web_login_account):
    result = perform_web_login(username, password, proxy_url,
                               on_twofa=cb, code_getter=getter)
    # result = {"status": "...", "sessionid": "...", "message": "..."}

status possíveis:
    success        -> sessionid preenchido
    bad_password   -> usuário/senha incorretos (fonte da verdade)
    twofa_required -> caiu em 2FA e não recebemos código a tempo
    checkpoint     -> Instagram pediu verificação extra (email/dispositivo)
    error          -> falha técnica (timeout, selkector, etc.)
"""
import re
import time
from urllib.parse import urlparse

LOGIN_URL = "https://www.instagram.com/accounts/login/"
# Quanto esperar, no total, o resultado do POST de login aparecer.
RESULT_TIMEOUT_S = 30
# Quanto esperar o usuário digitar o código (2FA do app ou código de e-mail/SMS
# do checkpoint). E-mail costuma demorar, por isso é generoso. IMPORTANTE: a
# task que chama isto precisa de time_limit maior que este valor (ver tasks.py).
CODE_WAIT_S = 300
# Alias retrocompatível.
TWOFA_WAIT_S = CODE_WAIT_S
# Campos de código nas telas de 2FA e de checkpoint (cobre variantes do IG).
CODE_INPUT_SEL = (
    'input[name="verificationCode"], input[name="security_code"], '
    'input[autocomplete="one-time-code"], input[inputmode="numeric"], '
    'input[maxlength="6"][type="tel"], input[maxlength="8"][type="tel"]'
)


def _parse_proxy(proxy_url):
    """Converte 'http://user:pass@host:port' no dict que o Playwright espera."""
    if not proxy_url:
        return None
    p = urlparse(proxy_url if "://" in proxy_url else "http://" + proxy_url)
    proxy = {"server": f"{p.scheme}://{p.hostname}:{p.port}"}
    if p.username:
        proxy["username"] = p.username
    if p.password:
        proxy["password"] = p.password
    return proxy


def _get_sessionid(context):
    for c in context.cookies("https://www.instagram.com"):
        if c.get("name") == "sessionid" and c.get("value"):
            return c["value"]
    return None


def perform_web_login(username, password, proxy_url=None,
                      on_code_needed=None, code_getter=None, screenshot_path=None):
    """Executa o login no instagram.com e classifica o resultado.

    on_code_needed: callable(kind) chamado quando o IG pede um código, onde
                    kind é 'twofa' (app/SMS) ou 'checkpoint' (e-mail/dispositivo).
                    Serve para marcar a conta ('2fa_required'/'challenge_required')
                    e a UI mostrar o campo de código.
    code_getter:    callable() que BLOQUEIA aguardando o código digitado pelo
                    usuário (retorna o código em str, ou None se desistir/timeout).

    O navegador permanece VIVO durante toda a espera do código, então digitamos
    o código na mesma sessão que o IG apresentou a tela — igual a um humano faria.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"status": "error", "sessionid": None,
                "message": "Playwright não instalado no servidor."}

    proxy = _parse_proxy(proxy_url)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
            proxy=proxy,
        )
        context = browser.new_context(
            locale="pt-BR",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )
        page = context.new_page()
        try:
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)

            # Aceita o banner de cookies se aparecer (não bloqueia se não existir).
            for label in ("Permitir todos os cookies", "Allow all cookies",
                          "Aceitar", "Only allow essential cookies"):
                try:
                    btn = page.get_by_role("button", name=label)
                    if btn.count():
                        btn.first.click(timeout=2000)
                        break
                except Exception:
                    pass

            # O Instagram serve duas variantes do form: a do app JS
            # (username/password + <button>) e uma leve (email/pass + <input submit>).
            # Seletores combinados cobrem ambas.
            page.fill('input[name="username"], input[name="email"]', username, timeout=15000)
            pass_sel = 'input[name="password"], input[name="pass"]'
            page.fill(pass_sel, password, timeout=15000)
            # Submeter via Enter é o mais confiável: o <button>/<input submit>
            # fica desabilitado/invisível em uma das variantes do form.
            page.press(pass_sel, "Enter")
            try:
                page.click('button[type="submit"], input[type="submit"]', timeout=2000)
            except Exception:
                pass

            outcome = _await_outcome(page, context)

            # 2FA ou checkpoint: o IG pediu um código. Mantemos o navegador vivo,
            # avisamos a UI, esperamos o usuário digitar e submetemos na hora.
            if outcome in ("twofa", "checkpoint"):
                kind = outcome
                if kind == "checkpoint":
                    # Alguns checkpoints têm um passo "enviar código" antes do input.
                    _advance_checkpoint(page)
                if on_code_needed:
                    try:
                        on_code_needed(kind)
                    except Exception:
                        pass
                code = code_getter() if code_getter else None
                if not code:
                    status = "twofa_required" if kind == "twofa" else "checkpoint"
                    onde = "pelo app/SMS" if kind == "twofa" else "no seu e-mail/SMS"
                    return {"status": status, "sessionid": None,
                            "message": f"Código necessário: informe o código enviado {onde}."}
                return _submit_code(page, context, code, kind, screenshot_path)

            if outcome == "success":
                sid = _get_sessionid(context)
                if sid:
                    return {"status": "success", "sessionid": sid,
                            "message": "Login realizado com sucesso."}
                return {"status": "error", "sessionid": None,
                        "message": "Login aparentou sucesso mas o sessionid não foi encontrado."}

            if outcome == "bad_password":
                return {"status": "bad_password", "sessionid": None,
                        "message": "Usuário ou senha incorretos."}

            if outcome == "rate_limited":
                return {"status": "error", "sessionid": None,
                        "message": "O Instagram pediu para aguardar alguns minutos "
                                   "antes de tentar de novo (limite temporário)."}

            if screenshot_path:
                try:
                    page.screenshot(path=screenshot_path)
                except Exception:
                    pass
            return {"status": "error", "sessionid": None,
                    "message": "Não foi possível determinar o resultado do login (timeout)."}

        except Exception as e:
            if screenshot_path:
                try:
                    page.screenshot(path=screenshot_path)
                except Exception:
                    pass
            return {"status": "error", "sessionid": None,
                    "message": f"Falha técnica no login web: {type(e).__name__}: {e}"}
        finally:
            try:
                context.close()
                browser.close()
            except Exception:
                pass


def _await_outcome(page, context):
    """Faz polling até identificar sucesso / senha errada / 2FA / checkpoint."""
    deadline = time.time() + RESULT_TIMEOUT_S
    while time.time() < deadline:
        # 1) Sucesso: cookie sessionid presente.
        if _get_sessionid(context):
            return "success"

        url = page.url or ""
        try:
            body = (page.locator("body").inner_text(timeout=1000) or "").lower()
        except Exception:
            body = ""

        # 2) 2FA (verificação em duas etapas do próprio usuário).
        if "two_factor" in url or "two_step" in url:
            return "twofa"
        try:
            if page.locator(CODE_INPUT_SEL).count() and (
                "autentic" in body or "two-factor" in body or "duas etapas" in body
                or "código de segurança" in body or "security code" in body
            ):
                return "twofa"
        except Exception:
            pass

        # 3) Checkpoint / challenge (IG não confia no login → código por email/SMS).
        if "/challenge/" in url or "/auth_platform/codeentry" in url:
            return "checkpoint"
        if ("enviamos um código" in body or "we sent a code" in body
                or "confirme que é você" in body or "help us confirm" in body
                or "manter sua conta segura" in body):
            return "checkpoint"

        # 4) Senha/usuário incorretos (texto de erro).
        if "incorret" in body or "not match our records" in body \
                or "senha estava incorreta" in body:
            return "bad_password"
        if "problema com a sua solicitação" in body or "try again later" in body \
                or "aguarde alguns minutos" in body or "please wait a few minutes" in body:
            return "rate_limited"

        page.wait_for_timeout(700)
    return "timeout"


def _advance_checkpoint(page):
    """Numa tela de checkpoint, avança até o campo de código.

    O IG às vezes mostra antes um passo tipo 'Foi você?' / escolher email ou SMS
    / 'Enviar código'. Clicamos no botão de continuar se ainda não há input de
    código. Best-effort: se a tela já tem o campo, não faz nada.
    """
    for _ in range(3):
        try:
            if page.locator(CODE_INPUT_SEL).count():
                return
        except Exception:
            pass
        clicked = False
        for label in ("Enviar código", "Enviar Código", "Send code", "Send Code",
                      "Continuar", "Continue", "Fui eu", "This was me", "Confirmar"):
            try:
                btn = page.get_by_role("button", name=label)
                if btn.count():
                    btn.first.click(timeout=3000)
                    clicked = True
                    break
            except Exception:
                pass
        if not clicked:
            return
        page.wait_for_timeout(1500)


def _submit_code(page, context, code, kind, screenshot_path=None):
    """Digita o código (2FA ou checkpoint) na tela viva e confirma."""
    status_required = "twofa_required" if kind == "twofa" else "checkpoint"
    code = re.sub(r"\D", "", str(code))
    try:
        page.fill(CODE_INPUT_SEL, code, timeout=15000)
        # Botão de confirmar, senão Enter.
        submitted = False
        for label in ("Confirmar", "Continuar", "Confirm", "Next", "Enviar", "Submit"):
            try:
                btn = page.get_by_role("button", name=label)
                if btn.count():
                    btn.first.click(timeout=3000)
                    submitted = True
                    break
            except Exception:
                pass
        if not submitted:
            page.keyboard.press("Enter")

        deadline = time.time() + 25
        while time.time() < deadline:
            sid = _get_sessionid(context)
            if sid:
                return {"status": "success", "sessionid": sid,
                        "message": "Login realizado com sucesso."}
            body = ""
            try:
                body = (page.locator("body").inner_text(timeout=1000) or "").lower()
            except Exception:
                pass
            if "incorret" in body or "inválido" in body or "invalid" in body \
                    or "não é o código certo" in body or "check the code" in body:
                return {"status": status_required, "sessionid": None,
                        "message": "Código inválido. Tente novamente com um código novo."}
            page.wait_for_timeout(700)

        return {"status": status_required, "sessionid": None,
                "message": "Não deu para confirmar o código a tempo. Tente novamente."}
    except Exception as e:
        if screenshot_path:
            try:
                page.screenshot(path=screenshot_path)
            except Exception:
                pass
        return {"status": "error", "sessionid": None,
                "message": f"Falha ao enviar o código: {type(e).__name__}: {e}"}
