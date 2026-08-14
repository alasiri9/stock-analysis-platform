"""
edgar_client.py — جلب معاملات المطلعين (Insider Transactions) من SEC EDGAR.

المصدر: نماذج Form 4 التي يودعها المطلعون (مدراء/مسؤولون) عند بيع أو شراء أسهم شركتهم.

الخطوات:
1. تحويل رمز السهم إلى CIK (معرّف الشركة لدى SEC).
2. جلب سجل الإيداعات الأخير وتصفية نماذج Form 4.
3. تحميل XML الخام لكل نموذج واستخراج المعاملات.

ملاحظات:
- SEC تتطلب ترويسة User-Agent فيها وسيلة تواصل (إيميل) — إلزامية.
- None ≠ 0 : أي حقل غير موجود يبقى None (مثلاً السعر في منح الأسهم).
- لا نخترع بيانات : لو فشل أي طلب نتجاهله ونكمل، وفي النهاية قد نُرجع قائمة فارغة.
"""

import time
import xml.etree.ElementTree as ET

import requests

# SEC تشترط User-Agent يحدّد هويّة التطبيق ووسيلة تواصل
HEADERS = {"User-Agent": "StockAnalysisPlatform alasiri9@hotmail.com"}
TIMEOUT = 20

# خريطة الرمز -> CIK تُحمّل مرة واحدة وتُخزّن في الذاكرة (cache)
_cik_map = None

# رموز المعاملات الشائعة في Form 4 وترجمتها
_CODE_LABELS = {
    "P": "شراء بسعر السوق",
    "S": "بيع بسعر السوق",
    "A": "منحة/استحقاق أسهم",
    "M": "شراء بسعر خاص (خيارات)",
    "F": "اقتطاع أسهم لسداد الضريبة",
    "G": "نقل أسهم لمستفيد آخر",
    "D": "استرداد للشركة",
    "C": "تحويل مشتق",
    "X": "تنفيذ حق",
    "J": "اقتناء/تصرّف آخر",
    "I": "معاملة تقديرية",
    "V": "معاملة مُبلَّغ عنها مبكراً",
    "O": "تنفيذ خيار خارج السعر",
    "E": "انتهاء مركز مشتق قصير",
    "H": "انتهاء مركز مشتق طويل",
    "L": "اقتناء صغير",
    "W": "نقل بالإرث/الوصية",
    "Z": "إيداع/سحب من صندوق تصويت",
    "K": "معاملة مقايضة أسهم",
    "U": "تصرّف عبر عرض استحواذ",
}

# اتجاه المعاملة يُشتقّ من الكود (نفس مصدر «النوع») ليتطابق معه دائماً ويُزال التعارض
# النادر في بيانات SEC (كود «شراء» مع علم «تخلّص»). للأكواد غير الحاسمة نرجع لعلم A/D.
_BUY_CODES = {"P", "M"}       # شراء واضح (سوق مفتوح / خيارات)
_SELL_CODES = {"S", "F", "D"}  # بيع/تخلّص واضح (سوق / ضريبة / استرداد)


def _load_cik_map():
    """يحمّل خريطة الرموز -> CIK من SEC (مرة واحدة)."""
    global _cik_map
    if _cik_map is not None:
        return _cik_map
    try:
        data = requests.get(
            "https://www.sec.gov/files/company_tickers.json",
            headers=HEADERS, timeout=TIMEOUT,
        ).json()
    except (requests.RequestException, ValueError) as e:
        print(f"[EDGAR] فشل تحميل خريطة CIK: {e}")
        return {}
    _cik_map = {row["ticker"].upper(): str(row["cik_str"]).zfill(10) for row in data.values()}
    return _cik_map


def get_cik(ticker):
    """يُرجع CIK (10 خانات) لرمز السهم، أو None لو غير موجود."""
    return _load_cik_map().get(ticker.upper())


