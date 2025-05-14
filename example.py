from datetime import datetime, timedelta
today = datetime.today().date()
tomorrow = today + timedelta(days=1)
max_days = 60
date_str = "01"
date_obj = datetime.strptime(date_str, "%d").date()

print(today)
print(tomorrow)
