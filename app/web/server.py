import os
import threading
import time

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

from app import paths

load_dotenv(os.path.join(paths.BASE_DIR, ".env"))

from app.adspower.client import AdsPowerError, list_profiles
from app.adspower.driver import close_driver, force_close_driver, open_driver
from app.automations import cnpj_list, pause, run_log, tracker
from app.automations.orchestrator import (
    DEFAULT_PROFILE,
    SINGLE_STEP_RUNNERS,
    SLOTS,
    run_facebook_login_only,
    run_for_next_pending_cnpj,
    run_single_step,
)

app = Flask(__name__, template_folder="templates", static_folder="static")

PILOT_PROFILE_ID = "k1eqaqk4"


def _welcome_name() -> str:
    """Nome exibido na tela de boas-vindas — usa a parte antes do @ do e-mail do
    Buildfy configurado no .env, já que não há um campo de nome dedicado."""
    email = os.environ.get("BUILDFY_EMAIL", "")
    return email.split("@")[0] if email else "usuário"


def _slot() -> str:
    """Slot da requisição — vem do query string (GET) ou do corpo JSON (POST),
    default 'A' pra manter compatibilidade com chamadas antigas sem slot."""
    if request.method == "GET":
        return (request.args.get("slot") or "A").strip() or "A"
    body = request.get_json(silent=True) or {}
    return (body.get("slot") or "A").strip() or "A"


@app.route("/")
def index():
    return render_template(
        "index.html", pilot_profile_id=PILOT_PROFILE_ID, slots=SLOTS, welcome_name=_welcome_name()
    )


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
    slot = _slot()
    if run_log.get_state(slot)["running"]:
        return jsonify({"ok": False, "error": f"Já existe uma execução em andamento no robô {slot}"}), 409

    body = request.get_json(silent=True) or {}
    requested_cnpj = (body.get("cnpj") or "").strip() or None
    profile_id = (body.get("profile_id") or "").strip() or DEFAULT_PROFILE

    def _run():
        try:
            run_for_next_pending_cnpj(requested_cnpj=requested_cnpj, profile_id=profile_id, slot=slot)
        except Exception:
            pass  # já registrado no run_log pelo orquestrador

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return jsonify({"ok": True})


@app.route("/api/automation/facebook-login", methods=["POST"])
def automation_facebook_login():
    """Processo independente de CNPJ: só abre o perfil e garante login no
    Facebook (2FA se pedido). Depois disso, 'continuar processo' já encontra
    a sessão logada e pula direto para a criação do site."""
    slot = _slot()
    if run_log.get_state(slot)["running"]:
        return jsonify({"ok": False, "error": f"Já existe uma execução em andamento no robô {slot}"}), 409

    body = request.get_json(silent=True) or {}
    profile_id = (body.get("profile_id") or "").strip() or DEFAULT_PROFILE

    def _run():
        try:
            run_facebook_login_only(profile_id=profile_id, slot=slot)
        except Exception:
            pass  # já registrado no run_log pelo orquestrador

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return jsonify({"ok": True})


@app.route("/api/automation/run-step", methods=["POST"])
def automation_run_step():
    slot = _slot()
    if run_log.get_state(slot)["running"]:
        return jsonify({"ok": False, "error": f"Já existe uma execução em andamento no robô {slot}"}), 409

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
            run_single_step(cnpj, step, profile_id=profile_id, slot=slot)
        except Exception:
            pass  # já registrado no run_log

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return jsonify({"ok": True})


@app.route("/api/cnpjs")
def api_cnpjs():
    try:
        # exclui da lista os CNPJs sendo processados agora por OUTRO robô — o
        # slot que está chamando continua vendo o que ele mesmo está rodando
        items = cnpj_list.list_cnpjs_with_status(exclude_slot=_slot())
        return jsonify({"ok": True, "items": items})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/cnpjs-avulsos", methods=["GET", "POST"])
def api_cnpjs_avulsos():
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        cnpj = (body.get("cnpj") or "").strip()
        if len(cnpj) != 14 or not cnpj.isdigit():
            return jsonify({"ok": False, "error": "CNPJ inválido — precisa ter 14 dígitos."}), 400
        cnpj_list.add_avulso(cnpj)
        return jsonify({"ok": True})

    try:
        items = cnpj_list.list_avulsos_with_status(exclude_slot=_slot())
        return jsonify({"ok": True, "items": items})
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


_PROFILES_CACHE_TTL = 30.0  # segundos — sucesso fica em cache por esse tempo
_PROFILES_ERROR_RETRY_AFTER = 5.0  # segundos — erro (ex: rate limit) tenta de novo mais cedo
_profiles_cache_lock = threading.Lock()
_profiles_cache: dict[str, object] = {"items": None, "error": None, "fetched_at": 0.0}


@app.route("/api/adspower/profiles")
def api_adspower_profiles():
    # a API local do AdsPower tem rate limit ("Too many request per second") e
    # com 3 robôs cada um pollando o dashboard periodicamente as chamadas somam
    # rápido — cacheia o resultado e serve o mesmo pra todos os slots em vez de
    # bater na API do AdsPower a cada requisição do frontend. O lock também
    # serializa chamadas concorrentes: só uma de fato vai à rede por vez, as
    # outras esperam e reaproveitam o resultado que acabou de chegar.
    with _profiles_cache_lock:
        age = time.monotonic() - _profiles_cache["fetched_at"]
        ttl = _PROFILES_ERROR_RETRY_AFTER if _profiles_cache["error"] else _PROFILES_CACHE_TTL
        if age < ttl and (_profiles_cache["items"] is not None or _profiles_cache["error"]):
            if _profiles_cache["error"]:
                return jsonify({"ok": False, "error": _profiles_cache["error"]}), 400
            return jsonify({"ok": True, "items": _profiles_cache["items"]})

        try:
            items = list_profiles()
            _profiles_cache.update(items=items, error=None, fetched_at=time.monotonic())
            return jsonify({"ok": True, "items": items})
        except AdsPowerError as e:
            _profiles_cache.update(error=str(e), fetched_at=time.monotonic())
            return jsonify({"ok": False, "error": str(e)}), 400


@app.route("/api/automation/status")
def automation_status():
    slot = _slot()
    state = run_log.get_state(slot)
    state["manual_step"] = pause.get_status(slot)
    return jsonify(state)


@app.route("/api/automation/resume", methods=["POST"])
def automation_resume():
    pause.resume(_slot())
    return jsonify({"ok": True})


@app.route("/api/automation/pause", methods=["POST"])
def automation_pause():
    slot = _slot()
    run_log.request_pause(slot)
    # se estiver parado numa pausa manual (aguardando "Continuar"), libera para
    # que o check_pause() seguinte capture o pedido de pausa
    pause.resume(slot)
    return jsonify({"ok": True})


@app.route("/api/automation/close-browser", methods=["POST"])
def automation_close_browser():
    slot = _slot()
    if run_log.get_state(slot)["running"]:
        return jsonify({"ok": False, "error": "Pare a execução em andamento antes de fechar o navegador"}), 409

    body = request.get_json(silent=True) or {}
    profile_id = (body.get("profile_id") or "").strip() or DEFAULT_PROFILE
    force_close_driver(profile_id)
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(debug=True, port=5051, use_reloader=False, threaded=True)
