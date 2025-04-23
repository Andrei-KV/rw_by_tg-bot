from datetime import datetime
today = datetime.today().date()

max_days = 60
date_str = "2025 06 21"
date_obj = datetime.strptime(date_str, "%Y %m %d").date()

print(today)
print(date_obj)
a = (today - date_obj).days
print(type(a))