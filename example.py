import requests



url = "https://pass.rw.by/ru/route/?from=%D0%9C%D0%B8%D0%BD%D1%81%D0%BA&to=%D0%9E%D1%80%D1%88%D0%B0&date=2025-08-14"


session = requests.Session()
session.headers.update(
                    {
                        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"
                        + " AppleWebKit/537.36 (KHTML, like Gecko)"
                        + " Chrome/133.0.0.0 Safari/537.36",
                        "Accept": "*/*",
                        "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8,"
                        + "ru;q=0.7,it;q=0.6",
                        "Accept-Encoding": "gzip, deflate, br, zstd",
                        "Referer": f"{url}",
                        "X-Requested-With": "XMLHttpRequest",
                    }
                )
r = session.get(url)
print(r.status_code)