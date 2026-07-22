"""Orquestra o fluxo completo: Buildfy -> Facebook Business, com log, checkpoints e pausas manuais.

Cada etapa salva um checkpoint no tracker. Se a execução cair no meio, uma nova
chamada a `run_for_next_pending_cnpj` retoma o mesmo CNPJ a partir da última
etapa concluída, em vez de recomeçar do zero.

Suporta múltiplos "slots" de execução independentes (ex: "A" e "B"), cada um
rodando em sua própria thread com seu próprio log/pausa — permite duas
automações em paralelo (dois perfis AdsPower diferentes) sem uma interferir
na outra. O slot é propagado via um pequeno objeto de contexto (_Ctx) criado
no início de cada execução, para não ter que passar "slot" em toda chamada.
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
from . import pause as pause_module

DEFAULT_PROFILE = "k1eqapx8"
SLOTS = ["A", "B", "C"]


class _Ctx:
    """Agrupa as chamadas de log/pausa já amarradas a um slot específico —
    evita ter que passar slot=... em toda chamada espalhada pelo arquivo."""

    def __init__(self, slot: str):
        self.slot = slot

    def log(self, message: str, level: str = "info") -> None:
        run_log.add(message, level=level, slot=self.slot)

    def check_pause(self) -> None:
        run_log.check_pause(self.slot)

    def begin(self) -> None:
        run_log.begin(slot=self.slot)

    def start_run(self, cnpj: str) -> None:
        run_log.start_run(cnpj, slot=self.slot)

    def finish_run(self, success: bool, message: str = "") -> None:
        run_log.finish_run(success, message, slot=self.slot)

    def wait_manual(self, message: str, timeout: int = 900) -> None:
        pause_module.wait_for_manual_step(message, timeout=timeout, slot=self.slot)

    def run_step_with_fallback(self, step_fn, step_name: str, max_retries: int = 1):
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
                self.log(
                    f"Erro inesperado em '{step_name}' [{type(e).__name__}]: {e}. "
                    "Resolva manualmente na tela do navegador e clique Continuar para tentar de novo.",
                    level="manual",
                )
                self.wait_manual(f"Resolver problema em '{step_name}' e clicar Continuar")
                self.log(f"Retomando '{step_name}' após intervenção manual")
        raise last_error


def _strip_cnpj_prefix(empresa: str) -> str:
    """Remove o prefixo de CNPJ formatado (ex: '68.078.890 ') do nome da empresa."""
    return re.sub(r"^\d{2}\.\d{3}\.\d{3}\s+", "", empresa).strip()


def _pick_cnpj_and_site_id(ctx: _Ctx, driver, requested_cnpj: str | None) -> tuple[str, str] | None:
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
        ctx.log(f"CNPJ {requested_cnpj} não tem site no Buildfy — criando um novo...")
        site_id = buildfy.create_site_from_cnpj(driver, requested_cnpj)
        ctx.log(f"Site criado no Buildfy (site_id={site_id})", level="success")
        return requested_cnpj, site_id

    in_progress_cnpj = tracker.get_in_progress_cnpj()
    if in_progress_cnpj:
        record = tracker.get_record(in_progress_cnpj)
        site_id = record["data"].get("site_id")
        if site_id:
            ctx.log(f"Retomando CNPJ {in_progress_cnpj} a partir da etapa '{record['status']}'")
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

def _step_site_data(ctx: _Ctx, driver, cnpj: str, site_id: str) -> None:
    site_data = buildfy.get_site_data(driver, site_id, cnpj)
    ctx.log(f"Dados do site obtidos: {site_data.empresa} / {site_data.dominio}")
    tracker.save_checkpoint(cnpj, "site_data_obtida", {
        "empresa": site_data.empresa, "dominio": site_data.dominio, "url": site_data.url,
        "logradouro": site_data.logradouro, "complemento": site_data.complemento,
        "bairro": site_data.bairro,
        "cidade": site_data.cidade, "estado": site_data.estado, "cep": site_data.cep,
    })


def _step_business_manager(ctx: _Ctx, driver, profile_id: str, cnpj: str, saved: dict) -> None:
    _require_saved(saved, "empresa", "dominio", step_name="Criar Business Manager")
    business_name = _strip_cnpj_prefix(saved["empresa"])
    business_email = f"{saved['dominio'].split('.')[0]}@{'.'.join(saved['dominio'].split('.')[1:])}"

    def _login_manual_step(message: str) -> None:
        ctx.log(message, level="manual")
        ctx.wait_manual(message)
        ctx.log("Confirmado: 2FA concluído manualmente")

    ctx.run_step_with_fallback(
        lambda: facebook_login.ensure_logged_in(driver, profile_id, on_manual_step=_login_manual_step),
        "Login no Facebook",
    )
    ctx.log("Login no Facebook confirmado")

    facebook_language.set_language_english(driver)
    ctx.log("Idioma da conta definido como English (US) para criação do BM")

    def _bm_manual_step(message: str) -> None:
        ctx.log(message, level="manual")
        ctx.wait_manual(message)
        ctx.log("Confirmado: Submit do Business Manager clicado manualmente")

    business_id = create_business_manager(
        driver, business_name, business_name, business_email, on_manual_step=_bm_manual_step
    )
    ctx.log(f"Business Manager criado: {business_id}", level="success")
    tracker.save_checkpoint(cnpj, "business_manager_criado", {"business_id": business_id})


def _step_confirm_email(ctx: _Ctx, driver, profile_id: str, cnpj: str, site_id: str, saved: dict) -> None:
    business_id = saved.get("business_id")
    _require_saved(saved, "business_id", step_name="Confirmar e-mail do Business Manager")
    ctx.run_step_with_fallback(
        lambda: facebook_login.ensure_logged_in(driver, profile_id), "Login no Facebook"
    )
    email_business_id = confirm_business_email(driver, site_id)
    if email_business_id and email_business_id != business_id:
        ctx.log(
            f"Aviso: business_id do e-mail ({email_business_id}) difere do salvo ({business_id})",
            level="warning",
        )
    ctx.log("E-mail do Business Manager confirmado", level="success")
    tracker.save_checkpoint(cnpj, "email_confirmado")


def _step_pages(ctx: _Ctx, driver, profile_id: str, cnpj: str, saved: dict) -> None:
    business_id = saved.get("business_id")
    _require_saved(saved, "business_id", step_name="Checagem de Pages")
    ctx.run_step_with_fallback(
        lambda: facebook_login.ensure_logged_in(driver, profile_id), "Login no Facebook"
    )
    if facebook_pages.has_locked_page(driver, business_id):
        tracker.mark_processed(cnpj, "abortado", "página bloqueada (cadeado) em Pages")
        raise RuntimeError("Página com cadeado detectada em Pages — não é possível prosseguir.")
    ctx.log("Pages OK (sem bloqueio)")
    tracker.save_checkpoint(cnpj, "pages_ok")


def _step_add_domain(ctx: _Ctx, driver, profile_id: str, cnpj: str, saved: dict) -> None:
    business_id = saved.get("business_id")
    _require_saved(saved, "business_id", "dominio", step_name="Adicionar domínio")
    ctx.run_step_with_fallback(
        lambda: facebook_login.ensure_logged_in(driver, profile_id), "Login no Facebook"
    )
    meta_code = facebook_domain.add_domain(driver, business_id, saved["dominio"])
    ctx.log(f"Domínio adicionado no Facebook, código meta-tag: {meta_code}")
    tracker.save_checkpoint(cnpj, "dominio_adicionado", {"meta_code": meta_code})


def _step_apply_meta_tag(ctx: _Ctx, driver, profile_id: str, cnpj: str, site_id: str, saved: dict) -> None:
    _require_saved(saved, "meta_code", step_name="Aplicar meta-tag no Buildfy")
    buildfy.apply_meta_tag(driver, site_id, saved["meta_code"])
    ctx.log("Meta-tag aplicada no Buildfy")
    tracker.save_checkpoint(cnpj, "meta_tag_aplicada")


def _step_verify_domain(ctx: _Ctx, driver, profile_id: str, cnpj: str, saved: dict) -> None:
    business_id = saved.get("business_id")
    _require_saved(saved, "business_id", "dominio", step_name="Verificar domínio")
    ctx.run_step_with_fallback(
        lambda: facebook_login.ensure_logged_in(driver, profile_id), "Login no Facebook"
    )
    verified = facebook_domain.verify_domain(driver, business_id, saved["dominio"])
    ctx.log(f"Verificação de domínio: {'sucesso' if verified else 'falhou'}",
            level="success" if verified else "error")
    if verified:
        tracker.save_checkpoint(cnpj, "dominio_verificado")
    else:
        raise RuntimeError("Verificação de domínio não teve sucesso (status continua Not Verified).")


def _step_business_info(ctx: _Ctx, driver, profile_id: str, cnpj: str, saved: dict) -> None:
    business_id = saved.get("business_id")
    _require_saved(
        saved, "business_id", "empresa", "logradouro", "cidade", "estado", "cep", "url",
        step_name="Preencher Business Info",
    )
    ctx.run_step_with_fallback(
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
        check_pause=ctx.check_pause,
    )
    ctx.check_pause()
    facebook_business_info.fill_phone(driver)
    ctx.log("Campos de Business Info preenchidos (telefone: 11999999999)")

    ctx.check_pause()
    facebook_business_info.submit_business_details(driver)
    ctx.log("Business Info salvo", level="success")
    tracker.save_checkpoint(cnpj, "business_info_preenchido")


def _step_language(ctx: _Ctx, driver, profile_id: str, cnpj: str) -> None:
    ctx.run_step_with_fallback(
        lambda: facebook_login.ensure_logged_in(driver, profile_id), "Login no Facebook"
    )
    facebook_language.set_language_pt_br(driver)
    ctx.log("Idioma da conta definido como Português (Brasil)")
    tracker.save_checkpoint(cnpj, "idioma_pt_br")


def _step_whatsapp(ctx: _Ctx, driver, profile_id: str, cnpj: str, saved: dict) -> None:
    business_id = saved.get("business_id")
    _require_saved(saved, "business_id", step_name="Iniciar conta WhatsApp")
    ctx.run_step_with_fallback(
        lambda: facebook_login.ensure_logged_in(driver, profile_id), "Login no Facebook"
    )
    facebook_whatsapp.start_create_whatsapp_account(driver, business_id)
    ctx.log("Wizard de criação da conta WhatsApp aberto")
    tracker.save_checkpoint(cnpj, "whatsapp_categoria_preenchida")

    ctx.log(
        "Selecione a categoria 'Outro', preencha o telefone, resolva a "
        "verificação/captcha na tela do WhatsApp e clique Continuar quando terminar.",
        level="manual",
    )
    ctx.wait_manual("Concluir criação da conta WhatsApp (categoria + telefone + captcha)")
    ctx.log("Confirmado: etapa de WhatsApp concluída manualmente", level="success")
    tracker.save_checkpoint(cnpj, "whatsapp_concluido")


def _step_business_verification(ctx: _Ctx, driver, profile_id: str, cnpj: str, saved: dict) -> None:
    business_id = saved.get("business_id")
    _require_saved(saved, "business_id", step_name="Iniciar verificação de negócio")
    ctx.run_step_with_fallback(
        lambda: facebook_login.ensure_logged_in(driver, profile_id), "Login no Facebook"
    )
    facebook_business_verification.start_business_verification(driver, business_id, cnpj)
    ctx.log("Wizard de verificação de negócio preenchido até a etapa de telefone/site")
    tracker.save_checkpoint(cnpj, "verificacao_negocio_iniciada")

    ctx.log(
        "Confirme telefone/site e resolva a verificação por SMS/ligação na tela "
        "do Facebook, depois clique Continuar aqui.",
        level="manual",
    )
    ctx.wait_manual("Concluir verificação de negócio (telefone/site)")
    ctx.log("Confirmado: verificação de negócio concluída manualmente", level="success")


# mapa etapa -> função executora avulsa. Usado por run_single_step (execução de
# uma etapa isolada, disparada pelo dashboard) e reaproveitado no fluxo completo.
SINGLE_STEP_RUNNERS = {
    "site_data_obtida": lambda ctx, driver, profile_id, cnpj, site_id, saved: _step_site_data(
        ctx, driver, cnpj, site_id
    ),
    "business_manager_criado": lambda ctx, driver, profile_id, cnpj, site_id, saved: _step_business_manager(
        ctx, driver, profile_id, cnpj, saved
    ),
    "email_confirmado": lambda ctx, driver, profile_id, cnpj, site_id, saved: _step_confirm_email(
        ctx, driver, profile_id, cnpj, site_id, saved
    ),
    "pages_ok": lambda ctx, driver, profile_id, cnpj, site_id, saved: _step_pages(
        ctx, driver, profile_id, cnpj, saved
    ),
    "dominio_adicionado": lambda ctx, driver, profile_id, cnpj, site_id, saved: _step_add_domain(
        ctx, driver, profile_id, cnpj, saved
    ),
    "meta_tag_aplicada": lambda ctx, driver, profile_id, cnpj, site_id, saved: _step_apply_meta_tag(
        ctx, driver, profile_id, cnpj, site_id, saved
    ),
    "dominio_verificado": lambda ctx, driver, profile_id, cnpj, site_id, saved: _step_verify_domain(
        ctx, driver, profile_id, cnpj, saved
    ),
    "business_info_preenchido": lambda ctx, driver, profile_id, cnpj, site_id, saved: _step_business_info(
        ctx, driver, profile_id, cnpj, saved
    ),
    "idioma_pt_br": lambda ctx, driver, profile_id, cnpj, site_id, saved: _step_language(
        ctx, driver, profile_id, cnpj
    ),
    "whatsapp_categoria_preenchida": lambda ctx, driver, profile_id, cnpj, site_id, saved: _step_whatsapp(
        ctx, driver, profile_id, cnpj, saved
    ),
    "verificacao_negocio_iniciada": (
        lambda ctx, driver, profile_id, cnpj, site_id, saved: _step_business_verification(
            ctx, driver, profile_id, cnpj, saved
        )
    ),
}


def run_single_step(cnpj: str, step: str, profile_id: str | None = None, slot: str = run_log.DEFAULT_SLOT) -> dict:
    """Executa UMA etapa isolada do fluxo para um CNPJ que já tem os checkpoints
    anteriores necessários salvos. Usado pelos botões 'Rodar só essa etapa' do
    dashboard, para depurar/retestar uma parte sem rodar o fluxo inteiro de novo.
    """
    if step not in SINGLE_STEP_RUNNERS:
        raise ValueError(f"Etapa '{step}' não pode ser executada isoladamente.")

    ctx = _Ctx(slot)
    profile_id = profile_id or DEFAULT_PROFILE
    record = tracker.get_record(cnpj)
    if not record or not record["data"].get("site_id"):
        raise RuntimeError(f"CNPJ {cnpj} não tem site_id salvo — rode 'Dados do site' primeiro.")
    site_id = record["data"]["site_id"]
    saved = record["data"]

    tracker.set_profile_id(cnpj, profile_id)
    driver = open_driver(profile_id)
    try:
        ctx.log(f"Usando perfil AdsPower: {profile_id}")
        ctx.start_run(cnpj)
        SINGLE_STEP_RUNNERS[step](ctx, driver, profile_id, cnpj, site_id, saved)
        ctx.finish_run(True, f"Etapa '{step}' concluída")
        return {"ok": True, "cnpj": cnpj, "step": step}
    except run_log.PausedByUser:
        ctx.log("Execução pausada pelo usuário.", level="warning")
        ctx.finish_run(True, "Pausado")
        return {"ok": False, "reason": "pausado"}
    except Exception as e:
        ctx.log(f"Erro: {e}", level="error")
        ctx.finish_run(False, str(e))
        raise
    finally:
        close_driver(driver, profile_id)


def run_facebook_login_only(
    profile_id: str | None = None,
    slot: str = run_log.DEFAULT_SLOT,
) -> dict:
    """Processo independente de CNPJ: só abre o perfil AdsPower e garante o
    login no Facebook (2FA se pedido). Se a sessão do perfil já estiver
    logada, `ensure_logged_in` detecta isso sozinho e não pede 2FA de novo —
    o usuário roda esse processo primeiro para "esquentar" o login, e depois
    usa 'continuar processo' (run_for_next_pending_cnpj) sabendo que o
    Facebook já está logado, sem passar pela tela de 2FA outra vez.
    """
    ctx = _Ctx(slot)
    profile_id = profile_id or DEFAULT_PROFILE
    ctx.begin()
    try:
        driver = open_driver(profile_id)
    except Exception as e:
        ctx.finish_run(False, f"Erro ao abrir perfil AdsPower '{profile_id}': {e}")
        raise

    try:
        ctx.log(f"Usando perfil AdsPower: {profile_id}")

        def _login_manual_step(message: str) -> None:
            ctx.log(message, level="manual")
            ctx.wait_manual(message)
            ctx.log("Confirmado: 2FA concluído manualmente")

        ctx.run_step_with_fallback(
            lambda: facebook_login.ensure_logged_in(driver, profile_id, on_manual_step=_login_manual_step),
            "Login no Facebook",
        )
        ctx.log("Login no Facebook confirmado", level="success")
        ctx.finish_run(True, "Login no Facebook concluído")
        return {"ok": True}
    except run_log.PausedByUser:
        ctx.log("Execução pausada pelo usuário.", level="warning")
        ctx.finish_run(True, "Pausado")
        return {"ok": False, "reason": "pausado"}
    except Exception as e:
        ctx.log(f"Erro: {e}", level="error")
        ctx.finish_run(False, str(e))
        raise
    finally:
        close_driver(driver, profile_id)


def run_for_next_pending_cnpj(
    requested_cnpj: str | None = None,
    profile_id: str | None = None,
    slot: str = run_log.DEFAULT_SLOT,
) -> dict:
    ctx = _Ctx(slot)
    profile_id = profile_id or DEFAULT_PROFILE
    ctx.begin()
    try:
        driver = open_driver(profile_id)
    except Exception as e:
        ctx.finish_run(False, f"Erro ao abrir perfil AdsPower '{profile_id}': {e}")
        raise

    try:
        ctx.log(f"Usando perfil AdsPower: {profile_id}")

        # login no Facebook primeiro, ANTES de qualquer coisa do Buildfy/CNPJ —
        # não depende de qual CNPJ vai ser processado, só do perfil AdsPower.
        # Motivação: se o 2FA falhar, não desperdiça um crédito de criação de
        # site no Buildfy à toa. Também é idempotente — se a sessão do
        # perfil já estiver logada (ensure_logged_in detecta isso sozinho),
        # não pede 2FA de novo, e o fluxo segue direto para o Buildfy.
        def _login_manual_step(message: str) -> None:
            ctx.log(message, level="manual")
            ctx.wait_manual(message)
            ctx.log("Confirmado: 2FA concluído manualmente")

        ctx.run_step_with_fallback(
            lambda: facebook_login.ensure_logged_in(driver, profile_id, on_manual_step=_login_manual_step),
            "Login no Facebook",
        )
        ctx.log("Login no Facebook confirmado")

        ctx.run_step_with_fallback(lambda: buildfy.ensure_logged_in(driver, slot=slot), "Login no Buildfy")
        ctx.log("Login no Buildfy confirmado")

        picked = _pick_cnpj_and_site_id(ctx, driver, requested_cnpj)
        if not picked:
            ctx.log("Nenhum CNPJ pendente encontrado", level="warning")
            return {"ok": False, "reason": "sem_pendentes"}

        cnpj, site_id = picked
        ctx.start_run(cnpj)
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
        ctx.check_pause()
        if "site_data_obtida" not in done:
            site_data = ctx.run_step_with_fallback(
                lambda: buildfy.get_site_data(driver, site_id, cnpj), "Obter dados do site"
            )
            ctx.log(f"Dados do site obtidos: {site_data.empresa} / {site_data.dominio}")
            tracker.save_checkpoint(cnpj, "site_data_obtida", {
                "empresa": site_data.empresa, "dominio": site_data.dominio, "url": site_data.url,
                "logradouro": site_data.logradouro, "complemento": site_data.complemento,
                "bairro": site_data.bairro,
                "cidade": site_data.cidade, "estado": site_data.estado, "cep": site_data.cep,
            })
        else:
            ctx.log("Dados do site já obtidos anteriormente (checkpoint)")
        saved = tracker.get_record(cnpj)["data"]

        if not saved.get("empresa"):
            raise RuntimeError(
                f"Dados do site do Buildfy vieram sem o nome da empresa (site_id={site_id}). "
                "Desmarque o checkpoint 'Dados do site' no dashboard e rode essa etapa de novo."
            )

        business_name = _strip_cnpj_prefix(saved["empresa"])
        business_email = f"{saved['dominio'].split('.')[0]}@{'.'.join(saved['dominio'].split('.')[1:])}"

        # 2. business manager
        ctx.check_pause()
        done = set(tracker.get_record(cnpj)["steps_done"])
        if "business_manager_criado" not in done:
            # o formulário de criação do BM só tem seletores confiáveis
            # mapeados na versão em inglês da tela — se a conta estiver em
            # outro idioma (ex: pt-BR, herdado do perfil AdsPower), força
            # inglês antes de abrir o formulário. Volta para pt-BR mais
            # adiante no fluxo (etapa "idioma_pt_br", antes do WhatsApp).
            ctx.run_step_with_fallback(
                lambda: facebook_language.set_language_english(driver), "Definir idioma da conta para inglês"
            )
            ctx.log("Idioma da conta definido como English (US) para criação do BM")

            def _bm_manual_step(message: str) -> None:
                ctx.log(message, level="manual")
                ctx.wait_manual(message)
                ctx.log("Confirmado: Submit do Business Manager clicado manualmente")

            business_id = ctx.run_step_with_fallback(
                lambda: create_business_manager(
                    driver, business_name, business_name, business_email, on_manual_step=_bm_manual_step
                ),
                "Criar Business Manager",
            )
            ctx.log(f"Business Manager criado: {business_id}")
            tracker.save_checkpoint(cnpj, "business_manager_criado", {"business_id": business_id})
        else:
            business_id = saved.get("business_id")
            if not business_id:
                raise RuntimeError(
                    "Etapa 'Business Manager criado' está marcada como concluída, mas não há "
                    "business_id salvo. Desmarque a etapa no dashboard e informe o business_id "
                    "no campo antes de marcá-la novamente."
                )
            ctx.log(f"Business Manager já criado anteriormente: {business_id}")

        # 2.5. confirmar e-mail do business manager (link recebido na caixa do Buildfy)
        ctx.check_pause()
        done = set(tracker.get_record(cnpj)["steps_done"])
        if "email_confirmado" not in done:
            def _confirm_email():
                return confirm_business_email(driver, site_id)

            email_business_id = ctx.run_step_with_fallback(_confirm_email, "Confirmar e-mail do Business Manager")
            if email_business_id and email_business_id != business_id:
                ctx.log(
                    f"Aviso: business_id do e-mail ({email_business_id}) difere do salvo ({business_id})",
                    level="warning",
                )
            ctx.log("E-mail do Business Manager confirmado")
            tracker.save_checkpoint(cnpj, "email_confirmado")
        else:
            ctx.log("E-mail do Business Manager já confirmado anteriormente")

        # 3. pages (cadeado)
        ctx.check_pause()
        done = set(tracker.get_record(cnpj)["steps_done"])
        if "pages_ok" not in done:
            if facebook_pages.has_locked_page(driver, business_id):
                ctx.log("Página com cadeado detectada — abortando processo", level="error")
                tracker.mark_processed(cnpj, "abortado", "página bloqueada (cadeado) em Pages")
                ctx.finish_run(False, "Abortado: página bloqueada")
                return {"ok": False, "reason": "pagina_bloqueada"}
            ctx.log("Pages OK (sem bloqueio)")
            tracker.save_checkpoint(cnpj, "pages_ok")
        else:
            ctx.log("Checagem de Pages já feita anteriormente")

        # 4. domínio
        ctx.check_pause()
        done = set(tracker.get_record(cnpj)["steps_done"])
        if "dominio_adicionado" not in done:
            meta_code = ctx.run_step_with_fallback(
                lambda: facebook_domain.add_domain(driver, business_id, saved["dominio"]),
                "Adicionar domínio",
            )
            ctx.log(f"Domínio adicionado no Facebook, código meta-tag: {meta_code}")
            tracker.save_checkpoint(cnpj, "dominio_adicionado", {"meta_code": meta_code})
        else:
            meta_code = saved["meta_code"]
            ctx.log("Domínio já adicionado anteriormente")

        # 5. meta-tag no buildfy
        ctx.check_pause()
        done = set(tracker.get_record(cnpj)["steps_done"])
        if "meta_tag_aplicada" not in done:
            ctx.run_step_with_fallback(
                lambda: buildfy.apply_meta_tag(driver, site_id, meta_code),
                "Aplicar meta-tag no Buildfy",
            )
            ctx.log("Meta-tag aplicada no Buildfy")
            tracker.save_checkpoint(cnpj, "meta_tag_aplicada")
        else:
            ctx.log("Meta-tag já aplicada anteriormente")

        # 6. verificar domínio
        ctx.check_pause()
        done = set(tracker.get_record(cnpj)["steps_done"])
        if "dominio_verificado" not in done:
            verified = ctx.run_step_with_fallback(
                lambda: facebook_domain.verify_domain(driver, business_id, saved["dominio"]),
                "Verificar domínio",
            )
            ctx.log(f"Verificação de domínio: {'sucesso' if verified else 'falhou'}",
                    level="success" if verified else "error")
            if verified:
                tracker.save_checkpoint(cnpj, "dominio_verificado")
        else:
            ctx.log("Domínio já verificado anteriormente")

        # 7. business info + telefone manual
        ctx.check_pause()
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
                    check_pause=ctx.check_pause,
                )
            ctx.run_step_with_fallback(_fill_business_info, "Preencher Business Info")
            ctx.run_step_with_fallback(
                lambda: facebook_business_info.fill_phone(driver), "Preencher telefone comercial"
            )
            ctx.log("Campos de Business Info preenchidos (telefone: 11999999999)")

            ctx.run_step_with_fallback(
                lambda: facebook_business_info.submit_business_details(driver),
                "Salvar Business Info",
            )
            ctx.log("Business Info salvo", level="success")
            tracker.save_checkpoint(cnpj, "business_info_preenchido")
        else:
            ctx.log("Business Info já preenchido anteriormente")

        # 7.5. idioma da conta para Português (Brasil) — precisa vir antes do WhatsApp
        ctx.check_pause()
        done = set(tracker.get_record(cnpj)["steps_done"])
        if "idioma_pt_br" not in done:
            ctx.run_step_with_fallback(
                lambda: facebook_language.set_language_pt_br(driver),
                "Definir idioma da conta para Português (Brasil)",
            )
            ctx.log("Idioma da conta definido como Português (Brasil)")
            tracker.save_checkpoint(cnpj, "idioma_pt_br")
        else:
            ctx.log("Idioma já definido como Português (Brasil) anteriormente")

        # 8. whatsapp business — abre o wizard automaticamente, depois pausa manual
        # para categoria + telefone + verificação + captcha (tudo numa pausa só)
        ctx.check_pause()
        done = set(tracker.get_record(cnpj)["steps_done"])
        if "whatsapp_categoria_preenchida" not in done:
            ctx.run_step_with_fallback(
                lambda: facebook_whatsapp.start_create_whatsapp_account(driver, business_id),
                "Iniciar conta WhatsApp",
            )
            ctx.log("Wizard de criação da conta WhatsApp aberto")
            tracker.save_checkpoint(cnpj, "whatsapp_categoria_preenchida")

            ctx.log(
                "Selecione a categoria 'Outro', preencha o telefone, resolva a "
                "verificação/captcha na tela do WhatsApp e clique Continuar quando terminar.",
                level="manual",
            )
            ctx.wait_manual("Concluir criação da conta WhatsApp (categoria + telefone + captcha)")
            ctx.log("Confirmado: etapa de WhatsApp concluída manualmente", level="success")
            tracker.save_checkpoint(cnpj, "whatsapp_concluido")
        else:
            ctx.log("Etapa de WhatsApp já tratada anteriormente")

        # 9. iniciar verificação de negócio (a partir da tela de Contas do
        # WhatsApp) até a tela de telefone/site — a confirmação do código por
        # SMS/ligação é manual
        ctx.check_pause()
        done = set(tracker.get_record(cnpj)["steps_done"])
        if "verificacao_negocio_iniciada" not in done:
            ctx.run_step_with_fallback(
                lambda: facebook_business_verification.start_business_verification(driver, business_id, cnpj),
                "Iniciar verificação de negócio",
                max_retries=4,
            )
            ctx.log("Wizard de verificação de negócio preenchido até a etapa de telefone/site")
            tracker.save_checkpoint(cnpj, "verificacao_negocio_iniciada")

            ctx.log(
                "Confirme telefone/site e resolva a verificação por SMS/ligação na tela "
                "do Facebook, depois clique Continuar aqui.",
                level="manual",
            )
            ctx.wait_manual("Concluir verificação de negócio (telefone/site)")
            ctx.log("Confirmado: verificação de negócio concluída manualmente", level="success")
        else:
            ctx.log("Verificação de negócio já iniciada anteriormente")

        ctx.log("Etapa automatizável concluída. Prossiga manualmente pelo Telegram / etapas finais.",
                level="success")
        tracker.mark_processed(cnpj, "concluido", f"business_id={business_id}")
        ctx.finish_run(True, "Fluxo automatizável concluído")
        return {"ok": True, "business_id": business_id, "cnpj": cnpj}
    except run_log.PausedByUser:
        ctx.log("Execução pausada pelo usuário. Progresso salvo — pode retomar depois.", level="warning")
        ctx.finish_run(True, "Pausado")
        return {"ok": False, "reason": "pausado"}
    except Exception as e:
        ctx.log(f"Erro: {e}", level="error")
        ctx.finish_run(False, str(e))
        raise
    finally:
        close_driver(driver, profile_id)
