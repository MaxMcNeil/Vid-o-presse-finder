import http.server, json, os, sys, threading, time, tempfile, pathlib
os.environ["NTFY_SERVER"] = "http://127.0.0.1:8765"
os.environ["NTFY_TOPIC"] = "t"; os.environ["PHONE_SECRET"] = "s3"
sys.path.insert(0, "/home/claude/quote_finder")
from quote_finder import phone
acks, handled = [], []
good = phone.build_request("Bill Clinton", "x", "", False, False, 5, "en", "s3")
bad = dict(good, id="evil", person="hack")  # signature invalide
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        self.send_response(200); self.end_headers()
        for m in (bad, good):
            self.wfile.write((json.dumps({"id": "e"+m["id"], "event": "message", "message": json.dumps(m)})+"\n").encode()); self.wfile.flush()
        time.sleep(3)
    def do_POST(self):
        n = int(self.headers["Content-Length"]); acks.append(self.rfile.read(n).decode()); self.send_response(200); self.end_headers()
threading.Thread(target=http.server.ThreadingHTTPServer(("127.0.0.1", 8765), H).serve_forever, daemon=True).start()
phone.handle = lambda d, r, c: handled.append(d["id"])
conf = pathlib.Path(tempfile.mkdtemp())
t = threading.Thread(target=phone.listen, args=(pathlib.Path("."), conf), daemon=True); t.start()
time.sleep(2)
print("sortie listener ci-dessus") ; print("acks:", acks, "| traités:", handled, "| attendu:", good["id"])
assert handled == [good["id"]] and good["id"] in acks[0]
print("OK")
