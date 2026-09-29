import urllib.request

BASES = [
    "https://main.d3enpe7opotop5.amplifyapp.com",
    "https://feature-resource-management-2.d3enpe7opotop5.amplifyapp.com",
]
MARKERS = [
    "EXCHANGE_API_URL",
    "loadExchangeWorkspace",
    "initializeExchange",
    "handover/confirm",
    "transfer/start",
    "/exchange/requests",
]


def check(base: str) -> None:
    print("BASE", base)
    html = urllib.request.urlopen(base + "/", timeout=30).read().decode("utf-8", "replace")
    print(" html_exchange_id", 'id="exchange"' in html)
    print(" html_data_section", 'data-section="exchange"' in html)
    js = urllib.request.urlopen(base + "/app.js", timeout=30).read().decode("utf-8", "replace")
    print(" app_js_len", len(js))
    for m in MARKERS:
        print(" ", m, m in js)


for base in BASES:
    try:
        check(base)
    except Exception as e:
        print("FAIL", base, type(e).__name__, e)
