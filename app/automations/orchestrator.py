"""Orquestra o fluxo completo: Buildfy -> Facebook Business, com log, checkpoints e pausas manuais.

Cada etapa salva um checkpoint no tracker. Se a execução cair no meio, uma nova
chamada a `run_for_next_pending_cnpj` retoma o mesmo CNPJ a partir da última
etapa concluída, em vez de recomeçar do zero.
"""
import re

from app.adspower.driver import close_driver, open_driver
from . import (
    buildfy,
    facebook_business_info,
    facebook_business_verification,
    facebook_domain,
    facebook_language,
    facebook_login,
    facebook_pages,
    facebook_whatsapp,
    run_log,
    tracker,
)
from .buildfy_email import confirm_business_email
from .create_business_manager import create_business_manager
from .pause import wait_for_manual_step

DEFAULT_PROFILE = "k1eqapx8"


def _run_step_with_fallback(step_fn, step_name: str, max_retries: int = 1):
    """Executa uma etapa; se falhar com um erro inesperado (ex: tela nova/não
    mapeada, elemento não encontrado), pausa pedindo intervenção manual em vez de
    abortar o fluxo inteiro. Ao retomar, tenta a etapa de novo automaticamente —
    a ideia é que o usuário resolva manualmente na tela (clique, preenchimento,
    fechar um popup inesperado) e a automação prossiga sozinha depois disso.
    """
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            return step_fn()
        except run_log.PausedByUser:
            raise  # pausa explícita do usuário não deve virar retry automático
        except Exception as e:
            last_error = e
            if attempt >= max_retries:
                break
            run_log.add(
                f"Erro inesperado em '{step_name}' [{type(e).__name__}]: {e}. "
                "Resolva manualmente na tela do navegador e clique Continuar para tentar de novo.",
                level="manual",
            )
            wait_for_manual_step(f"Resolver problema em '{step_name}' e clicar Continuar")
            run_log.add(f"Retomando '{step_name}' após intervenção manual")
    raise last_error


def _strip_cnpj_prefix(empresa: str) -> str:
    """Remove o prefixo de CNPJ formatado (ex: '68.078.890 ') do nome da empresa."""
    return re.sub(r"^\d{2}\.\d{3}\.\d{3}\s+", "", empresa).strip()


def _pick_cnpj_and_site_id(driver, requested_cnpj: str | None) -> tuple[str, str] | None:
    """Escolhe qual CNPJ processar:

    1. Se um CNPJ específico foi pedido (escolhido no dashboard):
       - usa o site já existente no Buildfy, se houver;
       - senão, cria um site novo no Buildfy a partir do CNPJ (o Buildfy consulta
         os dados da empresa sozinho, só precisa do número).
    2. Senão, retoma um CNPJ em progresso, se houver.
    3. Senão, pega o próximo pendente do Buildfy.
    """
    if requested_cnpj:
        record = tracker.get_record(requested_cnpj)
        if record and record["data"].get("site_id"):
            return requested_cnpj, record["data"]["site_id"]

        # busca o site_id na lista do Buildfy (CNPJ ainda sem checkpoint local)
        candidates = buildfy.list_pending_sites(driver, already_processed=set(), limit=200)
        for cnpj, site_id in candidates:
            if cnpj == requested_cnpj:
                return cnpj, site_id

        # CNPJ ainda não tem site — cria um novo no Buildfy (consome 1 crédito)
        run_log.add(f"CNPJ {requested_cnpj} não tem site no Buildfy — criando um novo...")
        site_id = buildfy.create_site_from_cnpj(driver, requested_cnpj)
        run_log.add(f"Site criado no Buildfy (site_id={site_id})")
        return requested_cnpj, site_id

    in_progress_cnpj = tracker.get_in_progress_cnpj()
    if in_progress_cnpj:
        record = tracker.get_record(in_progress_cnpj)
        site_id = record["data"].get("site_id")
        if site_id:
            run_log.add(f"Retomando CNPJ {in_progress_cnpj} a partir da etapa '{record['status']}'")
            return in_progress_cnpj, site_id

    already = tracker.get_all_processed_cnpjs()
    pending = buildfy.list_pending_sites(driver, already_processed=already, limit=1)
    if not pending:
        return None
    return pending[0]


