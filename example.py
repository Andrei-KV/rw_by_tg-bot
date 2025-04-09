from bs4 import BeautifulSoup
import requests
with open("example.html") as fp:
    soup = BeautifulSoup(fp, 'html.parser')
tag = soup.a
print(tag)