def _parse_form4(xml_text):
    """يحلّل XML خام لنموذج Form 4 ويُرجع قائمة معاملات (غير مشتقّة).

    كل معاملة dict: owner, title, date, code, code_label, direction, shares, price.
    يُرجع None لو كان XML غير صالح (فشل تحليل) — يُميَّز عن القائمة الفارغة (نموذج
    صحيح لكن بلا معاملات غير مشتقّة). None هنا = فشل، [] = نجاح بلا صفوف.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None

    owner_el = root.find(".//reportingOwner")
    owner_name = owner_el.findtext(".//rptOwnerName") if owner_el is not None else None
    rel = owner_el.find(".//reportingOwnerRelationship") if owner_el is not None else None
    if rel is not None:
        if rel.findtext("isDirector") in ("1", "true"):
            title = "عضو مجلس إدارة"
        elif rel.findtext("officerTitle"):
            title = rel.findtext("officerTitle")
        elif rel.findtext("isOfficer") in ("1", "true"):
            title = "مسؤول"
        elif rel.findtext("isTenPercentOwner") in ("1", "true"):
            title = "مالك +10%"
        else:
            title = None
    else:
        title = None

    def _num(text):
        """يحوّل نصاً لرقم، أو None (لا صفر ملفّق)."""
        if text is None or text == "":
            return None
        try:
            return float(text)
        except ValueError:
            return None

    transactions = []
    for t in root.findall(".//nonDerivativeTransaction"):
        code = t.findtext(".//transactionCoding/transactionCode")
        ad = t.findtext(".//transactionAmounts/transactionAcquiredDisposedCode/value")
        transactions.append({
            "owner": owner_name,
            "title": title,
            "date": t.findtext(".//transactionDate/value"),
            "code": code,
            "code_label": _CODE_LABELS.get(code, "معاملة أخرى") if code else None,
            "direction": ("شراء" if code in _BUY_CODES else
                          ("بيع" if code in _SELL_CODES else
                           ("شراء" if ad == "A" else ("بيع" if ad == "D" else None)))),
            "shares": _num(t.findtext(".//transactionAmounts/transactionShares/value")),
            "price": _num(t.findtext(".//transactionAmounts/transactionPricePerShare/value")),
        })
    return transactions


def get_insider_transactions(ticker, max_filings=10, max_rows=15):
    """يُرجع أحدث معاملات المطلعين. نُميّز الفشل عن النجاح-الفارغ صراحةً:

    - None  = فشل الجلب (تعذّر تحديد CIK، أو سقط سجل الإيداعات، أو فشلت كل محاولات
      تحميل النماذج بلا نتيجة). المتصل يُبقي كاش المطلعين السابق ولا يمسحه.
    - []    = نجاح بلا معاملات (لا نماذج Form 4، أو نماذج بلا صفقات فعلية). حالة سليمة.
    - قائمة غير فارغة = نجاح بمعاملات.

    max_filings: كم نموذج Form 4 نفحص. max_rows: حد أقصى للمعاملات المعروضة.
    """
    cik = get_cik(ticker)
    if not cik:
        return None  # تعذّر تحديد الشركة — فشل لا «لا-معاملات»

    # سجل الإيداعات: نفحص نجاح HTTP صراحةً — 4xx/5xx فشل، لا «لا-معاملات».
    try:
        resp = requests.get(
            f"https://data.sec.gov/submissions/CIK{cik}.json",
            headers=HEADERS, timeout=TIMEOUT,
        )
    except requests.RequestException as e:
        print(f"[EDGAR] فشل الاتصال بسجل الإيداعات لـ {ticker}: {e}")
        return None
    if resp.status_code != 200:
        print(f"[EDGAR] سجل الإيداعات لـ {ticker} رجّع حالة {resp.status_code} — فشل")
        return None  # HTTP فاشل لا يجوز أن يصل لمسار «النجاح الفارغ»
    try:
        sub = resp.json()
    except ValueError as e:
        print(f"[EDGAR] استجابة سجل الإيداعات لـ {ticker} غير صالحة (ليست JSON): {e}")
        return None

    recent = sub.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accns = recent.get("accessionNumber", [])
    docs = recent.get("primaryDocument", [])

    results = []
    checked = 0
    fetch_failed = False  # سقطت محاولة تحميل/تحليل نموذج واحد على الأقل (HTTP/اتصال/XML)
    for i, form in enumerate(forms):
        if form != "4":
            continue
        if checked >= max_filings or len(results) >= max_rows:
            break
        checked += 1

        acc = accns[i].replace("-", "")
        raw_doc = docs[i].split("/")[-1]  # نتجاهل بادئة xslF345X0N للحصول على XML الخام
        url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc}/{raw_doc}"
        try:
            doc_resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        except requests.RequestException:
            fetch_failed = True  # فشل اتصال
            continue
        if doc_resp.status_code != 200:
            fetch_failed = True  # HTTP 4xx/5xx فشل — لا يُحسب «نجاحاً فارغاً»
            continue
        parsed = _parse_form4(doc_resp.text)
        if parsed is None:
            fetch_failed = True  # XML غير صالح = فشل تحليل، لا «لا-معاملات»
            continue
        results.extend(parsed)  # قائمة (قد تكون فارغة = نموذج صحيح بلا صفوف)
        time.sleep(0.12)  # لطف مع خوادم SEC (أقل من 10 طلبات/ثانية)

    # لو تعثّرت أي محاولة (HTTP/اتصال/XML) ولم نجمع أي صفّ = فشل، لا «لا-معاملات»:
    # نُعيد None فيُبقي المتصل (radar) الكاش السليم السابق بدل استبداله بقائمة فارغة.
    if fetch_failed and not results:
        return None
    return results[:max_rows]


# ----------------------------------------------------------------------------
# اختبار يدوي: py services/edgar_client.py
# ----------------------------------------------------------------------------
if __name__ == "__main__":
    ticker = "AAPL"
    print(f"=== معاملات المطلعين لـ {ticker} (SEC EDGAR) ===\n")
    rows = get_insider_transactions(ticker) or []  # None (فشل) أو [] (لا معاملات)
    if not rows:
        print("لا توجد معاملات متاحة.")
    for r in rows:
        price = "—" if r["price"] is None else f"{r['price']:.2f}$"
        shares = "—" if r["shares"] is None else f"{r['shares']:,.0f}"
        print(f"{r['date']} | {r['owner']} ({r['title']}) | "
              f"{r['direction'] or '—'} [{r['code_label']}] | {shares} سهم @ {price}")
