"""مصفوفة E2E الرسمية لواجهة ثقة البيانات (Playwright) — تكمّل tests/test_phase6_ui_wiring.py.

تغطّي ما يحتاج متصفحاً حقيقياً: تفاعل التلميح (hover/tap + منع الانتقال + مطابقة النص)،
إظهار/إخفاء score بالـcomputed style، الثيمين المحسوبين، اتجاه LTR المحسوب، عدم overflow
أفقي للصفحة كاملة، والتباين المحسوب للأزواج المقفلة (warning + critical). ومعها اختبارات
انحدار لعقد السماح الشبكي وعزل .env.

البيانات مبذورة في e2e/_server_boot.py: AAPL=high · MSFT=missing+caps+عامل جوهري تحت النصف ·
NVDA=unavailable. الثيم يُفرض بـhtml.light (لا مُفعِّل runtime في المشروع) ويُثبَت بالـclass.
"""

import os
import subprocess
import sys
import tempfile

import pytest
from playwright.sync_api import expect

from e2e._server_boot import is_allowed_url, is_loopback_host

DESKTOP = dict(viewport={"width": 1280, "height": 900}, has_touch=False, is_mobile=False)
MOBILE = dict(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)

TIP = ".nav-tip"
TIP_SHOW = ".nav-tip.show"


# ═══════════ أدوات ═══════════
def _open(page, base_url, path, theme="dark"):
    op = "add" if theme == "light" else "remove"
    page.add_init_script(f"try{{document.documentElement.classList.{op}('light')}}catch(e){{}}")
    page.goto(base_url + path, wait_until="load")
    page.evaluate(f"document.documentElement.classList.{op}('light')")
    got = page.evaluate("document.documentElement.classList.contains('light')")
    assert got == (theme == "light"), f"إثبات الثيم فشل: طُلب {theme} وclassList.light={got}"
    return page


def _parse_rgb(s):
    return tuple(float(x) for x in s[s.find("(") + 1:s.find(")")].split(",")[:3])


