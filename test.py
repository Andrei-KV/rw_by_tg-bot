from urllib.parse import quote

import requests
from bs4 import BeautifulSoup
from bs4.filter import SoupStrainer

encoded_from = quote("Минск")
encoded_to = quote("Витебск")
date = "2025-04-26"
# получение новой страницы soup

url = f"https://pass.rw.by/ru/route/?from={"Минск"}&to={"Витебск"}&date={"2025-12-26"}"
url = f"https://pass.rw.by/ru/route/?from={encoded_from}&to={encoded_to}&date={date}"

r = requests.get(url)

only_span_div_tag = SoupStrainer(["span", "div"])
soup = BeautifulSoup(r.text, "lxml", parse_only=only_span_div_tag)

train_info = soup.select(f'div.sch-table__row[data-train-number="714Б"]')  # type: ignore
print(train_info[0]["data-ticket_selling_allowed"])
# selling_allowed = train_info[0]['data-ticket_selling_allowed']
for train in train_info:
    print(train.attrs)
