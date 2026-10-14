#!/usr/bin/env python3
"""
ARAFAT GEN — HTTP API server
Wraps gen.py create_account() into a REST API.
Deploy to Vercel / VPS / anywhere.
"""
import os, sys, json, time, threading
from datetime import datetime
from flask import Flask, request, jsonify

# ═══════════════════════════════════════════════════════════════════
#  Import gen.py
# ═══════════════════════════════════════════════════════════════════
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import importlib.util
_spec = importlib.util.spec_from_file_location(
    "arafat_gen", os.path.join(_HERE, "gen.py"))
_gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_gen)

# Grab handles
create_account = _gen.create_account
REGION_LANG    = _gen.REGION_LANG
generate_nick  = _gen.generate_nick

# ═══════════════════════════════════════════════════════════════════
#  Config
# ═══════════════════════════════════════════════════════════════════
API_KEY                = os.environ.get("GEN_API_KEY", "").strip()
DEFAULT_THREADS        = int(os.environ.get("GEN_DEFAULT_THREADS", "3"))
MAX_THREADS            = int(os.environ.get("GEN_MAX_THREADS", "10"))
MAX_COUNT_PER_REQUEST  = int(os.environ.get("GEN_MAX_COUNT", "50"))
PORT                   = int(os.environ.get("PORT", os.environ.get("GEN_PORT", "8080")))
DEFAULT_REGION         = os.environ.get("GEN_DEFAULT_REGION", "BD").upper()
DEFAULT_NICK_PREFIX    = os.environ.get("GEN_NICK_PREFIX", "EREN")
DEFAULT_PW_PREFIX      = os.environ.get("GEN_PW_PREFIX", "Eren")
DO_MAXIM               = os.environ.get("GEN_DO_MAXIM", "1") == "1"

# ── All supported regions ──
SUPPORTED_REGIONS = list(REGION_LANG.keys()) + ["OTHER", "SAC", "EU", "CIS"]

# ═══════════════════════════════════════════════════════════════════
#  Flask app
# ═══════════════════════════════════════════════════════════════════
app = Flask(__name__)

@app.after_request
def _cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-API-Key"
    return resp

def _auth_required(fn):
    def wrap(*a, **k):
        if request.method == "OPTIONS":
            return ("", 204)
        if API_KEY:
            key = request.headers.get("X-API-Key") or request.args.get("api_key")
            if key != API_KEY:
                return jsonify({"ok": False, "error": "unauthorized"}), 401
        return fn(*a, **k)
    wrap.__name__ = fn.__name__
    return wrap

# ═══════════════════════════════════════════════════════════════════
#  Worker
# ═══════════════════════════════════════════════════════════════════
_result_lock = threading.Lock()

def _worker(idx, region, pw_prefix, nick_prefix, results, stop_event):
    if stop_event.is_set():
        return
    try:
        res = create_account(
            pw_prefix=pw_prefix,
            nick_prefix=nick_prefix,
            region=region,
            tid=idx,
            do_maxim=DO_MAXIM,
        )
        if not res:
            return
        account = {
            # app.py expected keys
            "uid":           res.get("uid"),
            "pass":          res.get("password"),
            "password":      res.get("password"),
            "name":          res.get("name"),
            # extra keys
            "nickname":      res.get("nickname"),
            "account_id":    res.get("account_id"),
            "region":        res.get("region"),
            "open_id":       res.get("open_id"),
            "access_token":  res.get("access_token"),
            "refresh_token": res.get("refresh_token"),
            "jwt_token":     res.get("jwt_token"),
            "status":        res.get("status"),
            "date_created":  res.get("date_created"),
            "raw":           res,
        }
        with _result_lock:
            results.append(account)
    except Exception as e:
        sys.stderr.write(f"[worker {idx}] {type(e).__name__}: {e}\n")

# ═══════════════════════════════════════════════════════════════════
#  Routes
# ═══════════════════════════════════════════════════════════════════

@app.route("/", methods=["GET"])
def root():
    return jsonify({
        "ok": True,
        "service": "arafat-gen-api",
        "version": "1.0",
        "regions": SUPPORTED_REGIONS,
        "endpoints": ["POST /api/gen", "GET /health", "GET /regions"],
    })

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True, "time": datetime.utcnow().isoformat() + "Z"})

@app.route("/regions", methods=["GET"])
def regions():
    return jsonify({"ok": True, "regions": SUPPORTED_REGIONS})

@app.route("/api/gen", methods=["POST", "OPTIONS"])
@_auth_required
def api_gen():
    """
    POST /api/gen
    Body (JSON):
      {
        "region":       "BD",         # optional, default BD
        "count":        2,            # 1..MAX_COUNT_PER_REQUEST
        "threads":      1,            # 1..MAX_THREADS
        "name_prefix":  "EREN",       # nickname prefix
        "pw_prefix":    "Eren",       # password prefix
        "do_maxim":     true          # optional, default true
      }
    Response:
      {
        "ok": true, "region": "BD", "requested": 2, "generated": 2,
        "accounts": [ {...}, {...} ],
        "errors": []
      }
    """
    d = request.get_json(silent=True) or {}
    region = (d.get("region") or DEFAULT_REGION).strip().upper()
    if region not in SUPPORTED_REGIONS:
        return jsonify({
            "ok": False, "error": f"unsupported region '{region}'",
            "supported": SUPPORTED_REGIONS,
        }), 400

    try: count = int(d.get("count", 1))
    except Exception:
        return jsonify({"ok": False, "error": "count must be int"}), 400
    try: threads = int(d.get("threads", DEFAULT_THREADS))
    except Exception:
        threads = DEFAULT_THREADS

    name_prefix = (d.get("name_prefix") or DEFAULT_NICK_PREFIX).strip()[:12]
    pw_prefix   = (d.get("pw_prefix")   or DEFAULT_PW_PREFIX).strip()
    do_maxim    = bool(d.get("do_maxim", DO_MAXIM))

    if not 1 <= count <= MAX_COUNT_PER_REQUEST:
        return jsonify({
            "ok": False,
            "error": f"count must be 1..{MAX_COUNT_PER_REQUEST}"
        }), 400
    threads = max(1, min(threads, MAX_THREADS, count))

    # Override gen.py module global DO_MAXIM
    try:
        _gen.DO_MAXIM = do_maxim
    except Exception:
        pass

    results = []
    stop_event = threading.Event()
    workers = []
    for i in range(threads):
        t = threading.Thread(
            target=_worker,
            args=(i + 1, region, pw_prefix, name_prefix, results, stop_event),
            daemon=True,
            name=f"gen-{i+1}",
        )
        t.start()
        workers.append(t)

    deadline = time.time() + 300  # 5 min cap
    while time.time() < deadline:
        with _result_lock:
            if len(results) >= count:
                stop_event.set()
                break
        if all(not t.is_alive() for t in workers):
            break
        time.sleep(0.5)

    with _result_lock:
        out = results[:count]

    return jsonify({
        "ok": True,
        "region": region,
        "requested": count,
        "generated": len(out),
        "accounts": out,
        "errors": [],
    })

if __name__ == "__main__":
    print()
    print("  🔥 YUTA GEN API")
    print(f"  · port: {PORT}")
    print(f"  · key required: {'YES' if API_KEY else 'NO'}")
    print(f"  · default region: {DEFAULT_REGION}")
    print(f"  · regions: {', '.join(SUPPORTED_REGIONS)}")
    print(f"  · max count: {MAX_COUNT_PER_REQUEST}, max threads: {MAX_THREADS}")
    print()
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)