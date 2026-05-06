"""
KubeDDoS Attack Workflow — Frontend Application

Flask + SocketIO application for managing and monitoring DDoS attack simulations.
Provides endpoint discovery, strategy configuration, attack execution, and real-time
metrics monitoring through a web interface.
"""

import json
import os
import re
import subprocess
import signal
import sys
import threading
import time
import yaml
from datetime import datetime
from pathlib import Path

from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO

# Add parent to path for config
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import AttackConfig

# ── App Init ───────────────────────────────────────────────────────
config = AttackConfig()

app = Flask(__name__)
app.config["SECRET_KEY"] = config.secret_key
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# ── State ──────────────────────────────────────────────────────────
active_processes: dict = {}  # id -> {process, config, start_time, metrics}
discovery_result: dict = {}  # Latest discovery result
attack_history: list = []    # Completed attacks

# Experiment state
PROJECT_ROOT = str(Path(__file__).parent.parent.parent)
_exp_lock = threading.Lock()
_exp: dict = {
    "running": False,
    "process": None,
    "logs": [],
    "phase": "idle",
    "started_at": None,
    "results_dir": None,
    "config": {},
    "return_code": None,
}


# ── Helpers ────────────────────────────────────────────────────────

def load_strategies():
    """Load all attack strategy YAML files."""
    strategies = []
    strategies_dir = Path(config.configs_dir) / "attack-strategies"
    if strategies_dir.exists():
        for f in sorted(strategies_dir.glob("*.yaml")):
            try:
                with open(f) as fh:
                    data = yaml.safe_load(fh)
                    data["_filename"] = f.name
                    strategies.append(data)
            except Exception as e:
                strategies.append({"_filename": f.name, "_error": str(e)})
    return strategies


def load_target_adapters():
    """Load all target adapter YAML files."""
    adapters = []
    adapters_dir = Path(config.configs_dir) / "target-adapters"
    if adapters_dir.exists():
        for f in sorted(adapters_dir.glob("*.yaml")):
            try:
                with open(f) as fh:
                    data = yaml.safe_load(fh)
                    data["_filename"] = f.name
                    adapters.append(data)
            except Exception as e:
                adapters.append({"_filename": f.name, "_error": str(e)})
    return adapters


def load_discovered_endpoints():
    """Load the latest discovered endpoints file."""
    fp = Path(config.attacks_dir) / config.discovery_file
    if fp.exists():
        with open(fp) as f:
            return json.load(f)
    return None


def get_process_status(proc_id):
    """Get status of an attack process."""
    if proc_id not in active_processes:
        return None
    entry = active_processes[proc_id]
    proc = entry["process"]
    elapsed = time.time() - entry["start_time"]
    return {
        "id": proc_id,
        "running": proc.poll() is None,
        "return_code": proc.returncode,
        "elapsed_seconds": round(elapsed, 1),
        "config": entry["config"],
        "started_at": entry["started_at_iso"],
    }


# ── Page Routes ────────────────────────────────────────────────────

@app.route("/")
def index():
    """Attack dashboard — overview of capabilities and status."""
    return render_template("index.html", config=config)


@app.route("/discovery")
def discovery_page():
    """Endpoint discovery page."""
    return render_template("discovery.html", config=config)


@app.route("/strategies")
def strategies_page():
    """Attack strategy configuration page."""
    return render_template("strategies.html", config=config)


@app.route("/execute")
def execute_page():
    """Attack execution and monitoring page."""
    return render_template("execute.html", config=config)


@app.route("/results")
def results_page():
    """Attack results and history page."""
    return render_template("results.html", config=config)


@app.route("/experiment")
def experiment_page():
    """Experiment runner — control 4-scenario DDoS comparison tests."""
    return render_template("experiment.html", config=config)


# ── API Routes ─────────────────────────────────────────────────────

