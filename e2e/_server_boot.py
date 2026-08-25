"""إقلاع خادم اختبار معزول للـE2E (يُشغَّل كعملية مستقلة عبر subprocess).

يطبّق العزل قبل أول استيراد لـ`app`:
- تعطيل dotenv.load_dotenv (متغير PYTHON_DOTENV_DISABLED غير فعّال في dotenv 1.0.1 —
  أُثبت تجريبياً؛ فنعطّل الدالة نفسها) ⇒ منع تسريب أسرار .env.
- APP_PASSWORD="" صراحةً (المنصة مفتوحة بلا أسرار) + DATABASE_URL من قاعدة تملكها العملية
  الأم (تُمرَّر عبر E2E_DB_PATH) فلا تبقى ملفات مؤقتة بلا تنظيف.
- تعطيل المجدول (init_scheduler) كي لا تبدأ مهام خلفية.
- عزل كل عميل خارجي بالاسم الحقيقي: fmp/news/finnhub/edgar + telegram_client.send_message.
- حارس socket fail-closed يمنع أي اتصال/DNS غير loopback (connect/connect_ex/
  create_connection/getaddrinfo)، يُركّب **قبل استيراد وحدات الخدمات**، وتحقّقه
  الصارم عبر `ipaddress.ip_address(...).is_loopback` (لا prefix نصي).
ثم يبذر بيانات حتمية (AAPL=high · MSFT=missing+caps+عامل جوهري تحت النصف · NVDA=unavailable
بلا لقطة) ويشغّل werkzeug على 127.0.0.1 بمنفذ يخصّصه النظام (0)، ويطبع «E2E_PORT=<port>».

لا يلمس .env الحقيقي، ولا يعدّل أي كود إنتاج (كله إسناد وقت التشغيل داخل هذه العملية).
"""

import ipaddress
import json
import os
import socket
import sys
from datetime import date
from urllib.parse import urlsplit


def is_loopback_host(host):
    """عقد صارم لعناوين loopback (نقي، قابل للاختبار):

    يقبل: 127.0.0.0/8 (منها 127.0.0.1) · ::1 · «localhost» بالاسم الصريح.
    يرفض: 0.0.0.0 · 127.evil.com · 127.0.0.1.evil.com · مضيفاً خارجياً · «» · None.
    القيمة غير الصالحة ⇒ False (لا استثناء غير مضبوط).
    """
    if host == "localhost":
        return True
    if not host:  # "" أو None
        return False
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def is_allowed_url(url, host, port):
    """عقد السماح للحارس الشبكي (fail-closed): http + hostname == host + port == port؛
    وschemes خاملة (about:/data:/blob:). يمسك أخطاء التحليل/المنفذ ويعيد False بدل الرمي.

    يرفض: مضيفاً خارجياً، مضيفاً مشابهاً نصياً، حيلة userinfo (…@evil ⇒ hostname=evil)،
    منفذاً مختلفاً، «localhost» (العقد 127.0.0.1)، بروتوكولاً مختلفاً، ومنفذاً/URL malformed.
    """
    if url.startswith(("about:", "data:", "blob:")):
        return True
    try:
        parts = urlsplit(url)
        return parts.scheme == "http" and parts.hostname == host and parts.port == port
    except ValueError:
        return False


def _harden_env():
    """تصليب البيئة قبل استيراد app: قاعدة تملكها الأم + منصة مفتوحة + تعطيل dotenv."""
    dbp = os.environ.get("E2E_DB_PATH")
    if not dbp:
        raise RuntimeError("E2E_DB_PATH غير مُمرَّر — العملية الأم تملك القاعدة المؤقتة")
    os.environ["DATABASE_URL"] = f"sqlite:///{dbp}"
    os.environ["APP_PASSWORD"] = ""
    import dotenv
    dotenv.load_dotenv = lambda *a, **k: None


def _isolate_external_clients():
    """عزل كل عميل شبكة خارجي بالاسم الحقيقي (قبل استيراد app)."""
    from services import fmp_client, news_client, finnhub_client, edgar_client, telegram_client
    fmp_client.get_quote = lambda *a, **k: None
    fmp_client.get_profile = lambda *a, **k: None
    fmp_client.get_financials = lambda *a, **k: {"income": None, "balance": None, "cashflow": None}
    fmp_client.get_historical_prices = lambda *a, **k: None
    fmp_client.get_earnings_calendar = lambda *a, **k: None
    fmp_client.get_shares_float_all = lambda *a, **k: None
    news_client.get_market_news = lambda *a, **k: []
    finnhub_client.get_quote = lambda *a, **k: None
    edgar_client.get_insider_transactions = lambda *a, **k: []
    telegram_client.send_message = lambda *a, **k: None  # دالة الإرسال الحقيقية (تُعطَّل)


def _disable_scheduler():
    import services.scheduler as _sched
    _sched.init_scheduler = lambda *a, **k: None


