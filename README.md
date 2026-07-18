<p align="center">
  <img src="logo.png" alt="Mavio Robot" width="120">
</p>

<h1 align="center">Mavio Robot</h1>

<p align="center">
  Automação ponta a ponta: <b>Buildfy → Facebook Business Manager</b>
</p>

---

## O que é

O Mavio Robot lê CNPJs de empresas brasileiras (`cnpjs.csv`) e automatiza a
criação de toda a presença digital de cada uma no Facebook, a partir de um
site institucional gerado pelo [Buildfy](https://buildfyapp.vercel.app):

- Cria (ou reaproveita) o site institucional no Buildfy
- Cria um Business Manager novo no Facebook
- Confirma o e-mail comercial do BM (via caixa de entrada do próprio Buildfy)
- Adiciona e verifica o domínio do site no Facebook (meta-tag)
- Preenche as Informações da Empresa (endereço, CNPJ, telefone)
- Define o idioma da conta como Português (Brasil)
- Cria a conta do WhatsApp Business (categoria "Outro")
- Inicia a verificação de negócio até a etapa de telefone/site

Tudo isso é controlado por um **dashboard web** rodando em
`http://127.0.0.1:5050`, usando perfis do [AdsPower](https://www.adspower.com/)
(navegador antidetect) para cada conta do Facebook.

## Como funciona

O dashboard funciona como um mini "sistema operacional": cada robô (**A**,
**B**, **C**) é uma janela flutuante independente, que você pode abrir,
arrastar, redimensionar e minimizar — permitindo processar até três CNPJs em
paralelo, cada um com seu próprio perfil AdsPower e log de execução.

- **Fluxo completo automático** — processa um CNPJ do início ao fim, pausando
  apenas nas etapas que exigem confirmação humana (2FA, captcha, telefone).
- **Etapas avulsas** — cada passo do fluxo pode ser disparado isoladamente
  pelo botão "Rodar", útil para retestar ou depurar uma etapa específica sem
  repetir tudo.
- **Checkpoints por CNPJ** — o progresso é salvo passo a passo; se a execução
  cair no meio, retoma de onde parou.
- **Resolução de erros inesperados** — quando uma etapa encontra uma tela não
  mapeada, o robô pausa pedindo intervenção manual em vez de abortar o
  processo inteiro; ao confirmar, tenta de novo automaticamente.

## Como rodar

```bash
pip install -r requirements.txt
python -m app.web.server
```

O dashboard fica disponível em `http://127.0.0.1:5050`.

### Configuração

Copie `.env.example` para `.env` e preencha:

```
ADSPOWER_API_KEY=      # Configurações > Local API no app do AdsPower
ADSPOWER_API_PORT=     # só se a porta local for diferente de 50325
BUILDFY_EMAIL=
BUILDFY_PASSWORD=
```

O app do AdsPower precisa estar aberto na máquina que roda o dashboard.

## Estrutura do projeto

```
app/
  adspower/       cliente da API local do AdsPower (perfis, driver do Chrome)
  automations/    lógica de automação por etapa (Buildfy, login, domínio,
                  WhatsApp, verificação de negócio, orquestrador, checkpoints)
  web/            dashboard Flask (rotas, template, estáticos)
cnpjs.csv         lista de CNPJs a processar
data/             checkpoints salvos por CNPJ
```
