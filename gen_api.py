#!/usr/bin/env python3
"""
ARAFAT GEN — HTTP API (Vercel-safe version)
"""
import os, sys, json, time, threading
from datetime import datetime
from flask import Flask, request, jsonify

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import importlib.util
_spec = importlib.util.spec_from_file_location(
    "arafat_gen", os.path.join(_HERE, "gen.py"))
_gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_gen)

create_account = _gen.create_account
REGION_LANG = _gen.REGION_LANG

API_KEY = os.environ.get("GEN_API_KEY", "").strip()
DEFAULT_REGION = os.environ.get("GEN_DEFAULT_REGION", "BD").upper()
DEFAULT_NICK = os.environ.get("GEN_NICK_PREFIX", "EREN")
DEFAULT_PW = os.environ.get("GEN_PW_PREFIX", "Eren")
DO_MAXIM = os.environ.get("GEN_DO_MAXIM", "0") == "1"   # Vercel-এ OFF default

# ── Time budget: Vercel Hobby = 10s. Pro = 60s. Set conservative. ──
TIME_BUDGET = float(os.environ.get("GEN_TIME_BUDGET", "8.0"))

SUPPORTED_REGIONS = list(REGION_LANG.keys()) + ["OTHER", "SAC", "EU", "CIS"]

app = Flask(__name__)

@app.after_request
def _cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-API-Key"
    return resp

def _auth_ok():
    if not API_KEY:
        return True
    key = request.headers.get("X-API-Key") or request.args.get("api_key")
    return key == API_KEY

# ── Background-safe generation store (per-lambda memory) ──
_gen_lock = threading.Lock()
_gen_results = {}   # job_id -> {"status":..., "accounts":[...], "started": ts}

def _bg_generate(job_id, region, count, threads, nick_prefix, pw_prefix, do_maxim):
    try:
        results = []
        _gen.DO_MAXIM = do_maxim
        for i in range(max(1, min(threads, count))):
            if len(results) >= count:
                break
            try:
                r = create_account(
                    pw_prefix=pw_prefix,
                    nick_prefix=nick_prefix,
                    region=region,
                    tid=i + 1,
                    do_maxim=do_maxim,
                )
                if r:
                    results.append({
                        "uid":           r.get("uid"),
                        "pass":          r.get("password"),
                        "password":      r.get("password"),
                        "name":          r.get("name"),
                        "nickname":      r.get("nickname"),
                        "account_id":    r.get("account_id"),
                        "region":        r.get("region"),
                        "open_id":       r.get("open_id"),
                        "access_token":  r.get("access_token"),
                        "jwt_token":     r.get("jwt_token"),
                        "status":        r.get("status"),
                        "date_created":  r.get("date_created"),
                    })
            except Exception:
                pass
        with _gen_lock:
            _gen_results[job_id] = {
                "status": "done",
                "accounts": results,
                "count": len(results),
            }
    except Exception as e:
        with _gen_lock:
            _gen_results[job_id] = {
                "status": "error",
                "error": f"{type(e).__name__}: {e}",
                "accounts": [],
            }

# ═══════════════════════════════════════════════════════════════════
#  Routes
# ═══════════════════════════════════════════════════════════════════

@app.route("/", methods=["GET"])
def root():
    return jsonify({
        "ok": True, "service": "yuta-gen-api",
        "regions": SUPPORTED_REGIONS,
        "endpoints": ["POST /api/gen", "GET /api/gen/status/<job_id>", "GET /health"],
    })

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True, "time": datetime.utcnow().isoformat() + "Z"})

@app.route("/regions", methods=["GET"])
def regions():
    return jsonify({"ok": True, "regions": SUPPORTED_REGIONS})

@app.route("/api/gen", methods=["POST", "OPTIONS"])
def api_gen():
    """
    Vercel-safe endpoint. Returns 202 + job_id immediately.
    Client polls /api/gen/status/<job_id> for results.
    """
    if request.method == "OPTIONS":
        return ("", 204)
    if not _auth_ok():
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    d = request.get_json(silent=True) or {}
    region = (d.get("region") or DEFAULT_REGION).strip().upper()
    if region not in SUPPORTED_REGIONS:
        return jsonify({"ok": False, "error": f"unsupported region '{region}'"}), 400

    try: count = int(d.get("count", 1))
    except Exception: count = 1
    count = max(1, min(count, 10))

    try: threads = int(d.get("threads", 1))
    except Exception: threads = 1
    threads = max(1, min(threads, 3))

    nick_prefix = (d.get("name_prefix") or DEFAULT_NICK).strip()[:12]
    pw_prefix   = (d.get("pw_prefix")   or DEFAULT_PW).strip()
    do_maxim    = bool(d.get("do_maxim", DO_MAXIM))

    # Try synchronous first — if fast, returns accounts; if slow, returns job_id
    import uuid
    job_id = uuid.uuid4().hex[:12]

    results = []
    _gen.DO_MAXIM = do_maxim
    deadline = time.time() + TIME_BUDGET

    for i in range(threads):
        if len(results) >= count: break
        if time.time() > deadline: break
        try:
            r = create_account(
                pw_prefix=pw_prefix, nick_prefix=nick_prefix,
                region=region, tid=i + 1, do_maxim=do_maxim,
            )
            if r:
                results.append({
                    "uid":         r.get("uid"),
                    "pass":        r.get("password"),
                    "password":    r.get("password"),
                    "name":        r.get("name"),
                    "nickname":    r.get("nickname"),
                    "account_id":  r.get("account_id"),
                    "region":      r.get("region"),
                    "open_id":     r.get("open_id"),
                    "access_token":r.get("access_token"),
                    "jwt_token":   r.get("jwt_token"),
                    "status":      r.get("status"),
                    "date_created":r.get("date_created"),
                })
        except Exception:
            pass

    # If we filled the request within budget — return immediately
    if len(results) >= count:
        return jsonify({
            "ok": True, "region": region,
            "requested": count, "generated": len(results),
            "accounts": results, "job_id": job_id, "async": False,
        })

    # Otherwise, kick off background thread and return job_id
    with _gen_lock:
        _gen_results[job_id] = {"status": "running", "accounts": results, "count": len(results)}

    t = threading.Thread(
        target=_bg_generate,
        args=(job_id, region, count, threads, nick_prefix, pw_prefix, do_maxim),
        daemon=True,
    )
    t.start()

    return jsonify({
        "ok": True, "region": region,
        "requested": count, "generated": len(results),
        "accounts": results, "job_id": job_id, "async": True,
        "poll": f"/api/gen/status/{job_id}",
    }), 202

@app.route("/api/gen/status/<job_id>", methods=["GET"])
def api_gen_status(job_id):
    if not _auth_ok():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    with _gen_lock:
        s = _gen_results.get(job_id)
    if not s:
        return jsonify({"ok": False, "error": "job not found"}), 404
    return jsonify({
        "ok": True, "job_id": job_id,
        "status": s.get("status"),
        "accounts": s.get("accounts", []),
        "count": s.get("count", 0),
        "error": s.get("error"),
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    print(f"🔥 YUTA GEN API · port {port}")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