def _install_socket_guard():
    """حارس fail-closed: يمنع أي اتصال/DNS غير loopback قبل حدوثه (تحقق صارم بـipaddress)."""
    def _host_ok(host):
        return is_loopback_host(host)

    def _addr_host(address):
        return address[0] if isinstance(address, (tuple, list)) else address

    _orig_connect = socket.socket.connect
    _orig_connect_ex = socket.socket.connect_ex
    _orig_create = socket.create_connection
    _orig_gai = socket.getaddrinfo

    def _connect(self, address):
        if not _host_ok(_addr_host(address)):
            raise RuntimeError(f"E2E socket guard: blocked connect to {address!r}")
        return _orig_connect(self, address)

    def _connect_ex(self, address):
        if not _host_ok(_addr_host(address)):
            raise RuntimeError(f"E2E socket guard: blocked connect_ex to {address!r}")
        return _orig_connect_ex(self, address)

    def _create_connection(address, *a, **k):
        if not _host_ok(_addr_host(address)):
            raise RuntimeError(f"E2E socket guard: blocked create_connection to {address!r}")
        return _orig_create(address, *a, **k)

    def _getaddrinfo(host, *a, **k):
        if not _host_ok(host):
            raise RuntimeError(f"E2E socket guard: blocked DNS getaddrinfo for {host!r}")
        return _orig_gai(host, *a, **k)

    socket.socket.connect = _connect
    socket.socket.connect_ex = _connect_ex
    socket.create_connection = _create_connection
    socket.getaddrinfo = _getaddrinfo


# ═══════════ بذر البيانات الحتمية ═══════════
RUN = date(2026, 8, 21)  # جلسة ثابتة تُحقن (as_of = snap_date)


def _tech(keys):
    return [{"label": k, "value": "x", "status": "bull"} for k in keys]


def _conf_record(keys, **over):
    r = {
        "catalyst": 72, "catalyst_complete": True,
        "piotroski_computable": 9, "indicators": _tech(keys),
        "structure": {"trend": "up", "status": "bull"},
        "frames": {"weekly": "up", "monthly": "up"},
        "money_flow": {"score": 70.0, "status": "bull"},
        "analysis_date": "2026-08-21", "analysis_close": 100.0,
    }
    r.update(over)
    return r


def _report(ticker, name, sector="Technology"):
    return {
        "ticker": ticker, "name": name, "sector": sector,
        "price": 123.45, "change": 1.2, "change_percent": 0.98, "analysis_price": 123.45,
        "piotroski": {"score": 6, "computable": 9, "components": []},
        "catalyst": {"score": 70, "complete": True, "components": []},
        "indicators": [{"label": "MACD", "status": "bull", "value": "x"},
                       {"label": "RSI", "status": "neutral", "value": "x"}],
        "metrics": {"gross_margin": 40, "op_margin": 25, "pe": 20, "peg": 1.2, "roa": 10, "roe": 30},
        "break_status": None, "sustained": None, "reversal": None, "insider_trades": [],
        "fibonacci": None, "volume_profile": None, "atr_plan": None, "chart": None,
    }


def _scanner_record(ticker, name, sector="Technology"):
    return {
        "ticker": ticker, "name": name, "sector": sector,
        "price": 123.45, "analysis_price": 123.45, "change_percent": 1.2,
        "catalyst": 72, "catalyst_complete": True,
        "piotroski": 6, "piotroski_computable": 9,
        "indicators": [{"label": "MACD", "status": "bull", "value": "x"},
                       {"label": "RSI", "status": "bull", "value": "x"}],
        "money_flow": {"score": 70.0, "status": "bull"},
        "structure": {"trend": "up", "status": "bull"},
    }


def _seed(app):
    from models import db, StockCache, StockSnapshot
    from services.confidence import data_confidence, CONFIDENCE_TECHNICAL_INDICATOR_KEYS
    from datetime import datetime, timezone
    keys = CONFIDENCE_TECHNICAL_INDICATOR_KEYS

    high_dc = data_confidence(_conf_record(keys), RUN)  # كامل ⇒ high، بلا missing/caps
    # frames/flow فارغة ⇒ missing؛ piotroski_computable=0 ⇒ عامل جوهري تحت النصف (dc-below-half)؛
    # analysis_date أقدم من RUN ⇒ سقف medium. (كله عبر نواة data_confidence الحقيقية.)
    miss_dc = data_confidence(
        _conf_record(keys, piotroski_computable=0, frames={}, money_flow={}, analysis_date="2026-08-10"), RUN)

    now = datetime.now(timezone.utc)
    with app.app_context():
        db.session.merge(StockCache(ticker="screen:AAPL",
                                    data_json=json.dumps(_scanner_record("AAPL", "Apple Inc"), ensure_ascii=False),
                                    updated_at=now))
        db.session.merge(StockCache(ticker="report:AAPL",
                                    data_json=json.dumps(_report("AAPL", "Apple Inc"), ensure_ascii=False),
                                    updated_at=now))
        db.session.merge(StockCache(ticker="report:MSFT",
                                    data_json=json.dumps(_report("MSFT", "Microsoft Corp"), ensure_ascii=False),
                                    updated_at=now))
        db.session.merge(StockCache(ticker="report:NVDA",
                                    data_json=json.dumps(_report("NVDA", "NVIDIA Corp"), ensure_ascii=False),
                                    updated_at=now))
        # AAPL=high · MSFT=missing+caps+below-half · NVDA بلا لقطة ⇒ conf-na (لا نخزّن view-model).
        db.session.merge(StockSnapshot(ticker="AAPL", snap_date=RUN,
                                       extra_json=json.dumps({"data_confidence": high_dc}, ensure_ascii=False)))
        db.session.merge(StockSnapshot(ticker="MSFT", snap_date=RUN,
                                       extra_json=json.dumps({"data_confidence": miss_dc}, ensure_ascii=False)))
        db.session.commit()


def main():
    # الترتيب مقصود: تركيب حارس socket قبل أي استيراد لوحدات services.* أو app.
    _harden_env()
    _install_socket_guard()
    _isolate_external_clients()
    _disable_scheduler()

    from app import app  # يُنشئ الجداول على القاعدة المؤقتة
    _seed(app)

    from werkzeug.serving import make_server
    server = make_server("127.0.0.1", 0, app, threaded=True)
    print(f"E2E_PORT={server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