def _require_saved(saved: dict, *keys: str, step_name: str) -> None:
    missing = [k for k in keys if not saved.get(k)]
    if missing:
        raise RuntimeError(
            f"Não é possível rodar '{step_name}' isoladamente: faltam dados salvos "
            f"({', '.join(missing)}). Rode as etapas anteriores primeiro (ou o fluxo completo)."
        )


# cada função abaixo executa UMA etapa do fluxo, assumindo que os pré-requisitos
# (checkpoints anteriores) já existem. Usadas tanto pelo fluxo completo (via
# run_for_next_pending_cnpj) quanto pela execução avulsa (via run_single_step).

def _step_site_data(driver, cnpj: str, site_id: str) -> None:
    site_data = buildfy.get_site_data(driver, site_id, cnpj)
    run_log.add(f"Dados do site obtidos: {site_data.empresa} / {site_data.dominio}")
    tracker.save_checkpoint(cnpj, "site_data_obtida", {
        "empresa": site_data.empresa, "dominio": site_data.dominio, "url": site_data.url,
        "logradouro": site_data.logradouro, "complemento": site_data.complemento,
        "bairro": site_data.bairro,
        "cidade": site_data.cidade, "estado": site_data.estado, "cep": site_data.cep,
    })


def _step_business_manager(driver, profile_id: str, cnpj: str, saved: dict) -> None:
    _require_saved(saved, "empresa", "dominio", step_name="Criar Business Manager")
    business_name = _strip_cnpj_prefix(saved["empresa"])
    business_email = f"{saved['dominio'].split('.')[0]}@{'.'.join(saved['dominio'].split('.')[1:])}"

    def _login_manual_step(message: str) -> None:
        run_log.add(message, level="manual")
        wait_for_manual_step(message)
        run_log.add("Confirmado: 2FA concluído manualmente")

    _run_step_with_fallback(
        lambda: facebook_login.ensure_logged_in(driver, profile_id, on_manual_step=_login_manual_step),
        "Login no Facebook",
    )
    run_log.add("Login no Facebook confirmado")

    def _bm_manual_step(message: str) -> None:
        run_log.add(message, level="manual")
        wait_for_manual_step(message)
        run_log.add("Confirmado: Submit do Business Manager clicado manualmente")

    business_id = create_business_manager(
        driver, business_name, business_name, business_email, on_manual_step=_bm_manual_step
    )
    run_log.add(f"Business Manager criado: {business_id}")
    tracker.save_checkpoint(cnpj, "business_manager_criado", {"business_id": business_id})


def _step_confirm_email(driver, profile_id: str, cnpj: str, site_id: str, saved: dict) -> None:
    business_id = saved.get("business_id")
    _require_saved(saved, "business_id", step_name="Confirmar e-mail do Business Manager")
    _run_step_with_fallback(
        lambda: facebook_login.ensure_logged_in(driver, profile_id), "Login no Facebook"
    )
    email_business_id = confirm_business_email(driver, site_id)
    if email_business_id and email_business_id != business_id:
        run_log.add(
            f"Aviso: business_id do e-mail ({email_business_id}) difere do salvo ({business_id})",
            level="warning",
        )
    run_log.add("E-mail do Business Manager confirmado")
    tracker.save_checkpoint(cnpj, "email_confirmado")


def _step_pages(driver, profile_id: str, cnpj: str, saved: dict) -> None:
    business_id = saved.get("business_id")
    _require_saved(saved, "business_id", step_name="Checagem de Pages")
    _run_step_with_fallback(
        lambda: facebook_login.ensure_logged_in(driver, profile_id), "Login no Facebook"
    )
    if facebook_pages.has_locked_page(driver, business_id):
        tracker.mark_processed(cnpj, "abortado", "página bloqueada (cadeado) em Pages")
        raise RuntimeError("Página com cadeado detectada em Pages — não é possível prosseguir.")
    run_log.add("Pages OK (sem bloqueio)")
    tracker.save_checkpoint(cnpj, "pages_ok")


def _step_add_domain(driver, profile_id: str, cnpj: str, saved: dict) -> None:
    business_id = saved.get("business_id")
    _require_saved(saved, "business_id", "dominio", step_name="Adicionar domínio")
    _run_step_with_fallback(
        lambda: facebook_login.ensure_logged_in(driver, profile_id), "Login no Facebook"
    )
    meta_code = facebook_domain.add_domain(driver, business_id, saved["dominio"])
    run_log.add(f"Domínio adicionado no Facebook, código meta-tag: {meta_code}")
    tracker.save_checkpoint(cnpj, "dominio_adicionado", {"meta_code": meta_code})


