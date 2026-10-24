import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import os, tempfile
os.environ["QF_CACHE_DIR"] = tempfile.mkdtemp()
import importlib
import quote_finder.cache as c
importlib.reload(c)
