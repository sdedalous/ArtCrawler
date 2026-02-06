from time import sleep
from android import AndroidService

service = AndroidService("ArtCrawler Background Service", "Running…")
service.start()

while True:
    sleep(5)