def _step_apply_meta_tag(driver, profile_id: str, cnpj: str, site_id: str, saved: dict) -> None:
    _require_saved(saved, "meta_code", step_name="Aplicar meta-tag no Buildfy")
    buildfy.apply_meta_tag(driver, site_id, saved["meta_code"])
    run_log.add("Meta-tag aplicada no Buildfy")
    tracker.save_checkpoint(cnpj, "meta_tag_aplicada")


def _step_verify_domain(driver, profile_id: str, cnpj: str, saved: dict) -> None:
    business_id = saved.get("business_id")
    _require_saved(saved, "business_id", "dominio", step_name="Verificar domínio")
    _run_step_with_fallback(
        lambda: facebook_login.ensure_logged_in(driver, profile_id), "Login no Facebook"
    )
    verified = facebook_domain.verify_domain(driver, business_id, saved["dominio"])
    run_log.add(f"Verificação de domínio: {'sucesso' if verified else 'falhou'}",
                 level="success" if verified else "error")
    if verified:
        tracker.save_checkpoint(cnpj, "dominio_verificado")
    else:
        raise RuntimeError("Verificação de domínio não teve sucesso (status continua Not Verified).")


def _step_business_info(driver, profile_id: str, cnpj: str, saved: dict) -> None:
    business_id = saved.get("business_id")
    _require_saved(
        saved, "business_id", "empresa", "logradouro", "cidade", "estado", "cep", "url",
        step_name="Preencher Business Info",
    )
    _run_step_with_fallback(
        lambda: facebook_login.ensure_logged_in(driver, profile_id), "Login no Facebook"
    )
    business_name = _strip_cnpj_prefix(saved["empresa"])

    facebook_business_info.open_edit_business_details(driver, business_id)
    facebook_business_info.fill_business_details(
        driver,
        legal_name=business_name,
        street_address=saved["logradouro"],
        bairro=saved.get("bairro", ""),
        city=saved["cidade"],
        state=saved["estado"],
        zip_code=saved["cep"],
        tax_id=cnpj,
        website=saved["url"],
    )
    run_log.add("Campos de Business Info preenchidos (exceto telefone)")

    run_log.add("Preencha o número de telefone comercial na tela do Business Info e clique Continuar.",
                 level="manual")
    wait_for_manual_step("Preencher Business phone number no Facebook")
    run_log.add("Confirmado: telefone preenchido manualmente")

    facebook_business_info.submit_business_details(driver)
    run_log.add("Business Info salvo")
    tracker.save_checkpoint(cnpj, "business_info_preenchido")


def _step_language(driver, profile_id: str, cnpj: str) -> None:
    _run_step_with_fallback(
        lambda: facebook_login.ensure_logged_in(driver, profile_id), "Login no Facebook"
    )
    facebook_language.set_language_pt_br(driver)
    run_log.add("Idioma da conta definido como Português (Brasil)")
    tracker.save_checkpoint(cnpj, "idioma_pt_br")


def _step_whatsapp(driver, profile_id: str, cnpj: str, saved: dict) -> None:
    business_id = saved.get("business_id")
    _require_saved(saved, "business_id", step_name="Iniciar conta WhatsApp")
    _run_step_with_fallback(
        lambda: facebook_login.ensure_logged_in(driver, profile_id), "Login no Facebook"
    )
    facebook_whatsapp.start_create_whatsapp_account(driver, business_id)
    run_log.add("Wizard de criação da conta WhatsApp aberto")
    tracker.save_checkpoint(cnpj, "whatsapp_categoria_preenchida")

    run_log.add(
        "Selecione a categoria 'Outro', preencha o telefone, resolva a "
        "verificação/captcha na tela do WhatsApp e clique Continuar quando terminar.",
        level="manual",
    )
    wait_for_manual_step("Concluir criação da conta WhatsApp (categoria + telefone + captcha)")
    run_log.add("Confirmado: etapa de WhatsApp concluída manualmente")
    tracker.save_checkpoint(cnpj, "whatsapp_concluido")


