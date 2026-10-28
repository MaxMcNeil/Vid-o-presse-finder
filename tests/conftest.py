import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import os, tempfile
os.environ["QF_CACHE_DIR"] = tempfile.mkdtemp()
os.environ["QF_QUOTA_FILE"] = os.path.join(tempfile.mkdtemp(), "quota.json")
import importlib
import quote_finder.cache as c
importlib.reload(c)
os.environ["QF_SOCIAL"] = "0"   # tests hors ligne : Telegram / Reddit testés à part (test_social.py, sans réseau)
