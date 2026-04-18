"""
Vault-AI Remote Storage Server (Standard Library Edition)
Zero-dependency implementation using http.server.
Handles POST /push/<repo>?object_hash=<sha>
"""

import http.server
import os
import shutil
import urllib.parse

PORT = 8000
# Ensure REMOTE_DIR is in the same directory as the server script
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REMOTE_DIR = os.path.join(SCRIPT_DIR, "remote_storage")
os.makedirs(REMOTE_DIR, exist_ok=True)

class VaultHandler(http.server.BaseHTTPRequestHandler):

    def do_GET(self):
        if self.path == "/":
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"service": "Vault-AI Remote Server", "status": "ready"}')
        else:
            self.send_error(404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path_parts = parsed.path.strip("/").split("/")
        
        # Expecting /push/<repo_name>
        if len(path_parts) >= 2 and path_parts[0] == "push":
            repo_name = path_parts[1]
            query = urllib.parse.parse_qs(parsed.query)
            sha = query.get("object_hash", [None])[0]

            if not sha or len(sha) != 64:
                self.send_error(400, "Invalid SHA-256 hash")
                return

            # Read content length
            content_length = int(self.headers.get('Content-Length', 0))
            if content_length == 0:
                self.send_error(400, "Empty payload")
                return

            # Multi-part or raw? For simplicity we used files={"file": f} in requests.
            # But here we'll assume a simpler raw POST if possible, or parse multi-part if needed.
            # Let's check boundary.
            content_type = self.headers.get('Content-Type', '')
            
            repo_path = os.path.join(REMOTE_DIR, repo_name)
            obj_dir = os.path.join(repo_path, "objects", sha[:2])
            os.makedirs(obj_dir, exist_ok=True)
            obj_path = os.path.join(obj_dir, sha[2:])

            # For the purpose of the LiteVault push, we'll just read the body.
            # If it's multipart, this will be messy, so I'll simplify the CLI push too.
            body = self.rfile.read(content_length)
            
            with open(obj_path, "wb") as f:
                f.write(body)

            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status": "success"}')
        else:
            self.send_error(404)

def run():
    server_address = ('', PORT)
    httpd = http.server.HTTPServer(server_address, VaultHandler)
    print(f"  ☁  Vault-AI Remote Server running on port {PORT}...")
    httpd.serve_forever()

if __name__ == "__main__":
    run()
