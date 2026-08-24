"""تجهيزات E2E (pytest-playwright) — خادم اختبار معزول + حارس شبكة + إعداد المتصفح.

القيود: عزل .env/allowlist · كل context عبر plugin · فرض الثيم · متصفح عبر متغير اختياري ·
fail-closed شبكة على كل context (عقد صارم عبر is_allowed_url) · تنظيف موارد مؤقتة مضمون.
"""

import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

import pytest

from e2e._server_boot import is_allowed_url

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BOOT = os.path.join(_REPO_ROOT, "e2e", "_server_boot.py")
_ALLOWED_HOST = "127.0.0.1"

# import-guard صريح: لو Playwright غير متاح يفشل التجميع برسالة واضحة (لا تخطٍّ صامت).
try:
    import playwright  # noqa: F401
except Exception as exc:  # pragma: no cover
    raise RuntimeError(
        f"Playwright غير مثبّت — اختبارات E2E تتطلبه؛ ثبّت requirements-e2e.txt. (السبب: {exc})"
    )


def _no_proxy_opener():
    """opener يتجاوز إعدادات proxy في البيئة (health محلي على 127.0.0.1)."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _read_until_marker(proc, marker, timeout, sink):
    """يقرأ stdout الخادم (خيط) حتى marker المنفذ أو المهلة؛ يجمع كل المخرجات في sink."""
    port_box = {}

    def _reader():
        for line in iter(proc.stdout.readline, ""):
            sink.append(line)
            if line.startswith(marker) and "port" not in port_box:
                try:
                    port_box["port"] = int(line[len(marker):].strip())
                except ValueError:
                    pass

    t = threading.Thread(target=_reader, daemon=True)
    t.start()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if "port" in port_box:
            return port_box["port"], t
        if proc.poll() is not None:  # مات مبكراً
            break
        time.sleep(0.05)
    return None, t


def _wait_health(base_url, timeout, sink):
    """انتظار صحي على /health حتى 200 (بلا sleep ثابت)؛ عند الفشل يُرفق stdout/stderr المجمّع."""
    opener = _no_proxy_opener()
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            with opener.open(base_url + "/health", timeout=2) as r:
                if r.status == 200:
                    return
        except Exception as e:  # noqa: BLE001
            last = e
        time.sleep(0.1)
    raise RuntimeError(
        f"تعذّر إقلاع /health خلال {timeout}s (آخر خطأ: {last})\n--- مخرجات الخادم ---\n"
        + "".join(sink)
    )


@pytest.fixture(scope="session")
def live_server():
    """يشغّل خادم Flask في عملية مستقلة ببيئة allowlist، بمجلد مؤقت تملكه الأم ويُنظَّف."""
    with tempfile.TemporaryDirectory(prefix="e2e_") as tmp:
        db_path = os.path.join(tmp, "e2e.db")
        env = {  # allowlist صريح — لا نسخة كاملة من os.environ
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": _REPO_ROOT,
            "HOME": tmp,
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "APP_PASSWORD": "",
            "E2E_DB_PATH": db_path,
        }
        proc = subprocess.Popen(
            [sys.executable, _BOOT], env=env, cwd=tmp,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
        )
        sink = []
        reader = None
        try:
            port, reader = _read_until_marker(proc, "E2E_PORT=", timeout=30, sink=sink)
            if port is None:
                raise RuntimeError("لم يُطبع E2E_PORT — فشل إقلاع الخادم:\n" + "".join(sink))
            base_url = f"http://{_ALLOWED_HOST}:{port}"
            _wait_health(base_url, timeout=20, sink=sink)
            yield {"url": base_url, "port": port}
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            if reader is not None:
                reader.join(timeout=5)


@pytest.fixture(scope="session")
def server_url(live_server):
    return live_server["url"]


@pytest.fixture(scope="session")
def server_port(live_server):
    return live_server["port"]


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args):
    """الخيار D: متصفح محلي عبر متغير اختياري — لا مسار ثابت، لا fallback صامت."""
    exe = os.environ.get("E2E_CHROMIUM_EXECUTABLE")
    if exe:
        if not (os.path.isfile(exe) and os.access(exe, os.X_OK)):
            raise RuntimeError(f"E2E_CHROMIUM_EXECUTABLE ليس ملفاً تنفيذياً صالحاً: {exe!r}")
        return {**browser_type_launch_args, "executable_path": exe}
    return browser_type_launch_args  # المتصفح الافتراضي المطابق لـPlaywright (المسار الرسمي)


@pytest.fixture(autouse=True)
def _network_fail_closed(context, server_port):
    """حارس شبكة fail-closed بعقد صارم على كل Browser Context (context الافتراضي للـplugin)."""
    violations = []

    def _route(route):
        url = route.request.url
        if is_allowed_url(url, _ALLOWED_HOST, server_port):
            route.continue_()
        else:
            violations.append(url)
            route.abort()

    context.route("**/*", _route)
    yield
    assert not violations, f"طلبات شبكة خارجية غير مسموحة: {violations}"
