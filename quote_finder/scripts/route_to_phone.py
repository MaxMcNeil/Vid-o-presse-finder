"""Étape du workflow : décide si la recherche tourne sur le téléphone ou sur GitHub (écrit run_github dans GITHUB_OUTPUT)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from quote_finder import phone  # noqa: E402  (stdlib uniquement)

run_github = phone.route()
out = os.environ.get("GITHUB_OUTPUT")
if out:
    with open(out, "a") as f:
        f.write(f"run_github={'true' if run_github else 'false'}\n")
print("run_github =", run_github)