def _step_business_verification(driver, profile_id: str, cnpj: str, saved: dict) -> None:
    business_id = saved.get("business_id")
    _require_saved(saved, "business_id", step_name="Iniciar verificação de negócio")
    _run_step_with_fallback(
        lambda: facebook_login.ensure_logged_in(driver, profile_id), "Login no Facebook"
    )
    facebook_business_verification.start_business_verification(driver, business_id, cnpj)
    run_log.add("Wizard de verificação de negócio preenchido até a etapa de telefone/site")
    tracker.save_checkpoint(cnpj, "verificacao_negocio_iniciada")

    run_log.add(
        "Confirme telefone/site e resolva a verificação por SMS/ligação na tela "
        "do Facebook, depois clique Continuar aqui.",
        level="manual",
    )
    wait_for_manual_step("Concluir verificação de negócio (telefone/site)")
    run_log.add("Confirmado: verificação de negócio concluída manualmente")


# mapa etapa -> função executora avulsa. Usado por run_single_step (execução de
# uma etapa isolada, disparada pelo dashboard) e reaproveitado no fluxo completo.
SINGLE_STEP_RUNNERS = {
    "site_data_obtida": lambda driver, profile_id, cnpj, site_id, saved: _step_site_data(driver, cnpj, site_id),
    "business_manager_criado": lambda driver, profile_id, cnpj, site_id, saved: _step_business_manager(
        driver, profile_id, cnpj, saved
    ),
    "email_confirmado": lambda driver, profile_id, cnpj, site_id, saved: _step_confirm_email(
        driver, profile_id, cnpj, site_id, saved
    ),
    "pages_ok": lambda driver, profile_id, cnpj, site_id, saved: _step_pages(driver, profile_id, cnpj, saved),
    "dominio_adicionado": lambda driver, profile_id, cnpj, site_id, saved: _step_add_domain(
        driver, profile_id, cnpj, saved
    ),
    "meta_tag_aplicada": lambda driver, profile_id, cnpj, site_id, saved: _step_apply_meta_tag(
        driver, profile_id, cnpj, site_id, saved
    ),
    "dominio_verificado": lambda driver, profile_id, cnpj, site_id, saved: _step_verify_domain(
        driver, profile_id, cnpj, saved
    ),
    "business_info_preenchido": lambda driver, profile_id, cnpj, site_id, saved: _step_business_info(
        driver, profile_id, cnpj, saved
    ),
    "idioma_pt_br": lambda driver, profile_id, cnpj, site_id, saved: _step_language(driver, profile_id, cnpj),
    "whatsapp_categoria_preenchida": lambda driver, profile_id, cnpj, site_id, saved: _step_whatsapp(
        driver, profile_id, cnpj, saved
    ),
    "verificacao_negocio_iniciada": lambda driver, profile_id, cnpj, site_id, saved: _step_business_verification(
        driver, profile_id, cnpj, saved
    ),
}


def run_single_step(cnpj: str, step: str, profile_id: str | None = None) -> dict:
    """Executa UMA etapa isolada do fluxo para um CNPJ que já tem os checkpoints
    anteriores necessários salvos. Usado pelos botões 'Rodar só essa etapa' do
    dashboard, para depurar/retestar uma parte sem rodar o fluxo inteiro de novo.
    """
    if step not in SINGLE_STEP_RUNNERS:
        raise ValueError(f"Etapa '{step}' não pode ser executada isoladamente.")

    profile_id = profile_id or DEFAULT_PROFILE
    record = tracker.get_record(cnpj)
    if not record or not record["data"].get("site_id"):
        raise RuntimeError(f"CNPJ {cnpj} não tem site_id salvo — rode 'Dados do site' primeiro.")
    site_id = record["data"]["site_id"]
    saved = record["data"]

    tracker.set_profile_id(cnpj, profile_id)
    driver = open_driver(profile_id)
    try:
        run_log.add(f"Usando perfil AdsPower: {profile_id}")
        run_log.start_run(cnpj)
        SINGLE_STEP_RUNNERS[step](driver, profile_id, cnpj, site_id, saved)
        run_log.finish_run(True, f"Etapa '{step}' concluída")
        return {"ok": True, "cnpj": cnpj, "step": step}
    except run_log.PausedByUser:
        run_log.add("Execução pausada pelo usuário.", level="warning")
        run_log.finish_run(True, "Pausado")
        return {"ok": False, "reason": "pausado"}
    except Exception as e:
        run_log.add(f"Erro: {e}", level="error")
        run_log.finish_run(False, str(e))
        raise
    finally:
        close_driver(driver, profile_id)


