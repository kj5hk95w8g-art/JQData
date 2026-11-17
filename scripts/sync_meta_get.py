"""读 jqdata.db sync_meta 的值（供 shell 脚本取 checkpoint/额度）。用法: sync_meta_get.py <key>"""
import sqlite3, sys
try:
    r = sqlite3.connect("/data/jqdata-platform/data/jqdata.db").execute(
        "SELECT value FROM sync_meta WHERE key=?", (sys.argv[1],)).fetchone()
    print(r[0] if r else "")
except Exception:
    print("")