def _rel_lum(rgb):
    def _c(v):
        v /= 255.0
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (_c(x) for x in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(fg, bg):
    l1, l2 = _rel_lum(fg), _rel_lum(bg)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


# JS: لون النص واللون الخلفي الفعلي بتركيب alpha (source-over) لكل خلفيات الأسلاف.
_BG_JS = """(sel) => {
  const el = document.querySelector(sel);
  if (!el) return null;
  const fg = getComputedStyle(el).color;
  const parse = c => { const m = (c.match(/[\\d.]+/g) || []).map(Number);
    return m.length >= 3 ? [m[0], m[1], m[2], m.length >= 4 ? m[3] : 1] : null; };
  const layers = [];
  for (let n = el; n; n = n.parentElement) layers.push(getComputedStyle(n).backgroundColor);
  let cur = [255, 255, 255];
  for (const c of layers.slice().reverse()) {
    const p = parse(c); if (!p || p[3] === 0) continue;
    const a = p[3];
    cur = [p[0]*a + cur[0]*(1-a), p[1]*a + cur[1]*(1-a), p[2]*a + cur[2]*(1-a)];
  }
  return {fg, bg: `rgb(${Math.round(cur[0])}, ${Math.round(cur[1])}, ${Math.round(cur[2])})`};
}"""


def _contrast_of(page, sel):
    pair = page.evaluate(_BG_JS, sel)
    assert pair is not None, f"العنصر غير موجود للتباين: {sel}"
    ratio = _contrast(_parse_rgb(pair["fg"]), _parse_rgb(pair["bg"]))
    return ratio, pair


# ═══════════ الشارة: hover/tap (مطابقة نص data-tip) ═══════════
@pytest.mark.browser_context_args(**DESKTOP)
def test_badge_hover_shows_tip_text_and_mouseleave_hides(page, server_url):
    _open(page, server_url, "/", "dark")
    badge = page.locator(".conf-badge").first
    expect(badge).to_be_visible()
    tip_text = badge.get_attribute("data-tip")
    assert tip_text, "الشارة بلا data-tip"
    page.hover(".conf-badge")
    expect(page.locator(TIP_SHOW)).to_have_count(1)
    assert page.eval_on_selector(TIP, "e => e.textContent") == tip_text, "نص التلميح لا يطابق data-tip"
    page.mouse.move(3, 3)  # مغادرة ⇒ pointerleave
    expect(page.locator(TIP_SHOW)).to_have_count(0)


@pytest.mark.browser_context_args(**MOBILE)
def test_badge_tap_toggle_text_and_no_navigation(page, server_url):
    _open(page, server_url, "/", "dark")
    badge = page.locator(".conf-badge").first
    tip_text = badge.get_attribute("data-tip")
    url_before = page.url
    expect(page.locator(TIP_SHOW)).to_have_count(0)  # لا hover باللمس قبل النقر
    page.tap(".conf-badge")
    expect(page.locator(TIP_SHOW)).to_have_count(1)
    assert page.eval_on_selector(TIP, "e => e.textContent") == tip_text, "أول tap: نص التلميح لا يطابق"
    assert page.url == url_before, "أول tap غيّر الـURL (لم يُمنع انتقال البطاقة)"
    page.tap(".conf-badge")
    expect(page.locator(TIP_SHOW)).to_have_count(0)
    assert page.url == url_before, "ثاني tap غيّر الـURL"


# ═══════════ score: computed display على .conf-badge .conf-score ═══════════
@pytest.mark.browser_context_args(**MOBILE)
def test_score_hidden_on_mobile_computed(page, server_url):
    _open(page, server_url, "/", "dark")
    assert page.locator(".conf-badge .conf-score").count() >= 1, "لا .conf-score داخل الشارة"
    disp = page.eval_on_selector(".conf-badge .conf-score", "e => getComputedStyle(e).display")
    assert disp == "none", f"score يجب أن يكون مخفياً على الجوال (display={disp})"


@pytest.mark.browser_context_args(**DESKTOP)
def test_score_visible_on_desktop_computed(page, server_url):
    _open(page, server_url, "/", "dark")
    disp = page.eval_on_selector(".conf-badge .conf-score", "e => getComputedStyle(e).display")
    assert disp != "none", f"score يجب أن يظهر على المكتب (display={disp})"


# ═══════════ لوحة صفحة السهم ═══════════
@pytest.mark.browser_context_args(**DESKTOP)
def test_panel_position_and_structure(page, server_url):
    _open(page, server_url, "/stock/AAPL", "dark")
    assert page.locator(".dc-panel").count() == 1, "اللوحة غير موجودة"
    pos = page.evaluate(
        "() => { const y = s => { const e = document.querySelector(s); return e ? e.getBoundingClientRect().top : null; };"
        " return { sc: y('.score-cards'), dc: y('.dc-panel'), tm: y('.tmeter-wrap') }; }")
    assert pos["sc"] is not None and pos["dc"] is not None
    assert pos["sc"] < pos["dc"], "اللوحة ليست بعد score-cards"
    assert pos["tm"] is None or pos["dc"] < pos["tm"], "اللوحة ليست قبل tmeter"
    assert page.locator(".dc-progress").count() == 7, "العوامل السبعة غير مكتملة"
    tag = page.eval_on_selector(".dc-panel", "e => e.tagName.toLowerCase() + '|' + (e.getAttribute('aria-labelledby') || '')")
    assert tag == "section|dc-title", f"section + aria-labelledby=dc-title مفقود ({tag})"
    body = page.content()
    assert "reason_code" not in body and "schema_version" not in body, "ظهرت حقول داخلية"


@pytest.mark.browser_context_args(**DESKTOP)
def test_panel_missing_caps_and_below_half(page, server_url):
    _open(page, server_url, "/stock/MSFT", "dark")
    assert page.locator(".dc-panel").count() == 1
    assert page.locator(".dc-missing").count() == 1, "قسم missing غير ظاهر"
    assert page.locator(".dc-caps").count() == 1, "قسم caps غير ظاهر"
    assert page.locator(".dc-below-half").count() >= 1, "عامل جوهري تحت النصف غير ظاهر"


@pytest.mark.browser_context_args(**DESKTOP)
def test_panel_unavailable_no_false_factors(page, server_url):
    _open(page, server_url, "/stock/NVDA", "dark")
    assert page.locator(".dc-panel.conf-na").count() == 1, "لوحة conf-na غير موجودة"
    assert page.locator(".dc-factor").count() == 0, "ظهرت عوامل زائفة في unavailable"
    assert "غير متوفرة" in page.content(), "نص «غير متوفرة» غير ظاهر"


@pytest.mark.browser_context_args(**DESKTOP)
def test_date_ltr_computed(page, server_url):
    _open(page, server_url, "/stock/AAPL", "dark")
    d = page.eval_on_selector(".dc-asof time", "e => getComputedStyle(e).direction")
    assert d == "ltr", f"اتجاه التاريخ المحسوب ليس ltr ({d})"
    txt = page.eval_on_selector(".dc-asof time", "e => e.textContent.trim()")
    assert txt == "2026-08-21", f"نص التاريخ تغيّر ({txt})"


# ═══════════ overflow أفقي للصفحة كاملة — المقياس الرسمي (scrollingElement) ═══════════
# ثماني حالات مستقلة (صفحتان × حجمان × ثيمان)؛ فشل الرئيسية لا يمنع فحص صفحة السهم.
def _assert_no_overflow_full_page(page, base_url, path, theme):
    _open(page, base_url, path, theme)
    m = page.evaluate("() => { const s = document.scrollingElement;"
                      " return { sw: s.scrollWidth, cw: s.clientWidth }; }")
    assert m["sw"] <= m["cw"] + 1, (
        f"overflow أفقي في {path} ({theme}): scrollWidth={m['sw']} > clientWidth={m['cw']}+1")


@pytest.mark.browser_context_args(**DESKTOP)
@pytest.mark.parametrize("path", ["/", "/stock/AAPL"])
def test_no_overflow_desktop_dark(page, server_url, path):
    _assert_no_overflow_full_page(page, server_url, path, "dark")


@pytest.mark.browser_context_args(**DESKTOP)
@pytest.mark.parametrize("path", ["/", "/stock/AAPL"])
def test_no_overflow_desktop_light(page, server_url, path):
    _assert_no_overflow_full_page(page, server_url, path, "light")


@pytest.mark.browser_context_args(**MOBILE)
@pytest.mark.parametrize("path", ["/", "/stock/AAPL"])
def test_no_overflow_mobile_dark(page, server_url, path):
    _assert_no_overflow_full_page(page, server_url, path, "dark")


@pytest.mark.browser_context_args(**MOBILE)
@pytest.mark.parametrize("path", ["/", "/stock/AAPL"])
def test_no_overflow_mobile_light(page, server_url, path):
    _assert_no_overflow_full_page(page, server_url, path, "light")


# ═══════════ التباين المحسوب — warning + critical (dark/light) ═══════════
# الأزواج المقفلة الفعلية: warning = var(--dc-warning)؛ critical = var(--dc-critical) على
# عناصر العامل الجوهري تحت النصف (label + crit-tag؛ CSS يعيّن لهما --dc-critical في dc-below-half).
_WARNING_SELECTORS = (".dc-missing-title", ".dc-cap-head")
_CRITICAL_SELECTORS = (".dc-below-half .dc-factor-label", ".dc-below-half .dc-crit-tag")
# الوسم العادي (خارج dc-below-half) — بعد إصلاح لونه إلى AA (داكن #9aa6bd، فاتح var(--muted)).
_GENERAL_SELECTORS = (".dc-factor:not(.dc-below-half) .dc-crit-tag",)


def _assert_contrast_pairs(page, label):
    for sel in _WARNING_SELECTORS + _CRITICAL_SELECTORS + _GENERAL_SELECTORS:
        ratio, pair = _contrast_of(page, sel)
        print(f"[contrast {label}] {sel}: {ratio:.2f}  (fg={pair['fg']} bg={pair['bg']})")
        assert ratio >= 4.5, f"تباين {sel} ({label}) = {ratio:.2f} < 4.5"


@pytest.mark.browser_context_args(**DESKTOP)
def test_locked_pairs_contrast_dark(page, server_url):
    _open(page, server_url, "/stock/MSFT", "dark")
    _assert_contrast_pairs(page, "dark")


@pytest.mark.browser_context_args(**DESKTOP)
def test_locked_pairs_contrast_light(page, server_url):
    _open(page, server_url, "/stock/MSFT", "light")
    _assert_contrast_pairs(page, "light")


# ═══════════ منع الشبكة صراحةً (الحارس التلقائي يؤكّد على كل اختبار أيضاً) ═══════════
@pytest.mark.browser_context_args(**DESKTOP)
def test_no_external_requests(page, server_url):
    _open(page, server_url, "/", "dark")
    page.goto(server_url + "/stock/AAPL", wait_until="load")
    assert page.locator(".dc-panel").count() == 1  # التحميل نجح والحارس لم يُسجّل violations


# ═══════════ انحدار عقد السماح الشبكي (is_allowed_url) — يفشل لو عاد إلى startswith ═══════════
def test_url_guard_accepts_local_rejects_hostile():
    port = 54321
    host = "127.0.0.1"
    assert is_allowed_url(f"http://127.0.0.1:{port}/", host, port), "رفض الرابط المحلي الصحيح"
    assert is_allowed_url(f"http://127.0.0.1:{port}/gems?a=1", host, port)
    assert is_allowed_url("about:blank", host, port)
    # روابط عدائية — كلها يجب أن تُرفض:
    assert not is_allowed_url("http://evil.com/", host, port), "قبل مضيفاً خارجياً"
    assert not is_allowed_url("http://127.0.0.1.evil.com/", host, port), "قبل مضيفاً مشابهاً نصياً"
    assert not is_allowed_url(f"http://127.0.0.1:{port}@evil.com/", host, port), "قبل حيلة userinfo"
    assert not is_allowed_url(f"http://127.0.0.1:{port + 1}/", host, port), "قبل منفذاً مختلفاً"
    assert not is_allowed_url(f"http://localhost:{port}/", host, port), "قبل localhost (العقد 127.0.0.1 فقط)"
    assert not is_allowed_url(f"https://127.0.0.1:{port}/", host, port), "قبل بروتوكولاً مختلفاً"
    # malformed — يجب أن تُرفض fail-closed (بلا رمي استثناء):
    assert not is_allowed_url("http://127.0.0.1:bad/", host, port), "لم يرفض منفذاً غير رقمي"
    assert not is_allowed_url("http://127.0.0.1:99999/", host, port), "لم يرفض منفذاً خارج المدى"


def test_loopback_host_contract():
    # يقبل loopback:
    assert is_loopback_host("127.0.0.1")
    assert is_loopback_host("127.5.6.7")   # 127.0.0.0/8
    assert is_loopback_host("::1")
    assert is_loopback_host("localhost")
    # يرفض غير loopback / غير صالح (بلا استثناء):
    assert not is_loopback_host("0.0.0.0")
    assert not is_loopback_host("127.evil.com")
    assert not is_loopback_host("127.0.0.1.evil.com")
    assert not is_loopback_host("evil.com")
    assert not is_loopback_host("")
    assert not is_loopback_host(None)


# ═══════════ عزل .env (بلا لمس .env الحقيقي؛ بيئة child صريحة) ═══════════
def _run_dotenv_probe(disable):
    with tempfile.TemporaryDirectory(prefix="e2e_dotenv_") as d:
        with open(os.path.join(d, ".env"), "w", encoding="utf-8") as f:
            f.write("E2E_DUMMY_SECRET=leaked123\n")
        child_env = {"PATH": os.environ.get("PATH", "")}  # بيئة صريحة، لا نسخة os.environ
        child_env.pop("E2E_DUMMY_SECRET", None)  # صريحاً: لا نمرّر السرّ عبر البيئة
        disable_line = "import dotenv; dotenv.load_dotenv = lambda *a, **k: None\n" if disable else ""
        script = ("import os\n" + disable_line +
                  "from dotenv import load_dotenv; load_dotenv()\n"
                  "print(os.environ.get('E2E_DUMMY_SECRET'))\n")
        return subprocess.run([sys.executable, "-c", script], cwd=d, env=child_env,
                              capture_output=True, text=True)


def test_env_isolation_no_dotenv_leak():
    ctrl = _run_dotenv_probe(disable=False)
    assert ctrl.returncode == 0, f"الضابط تعطّل: {ctrl.stderr}"
    assert ctrl.stdout.strip() == "leaked123", f"الضابط لم يُظهر السرّ: {ctrl.stdout!r}"
    dis = _run_dotenv_probe(disable=True)
    assert dis.returncode == 0, f"probe التعطيل تعطّل: {dis.stderr}"
    assert dis.stdout.strip() == "None", f"تسرّب السرّ رغم التعطيل: {dis.stdout!r}"