def run_for_next_pending_cnpj(requested_cnpj: str | None = None, profile_id: str | None = None) -> dict:
    profile_id = profile_id or DEFAULT_PROFILE
    driver = open_driver(profile_id)
    try:
        run_log.add(f"Usando perfil AdsPower: {profile_id}")
        _run_step_with_fallback(lambda: buildfy.ensure_logged_in(driver), "Login no Buildfy")
        run_log.add("Login no Buildfy confirmado")

        picked = _pick_cnpj_and_site_id(driver, requested_cnpj)
        if not picked:
            run_log.add("Nenhum CNPJ pendente encontrado", level="warning")
            return {"ok": False, "reason": "sem_pendentes"}

        cnpj, site_id = picked
        run_log.start_run(cnpj)
        tracker.set_profile_id(cnpj, profile_id)
        done = set((tracker.get_record(cnpj) or {}).get("steps_done", []))
        saved = (tracker.get_record(cnpj) or {}).get("data", {})

        # salva só o site_id, sem marcar nenhuma etapa como concluída — usar
        # tracker.STEPS[0] aqui (bug anterior) marcava "site_data_obtida" como
        # feita ANTES da etapa rodar de verdade, deixando o checkpoint com o
        # step certo mas sem os dados reais (empresa, domínio, etc.) se a etapa
        # falhasse logo em seguida.
        if not done:
            all_data = tracker._load()
            record = all_data.get(cnpj, {"steps_done": [], "data": {}})
            record["data"]["site_id"] = site_id
            all_data[cnpj] = record
            tracker._save(all_data)

        # 1. dados do site
        run_log.check_pause()
        if "site_data_obtida" not in done:
            site_data = _run_step_with_fallback(
                lambda: buildfy.get_site_data(driver, site_id, cnpj), "Obter dados do site"
            )
            run_log.add(f"Dados do site obtidos: {site_data.empresa} / {site_data.dominio}")
            tracker.save_checkpoint(cnpj, "site_data_obtida", {
                "empresa": site_data.empresa, "dominio": site_data.dominio, "url": site_data.url,
                "logradouro": site_data.logradouro, "complemento": site_data.complemento,
                "bairro": site_data.bairro,
                "cidade": site_data.cidade, "estado": site_data.estado, "cep": site_data.cep,
            })
        else:
            run_log.add("Dados do site já obtidos anteriormente (checkpoint)")
        saved = tracker.get_record(cnpj)["data"]

        business_name = _strip_cnpj_prefix(saved["empresa"])
        business_email = f"{saved['dominio'].split('.')[0]}@{'.'.join(saved['dominio'].split('.')[1:])}"

        def _login_manual_step(message: str) -> None:
            run_log.add(message, level="manual")
            wait_for_manual_step(message)
            run_log.add("Confirmado: 2FA concluído manualmente")

        _run_step_with_fallback(
            lambda: facebook_login.ensure_logged_in(driver, profile_id, on_manual_step=_login_manual_step),
            "Login no Facebook",
        )
        run_log.add("Login no Facebook confirmado")

        # 2. business manager
        run_log.check_pause()
        done = set(tracker.get_record(cnpj)["steps_done"])
        if "business_manager_criado" not in done:
            def _bm_manual_step(message: str) -> None:
                run_log.add(message, level="manual")
                wait_for_manual_step(message)
                run_log.add("Confirmado: Submit do Business Manager clicado manualmente")

            business_id = _run_step_with_fallback(
                lambda: create_business_manager(
                    driver, business_name, business_name, business_email, on_manual_step=_bm_manual_step
                ),
                "Criar Business Manager",
            )
            run_log.add(f"Business Manager criado: {business_id}")
            tracker.save_checkpoint(cnpj, "business_manager_criado", {"business_id": business_id})
        else:
            business_id = saved.get("business_id")
            if not business_id:
                raise RuntimeError(
                    "Etapa 'Business Manager criado' está marcada como concluída, mas não há "
                    "business_id salvo. Desmarque a etapa no dashboard e informe o business_id "
                    "no campo antes de marcá-la novamente."
                )
            run_log.add(f"Business Manager já criado anteriormente: {business_id}")

        # 2.5. confirmar e-mail do business manager (link recebido na caixa do Buildfy)
        run_log.check_pause()
        done = set(tracker.get_record(cnpj)["steps_done"])
        if "email_confirmado" not in done:
            def _confirm_email():
                return confirm_business_email(driver, site_id)

            email_business_id = _run_step_with_fallback(_confirm_email, "Confirmar e-mail do Business Manager")
            if email_business_id and email_business_id != business_id:
                run_log.add(
                    f"Aviso: business_id do e-mail ({email_business_id}) difere do salvo ({business_id})",
                    level="warning",
                )
            run_log.add("E-mail do Business Manager confirmado")
            tracker.save_checkpoint(cnpj, "email_confirmado")
        else:
            run_log.add("E-mail do Business Manager já confirmado anteriormente")

        # 3. pages (cadeado)
        run_log.check_pause()
        done = set(tracker.get_record(cnpj)["steps_done"])
        if "pages_ok" not in done:
            if facebook_pages.has_locked_page(driver, business_id):
                run_log.add("Página com cadeado detectada — abortando processo", level="error")
                tracker.mark_processed(cnpj, "abortado", "página bloqueada (cadeado) em Pages")
                run_log.finish_run(False, "Abortado: página bloqueada")
                return {"ok": False, "reason": "pagina_bloqueada"}
            run_log.add("Pages OK (sem bloqueio)")
            tracker.save_checkpoint(cnpj, "pages_ok")
        else:
            run_log.add("Checagem de Pages já feita anteriormente")

        # 4. domínio
        run_log.check_pause()
        done = set(tracker.get_record(cnpj)["steps_done"])
        if "dominio_adicionado" not in done:
            meta_code = _run_step_with_fallback(
                lambda: facebook_domain.add_domain(driver, business_id, saved["dominio"]),
                "Adicionar domínio",
            )
            run_log.add(f"Domínio adicionado no Facebook, código meta-tag: {meta_code}")
            tracker.save_checkpoint(cnpj, "dominio_adicionado", {"meta_code": meta_code})
        else:
            meta_code = saved["meta_code"]
            run_log.add("Domínio já adicionado anteriormente")

        # 5. meta-tag no buildfy
        run_log.check_pause()
        done = set(tracker.get_record(cnpj)["steps_done"])
        if "meta_tag_aplicada" not in done:
            _run_step_with_fallback(
                lambda: buildfy.apply_meta_tag(driver, site_id, meta_code),
                "Aplicar meta-tag no Buildfy",
            )
            run_log.add("Meta-tag aplicada no Buildfy")
            tracker.save_checkpoint(cnpj, "meta_tag_aplicada")
        else:
            run_log.add("Meta-tag já aplicada anteriormente")

        # 6. verificar domínio
        run_log.check_pause()
        done = set(tracker.get_record(cnpj)["steps_done"])
        if "dominio_verificado" not in done:
            verified = _run_step_with_fallback(
                lambda: facebook_domain.verify_domain(driver, business_id, saved["dominio"]),
                "Verificar domínio",
            )
            run_log.add(f"Verificação de domínio: {'sucesso' if verified else 'falhou'}",
                         level="success" if verified else "error")
            if verified:
                tracker.save_checkpoint(cnpj, "dominio_verificado")
        else:
            run_log.add("Domínio já verificado anteriormente")

        # 7. business info + telefone manual
        run_log.check_pause()
        done = set(tracker.get_record(cnpj)["steps_done"])
        if "business_info_preenchido" not in done:
            def _fill_business_info():
                facebook_business_info.open_edit_business_details(driver, business_id)
                facebook_business_info.fill_business_details(
                    driver,
                    legal_name=business_name,
                    street_address=saved["logradouro"],
                    bairro=saved.get("bairro", ""),
                    city=saved["cidade"],
                    state=saved["estado"],
                    zip_code=saved["cep"],
                    tax_id=cnpj,
                    website=saved["url"],
                )
            _run_step_with_fallback(_fill_business_info, "Preencher Business Info")
            run_log.add("Campos de Business Info preenchidos (exceto telefone)")

            run_log.add("Preencha o número de telefone comercial na tela do Business Info e clique Continuar.",
                         level="manual")
            wait_for_manual_step("Preencher Business phone number no Facebook")
            run_log.add("Confirmado: telefone preenchido manualmente")

            _run_step_with_fallback(
                lambda: facebook_business_info.submit_business_details(driver),
                "Salvar Business Info",
            )
            run_log.add("Business Info salvo")
            tracker.save_checkpoint(cnpj, "business_info_preenchido")
        else:
            run_log.add("Business Info já preenchido anteriormente")

        # 7.5. idioma da conta para Português (Brasil) — precisa vir antes do WhatsApp
        run_log.check_pause()
        done = set(tracker.get_record(cnpj)["steps_done"])
        if "idioma_pt_br" not in done:
            _run_step_with_fallback(
                lambda: facebook_language.set_language_pt_br(driver),
                "Definir idioma da conta para Português (Brasil)",
            )
            run_log.add("Idioma da conta definido como Português (Brasil)")
            tracker.save_checkpoint(cnpj, "idioma_pt_br")
        else:
            run_log.add("Idioma já definido como Português (Brasil) anteriormente")

        # 8. whatsapp business — abre o wizard automaticamente, depois pausa manual
        # para categoria + telefone + verificação + captcha (tudo numa pausa só)
        run_log.check_pause()
        done = set(tracker.get_record(cnpj)["steps_done"])
        if "whatsapp_categoria_preenchida" not in done:
            _run_step_with_fallback(
                lambda: facebook_whatsapp.start_create_whatsapp_account(driver, business_id),
                "Iniciar conta WhatsApp",
            )
            run_log.add("Wizard de criação da conta WhatsApp aberto")
            tracker.save_checkpoint(cnpj, "whatsapp_categoria_preenchida")

            run_log.add(
                "Selecione a categoria 'Outro', preencha o telefone, resolva a "
                "verificação/captcha na tela do WhatsApp e clique Continuar quando terminar.",
                level="manual",
            )
            wait_for_manual_step("Concluir criação da conta WhatsApp (categoria + telefone + captcha)")
            run_log.add("Confirmado: etapa de WhatsApp concluída manualmente")
            tracker.save_checkpoint(cnpj, "whatsapp_concluido")
        else:
            run_log.add("Etapa de WhatsApp já tratada anteriormente")

        # 9. iniciar verificação de negócio (a partir da tela de Contas do
        # WhatsApp) até a tela de telefone/site — a confirmação do código por
        # SMS/ligação é manual
        run_log.check_pause()
        done = set(tracker.get_record(cnpj)["steps_done"])
        if "verificacao_negocio_iniciada" not in done:
            _run_step_with_fallback(
                lambda: facebook_business_verification.start_business_verification(driver, business_id, cnpj),
                "Iniciar verificação de negócio",
                max_retries=4,
            )
            run_log.add("Wizard de verificação de negócio preenchido até a etapa de telefone/site")
            tracker.save_checkpoint(cnpj, "verificacao_negocio_iniciada")

            run_log.add(
                "Confirme telefone/site e resolva a verificação por SMS/ligação na tela "
                "do Facebook, depois clique Continuar aqui.",
                level="manual",
            )
            wait_for_manual_step("Concluir verificação de negócio (telefone/site)")
            run_log.add("Confirmado: verificação de negócio concluída manualmente")
        else:
            run_log.add("Verificação de negócio já iniciada anteriormente")

        run_log.add("Etapa automatizável concluída. Prossiga manualmente pelo Telegram / etapas finais.",
                     level="success")
        tracker.mark_processed(cnpj, "concluido", f"business_id={business_id}")
        run_log.finish_run(True, "Fluxo automatizável concluído")
        return {"ok": True, "business_id": business_id, "cnpj": cnpj}
    except run_log.PausedByUser:
        run_log.add("Execução pausada pelo usuário. Progresso salvo — pode retomar depois.", level="warning")
        run_log.finish_run(True, "Pausado")
        return {"ok": False, "reason": "pausado"}
    except Exception as e:
        run_log.add(f"Erro: {e}", level="error")
        run_log.finish_run(False, str(e))
        raise
    finally:
        close_driver(driver, profile_id)
