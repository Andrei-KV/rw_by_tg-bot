# from_city = ''
# to_city = ''
# api_key = 'bd3851bc-92e4-4df8-9f8f-4bf09aa50d6d'
# date = '2025-08-16'
# system = ''
# api_url = f"""https://api.rasp.yandex.net/v3.0/search/ ?
#   from={from_city}
# & to={to_city}
# & [apikey={api_key}]
# & [date={date}]
# & [transport_types=train]
# & [system={system}]
# & [show_systems={system}]
# """

# url = "https://api.rasp.yandex.net/v3.0/stations_list/
# ?apikey=bd3851bc-92e4-4df8-9f8f-4bf09aa50d6d"

# import requests
# import json
# r = requests.get(url)
# print(r)
# with open("yandex_all_stations.json", "w", encoding="utf-8") as f:
#     # Записываем данные в файл, используя json.dump()
#     # indent=4 делает файл читаемым, но увеличивает его размер
#     json.dump(large_data, f, indent=4, ensure_ascii=False)

# for i in r.json():
#     print(i)
#     input()

a = {1: '1', 2: '2'}
b = [f"{i}" for i in a.items()]
c = *b
print(b)