@app.route("/api/health")
def api_health():
    running = sum(1 for e in active_processes.values() if e["process"].poll() is None)
    return jsonify({
        "status": "healthy",
        "active_attacks": running,
        "total_attacks": len(attack_history),
        "target_url": config.target_url,
    })


# -- Discovery API --

@app.route("/api/discovery/run", methods=["POST"])
def api_discovery_run():
    """Run endpoint discovery against the target."""
    global discovery_result
    body = request.get_json(silent=True) or {}
    target = body.get("target_url", config.target_url)
    max_depth = body.get("max_depth", config.discovery_max_depth)
    timeout = body.get("timeout", config.discovery_timeout)

    script = Path(config.attacks_dir) / "endpoint-discovery.py"
    if not script.exists():
        return jsonify({"error": "endpoint-discovery.py not found"}), 404

    output_file = Path(config.attacks_dir) / "discovered-endpoints-latest.json"
    cmd = [
        sys.executable, str(script),
        "--url", target,
        "--max-depth", str(max_depth),
        "--timeout", str(timeout),
        "--output", str(output_file),
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if output_file.exists():
            with open(output_file) as f:
                discovery_result = json.load(f)
            return jsonify({
                "status": "completed",
                "endpoints_found": len(discovery_result.get("endpoints", [])),
                "result": discovery_result,
                "stdout": result.stdout[-2000:] if result.stdout else "",
            })
        else:
            return jsonify({
                "status": "failed",
                "stderr": result.stderr[-2000:] if result.stderr else "",
                "returncode": result.returncode,
            }), 500
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Discovery timed out"}), 504
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/discovery/endpoints")
def api_discovery_endpoints():
    """Get the latest discovered endpoints."""
    endpoints = load_discovered_endpoints()
    if endpoints:
        return jsonify(endpoints)
    if discovery_result:
        return jsonify(discovery_result)
    return jsonify({"endpoints": [], "message": "No discovery run yet"})


# -- Strategy API --

@app.route("/api/strategies")
def api_strategies():
    """List all available attack strategies."""
    return jsonify({"strategies": load_strategies()})


@app.route("/api/strategies/<filename>")
def api_strategy_detail(filename):
    """Get details of a specific strategy."""
    fp = Path(config.configs_dir) / "attack-strategies" / filename
    if not fp.exists():
        return jsonify({"error": "Strategy not found"}), 404
    with open(fp) as f:
        return jsonify(yaml.safe_load(f))


@app.route("/api/adapters")
def api_adapters():
    """List all target adapters."""
    return jsonify({"adapters": load_target_adapters()})


# -- Attack Execution API --

@app.route("/api/attack/launch", methods=["POST"])
def api_attack_launch():
    """Launch an attack with the given configuration."""
    body = request.get_json(silent=True) or {}

    attack_type = body.get("type", "app_level")
    strategy_file = body.get("strategy")
    target_url = body.get("target_url", config.target_url)
    duration = body.get("duration", config.default_duration)
    workers = body.get("workers", config.default_workers)
    mode = body.get("mode", config.default_mode)
    pattern = body.get("pattern", config.default_pattern)

    # Determine which script to use
    if attack_type == "orchestrated" and strategy_file:
        script = Path(config.attacks_dir) / "orchestrator.py"
        cmd = [
            sys.executable, str(script),
            "--config", str(Path(config.configs_dir) / "attack-strategies" / strategy_file),
            "--duration", str(duration),
        ]
    elif attack_type == "network_level":
        script = Path(config.attacks_dir) / "network_crossfire_enhanced.py"
        cmd = [
            sys.executable, str(script),
            "--url", target_url,
            "--duration", str(duration),
            "--workers", str(workers),
            "--mode", mode,
            "--pattern", pattern,
        ]
    else:
        script = Path(config.attacks_dir) / "crossfire_enhanced.py"
        cmd = [
            sys.executable, str(script),
            "--url", target_url,
            "--duration", str(duration),
            "--workers", str(workers),
            "--mode", mode,
            "--pattern", pattern,
        ]

    if not script.exists():
        return jsonify({"error": f"Script {script.name} not found"}), 404

    # Launch subprocess
    proc_id = f"attack-{int(time.time())}-{len(active_processes)}"
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=config.attacks_dir,
        )
        active_processes[proc_id] = {
            "process": proc,
            "config": {
                "type": attack_type,
                "target_url": target_url,
                "duration": duration,
                "workers": workers,
                "mode": mode,
                "pattern": pattern,
                "strategy": strategy_file,
                "script": script.name,
            },
            "start_time": time.time(),
            "started_at_iso": datetime.utcnow().isoformat() + "Z",
            "output_lines": [],
        }

        # Background thread to collect output
        def _collect(pid, p):
            for line in p.stdout:
                if pid in active_processes:
                    active_processes[pid]["output_lines"].append(line.rstrip())
                    # Keep only last 500 lines
                    if len(active_processes[pid]["output_lines"]) > 500:
                        active_processes[pid]["output_lines"] = active_processes[pid]["output_lines"][-500:]
                    socketio.emit("attack_output", {"id": pid, "line": line.rstrip()})

        t = threading.Thread(target=_collect, args=(proc_id, proc), daemon=True)
        t.start()

        return jsonify({"id": proc_id, "status": "launched", "config": active_processes[proc_id]["config"]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/attack/stop", methods=["POST"])
def api_attack_stop():
    """Stop a running attack."""
    body = request.get_json(silent=True) or {}
    proc_id = body.get("id")
    if not proc_id or proc_id not in active_processes:
        return jsonify({"error": "Unknown attack id"}), 404

    entry = active_processes[proc_id]
    proc = entry["process"]
    if proc.poll() is None:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    status = get_process_status(proc_id)
    attack_history.append(status)
    return jsonify({"status": "stopped", "detail": status})


@app.route("/api/attack/status")
def api_attack_status():
    """Get status of all active attacks."""
    statuses = []
    for pid in list(active_processes):
        s = get_process_status(pid)
        if s:
            statuses.append(s)
    return jsonify({"attacks": statuses})


@app.route("/api/attack/status/<proc_id>")
def api_attack_status_detail(proc_id):
    """Get detailed status of a specific attack."""
    s = get_process_status(proc_id)
    if not s:
        return jsonify({"error": "Unknown attack id"}), 404
    s["output"] = active_processes[proc_id].get("output_lines", [])[-50:]
    return jsonify(s)


@app.route("/api/attack/history")
def api_attack_history():
    """Get history of completed attacks."""
    return jsonify({"history": attack_history})


# -- Experiment Runner API --

def _ansi_strip(text: str) -> str:
    return re.sub(r'\x1b\[[0-9;]*m', '', text)


def _stream_experiment(proc):
    """Background thread: stream experiment stdout via WebSocket and parse markers."""
    PHASE_MAP = {
        "###PHASE:baseline###": "baseline",
        "###PHASE:native###": "native",
        "###PHASE:nephio###": "nephio",
        "###PHASE:nephio_integrated###": "nephio_integrated",
    }
    for raw in proc.stdout:
        line = _ansi_strip(raw.rstrip())
        with _exp_lock:
            _exp["logs"].append(line)
            if len(_exp["logs"]) > 3000:
                _exp["logs"] = _exp["logs"][-3000:]
            for marker, phase in PHASE_MAP.items():
                if marker in line or marker in raw:
                    _exp["phase"] = phase
            if "###COMPLETE:" in line:
                rdir = line.split("###COMPLETE:")[-1].strip().strip("#")
                _exp["results_dir"] = rdir
        socketio.emit("experiment_log", {"line": line, "phase": _exp.get("phase", "")})

    proc.wait()
    with _exp_lock:
        _exp["running"] = False
        _exp["return_code"] = proc.returncode
        _exp["phase"] = "complete" if proc.returncode == 0 else "error"
    socketio.emit("experiment_done", {
        "returncode": proc.returncode,
        "results_dir": _exp.get("results_dir"),
        "phase": _exp["phase"],
    })


@app.route("/api/experiment/run", methods=["POST"])
def api_experiment_run():
    """Start the 4-scenario mitigation comparison experiment."""
    with _exp_lock:
        if _exp["running"]:
            return jsonify({"error": "Experiment already running"}), 409

    body = request.get_json(silent=True) or {}
    duration = int(body.get("duration", 120))
    workers = int(body.get("workers", 80))
    rate = int(body.get("rate", 20))
    bg_workers = int(body.get("bg_workers", 15))
    bg_rate = int(body.get("bg_rate", 3))

    script = Path(PROJECT_ROOT) / "scripts" / "workflows" / "quick-mitigation-comparison.sh"
    if not script.exists():
        return jsonify({"error": f"Script not found: {script}"}), 404

    env = os.environ.copy()
    env.update({
        "ATTACK_DURATION": str(duration),
        "ATTACK_WORKERS": str(workers),
        "ATTACK_RATE": str(rate),
        "BACKGROUND_WORKERS": str(bg_workers),
        "BACKGROUND_RATE": str(bg_rate),
        "PROMETHEUS_URL": os.environ.get("PROMETHEUS_URL", "http://localhost:9090"),
    })

    try:
        proc = subprocess.Popen(
            ["bash", str(script)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=PROJECT_ROOT,
            env=env,
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    with _exp_lock:
        _exp["running"] = True
        _exp["process"] = proc
        _exp["logs"] = []
        _exp["phase"] = "starting"
        _exp["started_at"] = datetime.utcnow().isoformat() + "Z"
        _exp["results_dir"] = None
        _exp["return_code"] = None
        _exp["config"] = {
            "duration": duration, "workers": workers, "rate": rate,
            "bg_workers": bg_workers, "bg_rate": bg_rate,
        }

    t = threading.Thread(target=_stream_experiment, args=(proc,), daemon=True)
    t.start()
    return jsonify({"status": "started", "config": _exp["config"]})


@app.route("/api/experiment/stop", methods=["POST"])
def api_experiment_stop():
    """Terminate a running experiment."""
    with _exp_lock:
        proc = _exp.get("process")
        if not proc or not _exp["running"]:
            return jsonify({"error": "No experiment running"}), 404
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    with _exp_lock:
        _exp["running"] = False
        _exp["phase"] = "stopped"
    return jsonify({"status": "stopped"})


@app.route("/api/experiment/status")
def api_experiment_status():
    """Get current experiment status."""
    with _exp_lock:
        return jsonify({
            "running": _exp["running"],
            "phase": _exp["phase"],
            "started_at": _exp["started_at"],
            "results_dir": _exp["results_dir"],
            "return_code": _exp["return_code"],
            "log_lines": len(_exp["logs"]),
            "config": _exp.get("config", {}),
        })


@app.route("/api/experiment/logs")
def api_experiment_logs():
    """Get buffered experiment logs with optional offset for polling."""
    offset = max(0, int(request.args.get("offset", 0)))
    with _exp_lock:
        lines = _exp["logs"][offset:]
        total = len(_exp["logs"])
    return jsonify({"lines": lines, "total": total + offset, "offset": offset + len(lines)})


@app.route("/api/experiment/results")
def api_experiment_results_list():
    """List all saved experiment result directories."""
    results_base = Path(PROJECT_ROOT) / "results" / "experiments"
    if not results_base.exists():
        return jsonify({"results": []})
    dirs = sorted(
        [d.name for d in results_base.iterdir()
         if d.is_dir() and (d / "summary.md").exists()],
        reverse=True,
    )
    return jsonify({"results": dirs})


@app.route("/api/experiment/results/<name>")
def api_experiment_results_detail(name):
    """Return parsed metrics and summary for a specific experiment run."""
    # Prevent path traversal
    if "/" in name or ".." in name or not name.replace("-", "").replace("_", "").isalnum():
        return jsonify({"error": "Invalid result name"}), 400
    results_dir = Path(PROJECT_ROOT) / "results" / "experiments" / name
    if not results_dir.exists():
        return jsonify({"error": "Not found"}), 404

    metrics: dict = {}
    for scenario in ("baseline", "native", "nephio", "nephio_integrated"):
        for phase in ("pre", "during", "post"):
            f = results_dir / f"metrics-{phase}-{scenario}.json"
            if f.exists():
                try:
                    with open(f) as fp:
                        metrics[f"{scenario}_{phase}"] = json.load(fp)
                except Exception:
                    pass

    summary_file = results_dir / "summary.md"
    summary_text = summary_file.read_text() if summary_file.exists() else ""
    return jsonify({"name": name, "summary": summary_text, "metrics": metrics})


@app.route("/api/experiment/health")
def api_experiment_health():
    """Quick health check: cluster, Sock Shop pods, Prometheus."""
    import requests as req
    checks: dict = {}

    # K8s cluster
    try:
        r = subprocess.run(
            ["kubectl", "cluster-info"], capture_output=True, text=True, timeout=5
        )
        checks["cluster"] = "ok" if r.returncode == 0 else "error"
    except Exception:
        checks["cluster"] = "error"

    # Sock Shop pods
    try:
        r = subprocess.run(
            ["kubectl", "get", "pods", "-n", "sock-shop", "--no-headers"],
            capture_output=True, text=True, timeout=5,
        )
        lines = [l for l in r.stdout.splitlines() if l.strip()]
        running = sum(1 for l in lines if "Running" in l)
        checks["sock_shop"] = {"running": running, "total": len(lines)}
        checks["sock_shop_ok"] = running >= 8
    except Exception:
        checks["sock_shop"] = "error"
        checks["sock_shop_ok"] = False

    # Prometheus
    prom_url = os.environ.get("PROMETHEUS_URL", "http://localhost:9090")
    try:
        r = req.get(f"{prom_url}/-/healthy", timeout=3)
        checks["prometheus"] = "ok" if r.status_code == 200 else "degraded"
    except Exception:
        checks["prometheus"] = "error"

    healthy = (
        checks["cluster"] == "ok"
        and checks.get("sock_shop_ok", False)
        and checks["prometheus"] in ("ok", "degraded")
    )
    return jsonify({"healthy": healthy, "checks": checks})


# -- Metrics API (proxy to Prometheus) --

@app.route("/api/metrics/target")
def api_metrics_target():
    """Get target service metrics from Prometheus."""
    import requests as req
    query = request.args.get("query", 'rate(http_requests_total[1m])')
    try:
        r = req.get(
            f"{config.prometheus_url}/api/v1/query",
            params={"query": query},
            timeout=10,
        )
        return jsonify(r.json())
    except Exception as e:
        return jsonify({"error": str(e)}), 502


# ── WebSocket Events ───────────────────────────────────────────────

@socketio.on("connect")
def handle_connect():
    """Client connected — send current state."""
    statuses = []
    for pid in active_processes:
        s = get_process_status(pid)
        if s:
            statuses.append(s)
    socketio.emit("state_sync", {"attacks": statuses})


def _background_emitter():
    """Periodically emit attack status updates."""
    while True:
        socketio.sleep(5)
        statuses = []
        finished = []
        for pid in list(active_processes):
            s = get_process_status(pid)
            if s:
                statuses.append(s)
                if not s["running"]:
                    finished.append(pid)

        if statuses:
            socketio.emit("attack_status_update", {"attacks": statuses})

        # Archive finished attacks
        for pid in finished:
            s = get_process_status(pid)
            if s:
                attack_history.append(s)
            del active_processes[pid]


# ── Main ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    socketio.start_background_task(_background_emitter)
    socketio.run(app, host="0.0.0.0", port=config.frontend_port, debug=False, allow_unsafe_werkzeug=True)
