"""Loopback-only module acceptance tool. No product routes or file browsing."""
import argparse
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading

from .adaptive_host import AdaptiveHost
from ..lab.adaptive import AdaptiveLab, adaptive_context
from ..lab.aiping import AipingClient, AUTHORIZED_MODELS
from ..lab.errors import LabError
from ..lab.models import GenerateJSONAdapter


class AcceptanceServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port=8765, mode="mock", model="DeepSeek-V4.1-Flash"):
        self.mode, self.model = mode, model
        self.client = AipingClient(model=model) if mode == "live" else None
        self.lab = AdaptiveLab(GenerateJSONAdapter(self.client.generate_json) if self.client else None,timeout_seconds=120)
        self.sessions, self.sessions_lock = {}, threading.Lock()
        self.page = (Path(__file__).parent / "adaptive_ui.html").read_bytes()
        super().__init__(("127.0.0.1",port), Handler)
        self.origin = f"http://127.0.0.1:{self.server_port}"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # Never log payloads or cookies.

    def _send(self, status, value, cookie=None, html=False):
        raw = value if html else json.dumps(value,ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type","text/html; charset=utf-8" if html else "application/json; charset=utf-8")
        self.send_header("Content-Length",str(len(raw)))
        self.send_header("Cache-Control","no-store")
        self.send_header("X-Content-Type-Options","nosniff")
        self.send_header("Content-Security-Policy","default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        if cookie:
            self.send_header("Set-Cookie",f"lab_session={cookie}; HttpOnly; SameSite=Strict; Path=/")
        self.end_headers()
        self.wfile.write(raw)

    def _host_valid(self):
        return self.headers.get("Host") == self.server.origin.removeprefix("http://")

    def _session(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie",""))
            token = cookie["lab_session"].value if "lab_session" in cookie else None
        except Exception:
            token = None
        with self.server.sessions_lock:
            if token in self.server.sessions:
                return self.server.sessions[token],None
            if len(self.server.sessions) >= 32:
                raise LabError("SESSION_LIMIT","本地工具会话已达上限，请保存结果后重启工具。")
            token = secrets.token_urlsafe(24)
            host = AdaptiveHost(adaptive_context("dev_"+secrets.token_hex(8),self.server.mode))
            self.server.sessions[token] = host
            return host,token

    def do_GET(self):
        if not self._host_valid():
            self._send(403,{"error":{"code":"HOST_REJECTED","message":"仅接受本机验收地址。"}})
            return
        if self.path == "/":
            self._send(200,self.server.page,html=True)
        elif self.path == "/api/state":
            try:
                host,cookie = self._session()
                value = host.public_read(self.server.lab)
                value["model"] = self.server.model if self.server.mode == "live" else None
                self._send(200,value,cookie)
            except LabError as exc:
                self._send(409,exc.to_dict())
        elif self.path == "/favicon.ico":
            self._send(204,b"",html=True)
        else:
            self._send(404,{"error":{"code":"NOT_FOUND","message":"无此工具入口。"}})

    def do_POST(self):
        if not self._host_valid() or self.headers.get("Origin") != self.server.origin:
            self._send(403,{"error":{"code":"ORIGIN_REJECTED","message":"请求来源不匹配。"}})
            return
        if self.path != "/api/action":
            self._send(404,{"error":{"code":"NOT_FOUND","message":"无此工具入口。"}})
            return
        try:
            length = int(self.headers.get("Content-Length","0"))
            if not 0 < length <= 4096 or self.headers.get("Content-Type","").split(";")[0] != "application/json":
                raise ValueError()
            data = json.loads(self.rfile.read(length))
            if not isinstance(data,dict):
                raise ValueError()
            action,request_id,revision = (data.pop(k) for k in ("action","request_id","expected_revision"))
            host,cookie = self._session()
            result = host.execute(self.server.lab,action,request_id,revision,**data)
            # Never serialize the trusted server snapshot or model configuration.
            value = {k:result[k] for k in ("revision","lab_state","plan")}
            value.update(runtime_mode=self.server.mode, model=self.server.model if self.server.mode == "live" else None)
            self._send(200,value,cookie)
        except LabError as exc:
            self._send(409,exc.to_dict())
        except (ValueError,TypeError,KeyError,UnicodeError):
            self._send(400,{"error":{"code":"INVALID_INPUT","message":"请求格式无效。"}})


def main():
    parser = argparse.ArgumentParser(description="盲点实验室本地验收工具")
    parser.add_argument("--port",type=int,default=8765)
    parser.add_argument("--mode",choices=("mock","live"),default="mock")
    parser.add_argument("--model",choices=sorted(AUTHORIZED_MODELS),default="DeepSeek-V4.1-Flash")
    args = parser.parse_args()
    server = AcceptanceServer(args.port,args.mode,args.model)
    print(f"本地模块验收：{server.origin}；模式={args.mode}；内存存储，重启不保留。",flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
