import threading
from kivy.app import App
from kivy.uix.screenmanager import ScreenManager, Screen
from kivy.properties import StringProperty
from kivy.clock import Clock

import crawler
import indexer
from db import get_db, get_indexer_offset

LOG_PATH = "/storage/emulated/0/Download/ArtCrawler/failed_download.log"


class HomeScreen(Screen):
    status_text = StringProperty("Idle")
    db_stats_text = StringProperty("DB: loading…")

    crawler_status_text = StringProperty("Crawler: idle")
    indexer_status_text = StringProperty("Indexer: idle")

    last_offset = 0

    crawler_thread = None
    indexer_thread = None

    crawler_running = False
    indexer_running = False

    def on_enter(self):
        Clock.schedule_interval(self.update_status, 1)

    # -------------------------
    # Unified status updater
    # -------------------------
    def update_status(self, dt):
        self.update_db_stats()
        self.update_running_status()

    def update_running_status(self):
        parts = []
        parts.append("Crawler: RUNNING" if self.crawler_running else "Crawler: stopped")
        parts.append("Indexer: RUNNING" if self.indexer_running else "Indexer: stopped")
        self.status_text = " | ".join(parts)

    # -------------------------
    # DB stats
    # -------------------------
    def update_db_stats(self):
        try:
            conn = get_db()
            c = conn.cursor()

            total = c.execute("SELECT COUNT(*) FROM items").fetchone()[0]
            modern = c.execute("SELECT COUNT(*) FROM items WHERE bucket='modern'").fetchone()[0]
            contemporary = c.execute("SELECT COUNT(*) FROM items WHERE bucket='contemporary'").fetchone()[0]

            self.db_stats_text = (
                f"Items: {total} | Modern: {modern} | Contemporary: {contemporary}"
            )

            conn.close()
        except Exception as e:
            self.db_stats_text = f"DB error: {e}"

    # -------------------------
    # Crawler controls
    # -------------------------
    def start_crawler(self):
        if self.crawler_running:
            self.status_text = "Crawler already running"
            return

        self.status_text = "Starting crawler..."
        crawler.STOP_REQUESTED = False
        self.crawler_running = True

        Clock.unschedule(self.check_crawler_progress)
        Clock.schedule_interval(self.check_crawler_progress, 10)

        self.crawler_thread = threading.Thread(
            target=self._run_crawler_thread,
            daemon=True
        )
        self.crawler_thread.start()

    def _run_crawler_thread(self):
        try:
            crawler.run_crawler(
                progress_callback=lambda msg: Clock.schedule_once(
                    lambda dt: self._set_crawler_status(msg)
                )
            )
        except Exception as e:
            msg = str(e).split("\n")[0]
            Clock.schedule_once(
                lambda dt, err=msg: self._set_status(f"CRAWLER ERROR: {err}")
            )
        finally:
            self.crawler_running = False
            Clock.unschedule(self.check_crawler_progress)
            Clock.schedule_once(lambda dt: self._set_status("Crawler stopped"))

    def stop_crawler(self):
        if not self.crawler_running:
            self.status_text = "Crawler not running"
            return

        crawler.STOP_REQUESTED = True
        self.status_text = "Stopping crawler..."
        Clock.unschedule(self.check_crawler_progress)

    def _set_crawler_status(self, msg):
        self.crawler_status_text = msg

    # -------------------------
    # Indexer controls
    # -------------------------
    def start_indexer(self):
        if self.indexer_running:
            self.status_text = "Indexer already running"
            return

        self.status_text = "Starting indexer..."
        indexer.STOP_INDEXER = False
        self.indexer_running = True

        self.last_offset = 0

        Clock.schedule_interval(self.check_indexer_progress, 300)

        self.indexer_thread = threading.Thread(
            target=self._run_indexer_thread,
            daemon=True
        )
        self.indexer_thread.start()

    def _run_indexer_thread(self):
        try:
            indexer.run_indexer(
                progress_callback=lambda msg: Clock.schedule_once(
                    lambda dt: self._set_indexer_status(msg)
                )
            )
        except Exception as e:
            msg = str(e).split("\n")[0]
            Clock.schedule_once(
                lambda dt, err=msg: self._set_status(f"INDEXER ERROR: {err}")
            )
        finally:
            self.indexer_running = False
            Clock.unschedule(self.check_indexer_progress)
            Clock.schedule_once(lambda dt: self._set_status("Indexer stopped"))

    def stop_indexer(self):
        if not self.indexer_running:
            self.status_text = "Indexer not running"
            return

        indexer.STOP_INDEXER = True
        self.status_text = "Stopping indexer..."
        Clock.unschedule(self.check_indexer_progress)

    def _set_indexer_status(self, msg):
        self.indexer_status_text = msg

    # -------------------------
    # Progress checks
    # -------------------------
    def check_indexer_progress(self, dt):
        try:
            current = get_indexer_offset()
        except Exception as e:
            self._set_status(f"DB error: {e}")
            return

        if self.last_offset == 0:
            self.last_offset = current
            self._set_status(f"Indexer running… offset {current}")
            return

        if current > self.last_offset:
            self._set_status(f"Indexer active — offset {self.last_offset} → {current}")
        else:
            self._set_status("Indexer stalled — no progress in last check")

        self.last_offset = current

    def check_crawler_progress(self, dt):
        try:
            conn = get_db()
            c = conn.cursor()

            c.execute("SELECT COUNT(*) FROM items")
            total = c.fetchone()[0]

            c.execute("SELECT COUNT(*) FROM items WHERE done = 1")
            downloaded = c.fetchone()[0]

            pending = total - downloaded

            c.execute("""
                SELECT qid, year, bucket
                FROM items
                WHERE done = 1
                ORDER BY rowid DESC
                LIMIT 1
            """)
            last = c.fetchone()

            conn.close()

        except Exception as e:
            self._set_status(f"Crawler DB error: {e}")
            return

        if last:
            qid, year, bucket = last
            last_text = f"Last: {qid} ({year}, {bucket})"
        else:
            last_text = "Last: none yet"

        self._set_status(
            f"Crawler — {downloaded}/{total} downloaded | Pending {pending} | {last_text}"
        )

    # -------------------------
    # Log forwarding
    # -------------------------
    def _set_status(self, text):
        self.status_text = text


class LogScreen(Screen):
    log_text = StringProperty("")

    def on_enter(self):
        self.load_logs()

    def load_logs(self):
        try:
            with open(LOG_PATH, "r") as f:
                self.log_text = f.read()
        except:
            self.log_text = "No logs found."


class RootManager(ScreenManager):
    pass


class ArtCrawlerApp(App):
    def build(self):
        return RootManager()


if __name__ == "__main__":
    ArtCrawlerApp().run()
