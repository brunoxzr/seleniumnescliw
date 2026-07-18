import threading

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

load_dotenv()

from app.adspower.client import AdsPowerError, list_profiles
from app.adspower.driver import close_driver, force_close_driver, open_driver
from app.automations import cnpj_list, pause, run_log, tracker
from app.automations.orchestrator import (
    DEFAULT_PROFILE,
    SINGLE_STEP_RUNNERS,
    run_for_next_pending_cnpj,
    run_single_step,
)

app = Flask(__name__, template_folder="templates", static_folder="static")

PILOT_PROFILE_ID = "k1eqaqk4"

_run_lock = threading.Lock()


@app.route("/")
def index():
    return render_template("index.html", pilot_profile_id=PILOT_PROFILE_ID)


@app.route("/api/test-open", methods=["POST"])
def test_open():
    profile_id = request.json.get("profile_id", PILOT_PROFILE_ID)
    driver = None
    try:
        driver = open_driver(profile_id)
        title = driver.title
        url = driver.current_url
        return jsonify({"ok": True, "title": title, "url": url})
    except AdsPowerError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    finally:
        if driver is not None:
            close_driver(driver, profile_id)


@app.route("/api/automation/start", methods=["POST"])
def automation_start():
    if run_log.get_state()["running"]:
        return jsonify({"ok": False, "error": "Já existe uma execução em andamento"}), 409

    body = request.get_json(silent=True) or {}
    requested_cnpj = (body.get("cnpj") or "").strip() or None
    profile_id = (body.get("profile_id") or "").strip() or DEFAULT_PROFILE

    def _run():
        try:
            run_for_next_pending_cnpj(requested_cnpj=requested_cnpj, profile_id=profile_id)
        except Exception:
            pass  # já registrado no run_log pelo orquestrador

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return jsonify({"ok": True})


@app.route("/api/automation/run-step", methods=["POST"])
def automation_run_step():
    if run_log.get_state()["running"]:
        return jsonify({"ok": False, "error": "Já existe uma execução em andamento"}), 409

    body = request.get_json(silent=True) or {}
    cnpj = (body.get("cnpj") or "").strip()
    step = (body.get("step") or "").strip()
    profile_id = (body.get("profile_id") or "").strip() or DEFAULT_PROFILE

    if not cnpj:
        return jsonify({"ok": False, "error": "cnpj é obrigatório"}), 400
    if step not in SINGLE_STEP_RUNNERS:
        return jsonify({"ok": False, "error": f"Etapa inválida: {step}"}), 400

    def _run():
        try:
            run_single_step(cnpj, step, profile_id=profile_id)
        except Exception:
            pass  # já registrado no run_log

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return jsonify({"ok": True})


@app.route("/api/cnpjs")
def api_cnpjs():
    try:
        return jsonify({"ok": True, "items": cnpj_list.list_cnpjs_with_status()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/cnpj/<cnpj>/record")
def api_cnpj_record(cnpj):
    record = tracker.get_record(cnpj)
    return jsonify({
        "ok": True,
        "record": record or {"steps_done": [], "data": {}, "status": None},
        "all_steps": tracker.STEPS,
    })


@app.route("/api/cnpj/<cnpj>/checkpoint", methods=["POST"])
def api_cnpj_set_checkpoint(cnpj):
    body = request.get_json(silent=True) or {}
    step = (body.get("step") or "").strip()
    extra_data = body.get("data") or {}

    if step not in tracker.STEPS:
        return jsonify({"ok": False, "error": f"Etapa inválida: {step}"}), 400

    tracker.save_checkpoint(cnpj, step, extra_data)
    return jsonify({"ok": True, "record": tracker.get_record(cnpj)})


@app.route("/api/cnpj/<cnpj>/unmark-step", methods=["POST"])
def api_cnpj_unmark_step(cnpj):
    """Remove uma etapa da lista de concluídas (força reexecução dela)."""
    body = request.get_json(silent=True) or {}
    step = (body.get("step") or "").strip()

    record = tracker.get_record(cnpj)
    if not record:
        return jsonify({"ok": False, "error": "CNPJ sem registro"}), 404

    if step in record["steps_done"]:
        record["steps_done"].remove(step)
        # recua o status para a última etapa que ainda restou
        record["status"] = record["steps_done"][-1] if record["steps_done"] else None
        all_data = tracker._load()
        all_data[cnpj] = record
        tracker._save(all_data)

    return jsonify({"ok": True, "record": tracker.get_record(cnpj)})


@app.route("/api/profile/<profile_id>/cnpj")
def api_profile_cnpj(profile_id):
    """CNPJ mais recentemente associado a este perfil AdsPower, se houver —
    usado pelo dashboard para pré-selecionar o CNPJ ao trocar de perfil."""
    return jsonify({"ok": True, "cnpj": tracker.get_cnpj_by_profile_id(profile_id)})


@app.route("/api/adspower/profiles")
def api_adspower_profiles():
    try:
        return jsonify({"ok": True, "items": list_profiles()})
    except AdsPowerError as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route("/api/automation/status")
def automation_status():
    state = run_log.get_state()
    state["manual_step"] = pause.get_status()
    return jsonify(state)


@app.route("/api/automation/resume", methods=["POST"])
def automation_resume():
    pause.resume()
    return jsonify({"ok": True})


@app.route("/api/automation/pause", methods=["POST"])
def automation_pause():
    run_log.request_pause()
    # se estiver parado numa pausa manual (aguardando "Continuar"), libera para
    # que o check_pause() seguinte capture o pedido de pausa
    pause.resume()
    return jsonify({"ok": True})


@app.route("/api/automation/close-browser", methods=["POST"])
def automation_close_browser():
    if run_log.get_state()["running"]:
        return jsonify({"ok": False, "error": "Pare a execução em andamento antes de fechar o navegador"}), 409

    body = request.get_json(silent=True) or {}
    profile_id = (body.get("profile_id") or "").strip() or DEFAULT_PROFILE
    force_close_driver(profile_id)
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(debug=True, port=5050, use_reloader=False)
