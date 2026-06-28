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
    "P": "شراء (سوق مفتوح)",
    "S": "بيع (سوق مفتوح)",
    "A": "منحة/استحقاق أسهم",
    "M": "تنفيذ خيارات",
    "F": "اقتطاع ضريبي",
    "G": "هدية",
    "D": "استرداد للشركة",
    "C": "تحويل مشتق",
    "X": "تنفيذ حق",
}


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
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    owner_el = root.find(".//reportingOwner")
    owner_name = owner_el.findtext(".//rptOwnerName") if owner_el is not None else None
    rel = owner_el.find(".//reportingOwnerRelationship") if owner_el is not None else None
    if rel is not None:
        if rel.findtext("isDirector") in ("1", "true"):
            title = "مدير"
        elif rel.findtext("officerTitle"):
            title = rel.findtext("officerTitle")
        elif rel.findtext("isOfficer") in ("1", "true"):
            title = "مسؤول"
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
            "code_label": _CODE_LABELS.get(code, code),
            "direction": "شراء" if ad == "A" else ("بيع" if ad == "D" else None),
            "shares": _num(t.findtext(".//transactionAmounts/transactionShares/value")),
            "price": _num(t.findtext(".//transactionAmounts/transactionPricePerShare/value")),
        })
    return transactions


def get_insider_transactions(ticker, max_filings=10, max_rows=15):
    """يُرجع قائمة بأحدث معاملات المطلعين، أو [] لو لا شيء/فشل.

    max_filings: كم نموذج Form 4 نفحص. max_rows: حد أقصى للمعاملات المعروضة.
    """
    cik = get_cik(ticker)
    if not cik:
        return []

    try:
        sub = requests.get(
            f"https://data.sec.gov/submissions/CIK{cik}.json",
            headers=HEADERS, timeout=TIMEOUT,
        ).json()
    except (requests.RequestException, ValueError) as e:
        print(f"[EDGAR] فشل جلب سجل الإيداعات لـ {ticker}: {e}")
        return []

    recent = sub.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accns = recent.get("accessionNumber", [])
    docs = recent.get("primaryDocument", [])

    results = []
    checked = 0
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
            xml_text = requests.get(url, headers=HEADERS, timeout=TIMEOUT).text
        except requests.RequestException:
            continue
        results.extend(_parse_form4(xml_text))
        time.sleep(0.12)  # لطف مع خوادم SEC (أقل من 10 طلبات/ثانية)

    return results[:max_rows]


# ----------------------------------------------------------------------------
# اختبار يدوي: py services/edgar_client.py
# ----------------------------------------------------------------------------
if __name__ == "__main__":
    ticker = "AAPL"
    print(f"=== معاملات المطلعين لـ {ticker} (SEC EDGAR) ===\n")
    rows = get_insider_transactions(ticker)
    if not rows:
        print("لا توجد معاملات متاحة.")
    for r in rows:
        price = "—" if r["price"] is None else f"{r['price']:.2f}$"
        shares = "—" if r["shares"] is None else f"{r['shares']:,.0f}"
        print(f"{r['date']} | {r['owner']} ({r['title']}) | "
              f"{r['direction'] or '—'} [{r['code_label']}] | {shares} سهم @ {price}")
