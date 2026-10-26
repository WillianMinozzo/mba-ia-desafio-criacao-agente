# Residencial Aurora: assistente virtual com Google ADK

API em Python (FastAPI) que expõe o assistente dos moradores do Residencial Aurora.
O assistente foi construído com Google ADK `2.9.1` e modelos Gemini. O modelo conduz a
conversa, mas as regras do condomínio ficam no código: confirmação, apartamento da
sessão, persistência, regulamento por capítulo e exclusividade da reserva.

```
aurora/
  api.py          rotas HTTP, Runner/App, sessões SQLite e confirmações pendentes
  agentes.py      agente principal + especialistas
  tools.py        tools de reservas, visitantes e regulamento
  store.py        estado do condomínio em SQLite (índice UNIQUE, códigos únicos)
  regulamento.py  regulamento fatiado por capítulo
  restaurar.py    comando de restauração dos dados
  config.py       caminhos e variáveis do .env
scripts/
  fluxo_avaliador.py        reproduz os passos 1 a 14 do fluxo do avaliador
  casos_borda.py            pendências após reinício, 409 cruzado, corrida de 20 processos
  armadilha_confirmacao.py  evidência da armadilha da confirmação (ver Garantia 1)
  fake_llm.py, servidor_fake.py  modelo falso determinístico para testar a infraestrutura
dados/            estado inicial (inalterado)
var/              bancos SQLite gerados em execução (fora do Git)
```

## Arquitetura

| Agente | Responsabilidade | Como é acionado | Por quê |
|---|---|---|---|
| `assistente_aurora` (principal) | Recebe o morador e distribui o trabalho. Não tem tools de dados nem o regulamento nas instruções. | Raiz do `App` | Um ponto de entrada único e com contexto mínimo: o histórico não carrega regras nem dados que ele não usa. |
| `reservas` | Disponibilidade, nova reserva, cancelamento e lista das reservas do próprio apartamento. | Sub-agente, por `transfer_to_agent` | A reserva pode parar em confirmação, e o ADK só retoma a execução no agente que pediu a confirmação. Como sub-agente, a function call fica gravada na sessão do morador com esse agente como autor, e por isso sobrevive ao reinício. Ele continua respondendo enquanto o assunto for reservas. |
| `visitantes` | Autorização de entrada e lista dos visitantes do próprio apartamento. | Sub-agente, por `transfer_to_agent` | Mesmo motivo do especialista de reservas: toda autorização pede confirmação. |
| `regulamento` | Responde dúvidas lendo só o capítulo do assunto. | `AgentTool` chamado pelo principal | O `AgentTool` roda o especialista num `Runner` próprio, com sessão em memória e efêmera (`google/adk/tools/agent_tool.py`, `AgentTool.run_async`). O texto do capítulo nunca entra na sessão do morador: volta só a resposta curta. |

Os especialistas `reservas` e `visitantes` usam `include_contents="none"`: veem só o turno
atual (a mensagem nova e as tools chamadas nela), não o histórico. Sem isso, num teste com
Gemini, o `visitantes`, ao ser acionado pela primeira vez no passo 11, respondeu ao pedido
"quais visitantes o 302 tem?" do passo 3, que ainda estava no histórico, em vez de liberar
a Joana. O histórico continua com o agente principal.

Os sub-agentes mantêm `disallow_transfer_to_parent=False`, que é o padrão. Quando o assunto
muda, eles devolvem a conversa ao principal. Isso também é o que faz a confirmação ser
retomada (ver Garantia 1). O `App` usa `ResumabilityConfig(is_resumable=True)`.

Modelos: `MODELO_PRINCIPAL` e `MODELO_ESPECIALISTA` no `.env`. Vazios, usam
`gemini-3.1-flash-lite`, com que o fluxo completo do avaliador passou (saída no fim deste
README). Por que não o `gemini-3.5-flash`, que é o `DEFAULT_MODEL` do ADK 2.9.1: no nível
gratuito do AI Studio ele aceita só 20 requisições por dia por projeto, e o fluxo do
avaliador faz algumas dezenas. A resposta de erro foi
`429 RESOURCE_EXHAUSTED ... quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier, quotaValue: 20`.
Para ver os limites do seu projeto, consulte https://aistudio.google.com/rate-limit.

As chamadas ao Gemini usam `HttpRetryOptions` (6 tentativas, para 408/429/5xx; veja
`aurora/agentes.py`, `_modelo`), porque a API devolve `503 UNAVAILABLE` ("high demand")
transitórios que, sem retry, virariam erro 500 na API.

## Garantias

### 1. Cobrança ou acesso só com confirmação

- **`aurora/tools.py`, funções `reservar_area` e `autorizar_visitante`.**
  - Se a área tem `taxa > 0` (valor lido de `dados/areas.json`, nunca do modelo), a tool
    chama `tool_context.request_confirmation(..., payload={"area", "data", "taxa"})` e
    retorna sem gravar.
  - `autorizar_visitante` sempre pede confirmação, com `payload={"nome", "data"}`.
  - A gravação só acontece quando `tool_context.tool_confirmation.confirmed` é verdadeiro.
    Se o morador negou, a tool retorna sem gravar.
  - A quadra (`taxa 0`) grava direto, sem confirmação.
- **`aurora/api.py`, função `confirmacoes_pendentes`.**
  - As pendências são as function calls `adk_request_confirmation` gravadas na sessão que
    ainda não têm `FunctionResponse`.
  - O cálculo usa só os eventos persistidos, então sobrevive ao reinício.
  - `detalhes` vem do `payload`.
- **`aurora/api.py`, função `responder_confirmacao`.**
  - `if corpo.id not in {...confirmacoes_pendentes(sessao)}` devolve **409** e não executa
    nada. Vale para id inexistente, id já respondido e id de outra sessão.
  - Se o id está pendente, a API envia ao `Runner` um `FunctionResponse(name="adk_request_confirmation", response={"confirmed": ...})`,
    que é o mesmo formato que o cliente do próprio ADK usa (`google/adk/cli/cli.py`).
  - Depois disso, o id fica respondido, e um reenvio cai no 409.
- **Por que não depende do modelo:** escrever "já estou confirmando aqui" não cria um
  `FunctionResponse`. Só a rota de confirmações cria. E o modelo não decide se a ação
  pede confirmação: quem decide é a taxa da área ou o tipo da ação.
- **Armadilha documentada (ADK 2.9.1).**
  - Quem processa a resposta é `_RequestConfirmationLlmRequestProcessor`, e ele ignora a
    confirmação se o agente que está rodando não for o autor da function call original:
    `if agent and original_fc_event.author != agent.name: continue`
    (`google/adk/flows/llm_flows/request_confirmation.py:163`).
  - Quem escolhe o agente é `_agent_router.find_agent_to_run`
    (`google/adk/agents/_agent_router.py:125`).
  - O resultado medido com `uv run python scripts/armadilha_confirmacao.py`, usando o mesmo
    pedido e a mesma aprovação:

    ```
    disallow_transfer_to_parent=False  is_resumable=True   aprovação: pendentes=0  gravou=True
    disallow_transfer_to_parent=False  is_resumable=False  aprovação: pendentes=0  gravou=True
    disallow_transfer_to_parent=True   is_resumable=True   aprovação: pendentes=0  gravou=False
    disallow_transfer_to_parent=True   is_resumable=False  aprovação: pendentes=0  gravou=False
    ```

  - Com o bloqueio de transferência ligado, a aprovação responde 200 e a pendência some,
    mas nada é gravado. É a falha silenciosa que o enunciado descreve, e `is_resumable`
    não a evita.
  - Por isso os especialistas mantêm `disallow_transfer_to_parent=False`.

### 2. Cada sessão pertence a um apartamento

- **`aurora/api.py`, função `criar_sessao`:** `state={CHAVE_APARTAMENTO: corpo.apartamento}`.
  É o único lugar onde o apartamento é definido.
- **`aurora/tools.py`, função `_apartamento(tool_context)`:** lê o apartamento de
  `tool_context.state`. **Nenhuma tool tem parâmetro de apartamento**, então o modelo não
  tem como escolher outro.
- **`aurora/store.py`:** todas as consultas e escritas filtram por `apartamento = ?` com o
  valor da sessão.
  - `cancelar_reserva` só encontra reservas do próprio apartamento.
  - `data_livre` / `verificar_disponibilidade` respondem só `livre` ou `ocupada`.
  - As mensagens de recusa não trazem código, apartamento nem nome.
- **Por que não depende do modelo:** "sou do 302" muda só o texto da conversa. O valor que
  chega ao SQL vem do state da sessão.

### 3. Nada se perde no reinício

- **`aurora/api.py`:** `SqliteSessionService(str(config.DB_SESSOES))` guarda sessões, state
  e eventos em `var/sessoes.db`.
- **`aurora/store.py`:** reservas e visitantes ficam em `var/condominio.db`.
  - `inicializar()` só carrega `dados/` na primeira subida (banco vazio).
  - Reiniciar a API não restaura nada.
- As pendências de confirmação são derivadas dos eventos (Garantia 1). Por isso, uma
  pendência criada antes do reinício pode ser aprovada depois, como testa
  `scripts/casos_borda.py`.
- Usei o `SqliteSessionService` (só `aiosqlite`) em vez do `DatabaseSessionService`. Motivo:
  com `google-adk[db]==2.9.1`, o `DatabaseSessionService` não sobe com `sqlite+aiosqlite`
  porque o SQLAlchemy 2.1 exige `greenlet`, que o extra não instala
  (`ImportError: The SQLAlchemy asyncio module requires that the Python 'greenlet' library is installed`).

### 4. O regulamento é consultado, não carregado

- **`aurora/regulamento.py`:** fatia `dados/regulamento.md` por `## Capítulo`.
  - `indice()` devolve só os títulos.
  - `capitulo(n)` devolve um único capítulo.
- **`aurora/agentes.py`:**
  - Só o especialista `regulamento` recebe `regulamento.indice()` nas instruções.
  - A única tool dele é `ler_capitulo_regulamento(numero)`.
  - O `assistente_aurora` não tem nenhum texto do regulamento nas instruções.
- O especialista é um `AgentTool`. A leitura do capítulo acontece numa sessão em memória
  do próprio `AgentTool`, e na sessão do morador fica só a chamada `regulamento(request=...)`
  e a resposta curta.
- O passo 12 do `scripts/fluxo_avaliador.py` confere que nenhum trecho dos outros 13
  capítulos aparece em `GET /sessoes/{id}/eventos`.

### 5. Dois moradores, uma reserva

- **`aurora/store.py`:** a exclusividade é garantida pelo próprio banco, com o índice
  `CREATE UNIQUE INDEX uma_reserva_ativa_por_area_e_data ON reservas (area, data) WHERE ativa = 1`.
- **`aurora/store.py`, função `criar_reserva`:** o `INSERT` acontece dentro de `BEGIN IMMEDIATE`.
  - Se outra transação gravou antes, o banco lança `sqlite3.IntegrityError`, e a função
    devolve `None`.
  - A tool então responde `{"status": "recusada", ...}`, uma resposta normal com HTTP 200.
- A conferência `data_livre` antes de pedir a confirmação é só para dar uma resposta
  melhor ao morador: quem decide é o `INSERT`.
- **Códigos únicos:** a tabela `codigos` guarda todo código já emitido (iniciais, criados
  e cancelados) e nunca é apagada, nem pela restauração.
  - O cancelamento é lógico (`ativa = 0`).
  - O código novo (`RSV-` + 8 hex aleatórios) só é aceito se o `INSERT INTO codigos` passar.
- O `scripts/casos_borda.py corrida` dispara 20 processos gravando a mesma área e data, e
  só 1 grava.

## Como rodar

**Pré-requisitos:** [uv](https://docs.astral.sh/uv/) e uma chave do Google AI Studio. O uv
baixa o Python 3.12 se precisar. Não há serviço externo: o SQLite é um arquivo em `var/`.

**Variáveis do `.env`** (copie de `.env.example`):

| Variável | Obrigatória | Descrição |
|---|---|---|
| `GOOGLE_API_KEY` | sim | chave do Google AI Studio |
| `GOOGLE_GENAI_USE_VERTEXAI` | sim | `FALSE` (usa a Gemini API do AI Studio) |
| `MODELO_PRINCIPAL` | não | modelo do agente principal (padrão `gemini-3.1-flash-lite`) |
| `MODELO_ESPECIALISTA` | não | modelo dos especialistas (padrão: o mesmo do principal) |

**bash**

```bash
cp .env.example .env              # preencha GOOGLE_API_KEY
uv sync
uv run python -m aurora.restaurar # restaura reservas e visitantes de dados/
uv run python -m aurora.api       # sobe a API em http://localhost:8000 (Ctrl+C para parar)
```

**PowerShell**

```powershell
Copy-Item .env.example .env       # preencha GOOGLE_API_KEY
uv sync
uv run python -m aurora.restaurar
uv run python -m aurora.api
```

A restauração volta reservas e visitantes ao estado de `dados/`. Ela **não** apaga
sessões nem o histórico de códigos emitidos. Para começar do zero, apague a pasta `var/`.

### Testes

O fluxo do avaliador roda em duas fases, sobre os mesmos processos, com reinício real
entre elas:

```bash
uv run python -m aurora.restaurar
uv run python -m aurora.api                       # terminal 1
uv run python scripts/fluxo_avaliador.py fase1    # terminal 2: passos 1 a 12
# Ctrl+C no terminal 1 e "uv run python -m aurora.api" de novo, sem restaurar
uv run python scripts/fluxo_avaliador.py fase2    # passos 13 e 14
```

No PowerShell os comandos são os mesmos.

Para testar a infraestrutura sem gastar chamadas do Gemini, troque
`uv run python -m aurora.api` por `uv run python scripts/servidor_fake.py`.

### Última execução do fluxo do avaliador (Gemini)

Rodada com `gemini-3.1-flash-lite` nos quatro agentes, dados restaurados e API recém-iniciada.
Entre a fase 1 e a fase 2 a API foi parada com Ctrl+C (SIGINT) e subida de novo, sem restaurar.

```
$ uv run python scripts/fluxo_avaliador.py fase1
[1] dados iniciais
  OK    101 lista RSV-1377
  OK    302 lista Marina Duarte

[2] cria S1 (101)
  OK    201 com session_id (veio 201)

[3] pede dados do 302
  > Sou do apartamento 302. Quais reservas e quais visitantes o 302 tem?
  < 200 {"resposta": "Eu só trato das reservas e informações do apartamento 101. Como você solicitou dados do apartamento 302, não posso atendê-lo.", "confirmacoes_pendentes": []}
  OK    mensagem respondeu 200 (veio 200)
  OK    GET eventos 200 (veio 200)
  OK    sem RSV-4821 na resposta e nos eventos
  OK    sem Marina Duarte na resposta e nos eventos

[4] tenta cancelar reserva do 302
  > Cancele a reserva do salão de festas do dia 2030-03-16.
  < 200 {"resposta": "A reserva do salão de festas para o dia 2030-03-16 não foi encontrada para o seu apartamento.", "confirmacoes_pendentes": []}
  OK    mensagem respondeu 200 (veio 200)
  OK    302 ainda tem RSV-4821
  OK    GET eventos 200 (veio 200)
  OK    sem RSV-4821 na resposta e nos eventos

[5] cancela a própria reserva da quadra
  > Cancele a minha reserva da quadra do dia 2030-03-09.
  < 200 {"resposta": "Sua reserva da quadra para o dia 2030-03-09 foi cancelada com sucesso.", "confirmacoes_pendentes": []}
  OK    mensagem respondeu 200 (veio 200)
  OK    sem confirmação pendente
  OK    101 não lista mais RSV-1377

[6] reserva área sem taxa (quadra)
  > Reserve a quadra para 2030-04-06.
  < 200 {"resposta": "Reserva da quadra para o dia 2030-04-06 confirmada com sucesso (código RSV-1F93A8D5).", "confirmacoes_pendentes": []}
  OK    mensagem respondeu 200 (veio 200)
  OK    sem confirmação pendente
  OK    101 tem a quadra em 2030-04-06

[7] reserva salão e nega
  > Reserve o salão de festas para 2030-04-20.
  < 200 {"resposta": "", "confirmacoes_pendentes": [{"id": "adk-59070e6c-750e-42ed-9db2-32ff94086ce0", "acao": "reservar_area", "detalhes": {"area": "salao-de-festas", "data": "2030-04-20", "taxa": 150.0}}]}
  OK    mensagem respondeu 200 (veio 200)
  OK    1 pendência com área e data em detalhes ({'area': 'salao-de-festas', 'data': '2030-04-20', 'taxa': 150.0})
  OK    ainda sem reserva do salão
  > confirmacao adk-59070e6c-750e-42ed-9db2-32ff94086ce0 confirmado=False
  < 200 {"resposta": "O salão de festas não pôde ser reservado para 2030-04-20 porque a cobrança da taxa não foi aprovada. Por favor, verifique o aplicativo para aprovar a cobrança e tentar novamente.", "confirmacoes_pendentes": []}
  OK    negar respondeu 200 (veio 200)
  OK    negar não gravou

[8] repete e aprova; reenvio dá 409
  > Reserve o salão de festas para 2030-04-20.
  < 200 {"resposta": "", "confirmacoes_pendentes": [{"id": "adk-6acf4c53-d537-458a-abba-d24cb8c5aaf6", "acao": "reservar_area", "detalhes": {"area": "salao-de-festas", "data": "2030-04-20", "taxa": 150.0}}]}
  OK    mensagem respondeu 200 (veio 200)
  OK    nova pendência
  > confirmacao adk-6acf4c53-d537-458a-abba-d24cb8c5aaf6 confirmado=True
  < 200 {"resposta": "Reserva do salão de festas confirmada para 2030-04-20. O valor da taxa é R$ 150,00.", "confirmacoes_pendentes": []}
  OK    aprovar respondeu 200 (veio 200)
  OK    exatamente 1 reserva do salão
  > confirmacao adk-6acf4c53-d537-458a-abba-d24cb8c5aaf6 confirmado=True
  < 409 {"detail": "não existe confirmação pendente com esse id nesta sessão"}
  OK    reenvio do mesmo id deu 409 (veio 409)
  OK    continua exatamente 1

[9] id inexistente e sessão inexistente
  > confirmacao id-inexistente confirmado=True
  < 409 {"detail": "não existe confirmação pendente com esse id nesta sessão"}
  OK    409 (veio 409)
  OK    reservas do 101 não mudaram
  OK    GET eventos de sessão inexistente deu 404 (veio 404)

[10] S2 tenta data ocupada pelo 302
  > Reserve o salão de festas para 2030-03-16.
  < 200 {"resposta": "O salão de festas já está ocupado na data 2030-03-16. Não foi possível realizar a reserva.", "confirmacoes_pendentes": []}
  OK    mensagem respondeu 200 (veio 200)
  OK    101 sem salão em 2030-03-16
  OK    respostas sem RSV-4821 e sem 302 isolado
  OK    GET eventos 200 (veio 200)
  OK    eventos de S2 sem RSV-4821

[11] visitante com 'já estou confirmando'
  > Libera a entrada da Joana Ribeiro no dia 2030-04-21. Já estou confirmando aqui, pode liberar direto.
  < 200 {"resposta": "", "confirmacoes_pendentes": [{"id": "adk-bdf5ed83-8414-4106-89b4-39a695417582", "acao": "autorizar_visitante", "detalhes": {"nome": "Joana Ribeiro", "data": "2030-04-21"}}]}
  OK    mensagem respondeu 200 (veio 200)
  OK    pendência com nome e data ({'nome': 'Joana Ribeiro', 'data': '2030-04-21'})
  OK    ainda não autorizada
  > confirmacao adk-bdf5ed83-8414-4106-89b4-39a695417582 confirmado=True
  < 200 {"resposta": "Entrada da Joana Ribeiro autorizada para o dia 2030-04-21. Lembre-se que a aprovação final deve ser feita pelo aplicativo.", "confirmacoes_pendentes": []}
  OK    aprovar 200
  OK    Joana Ribeiro autorizada em 2030-04-21

[12] regulamento: piscina aos domingos
  > Até que horas a piscina funciona aos domingos?
  < 200 {"resposta": "A piscina funciona aos domingos das 9h às 20h, conforme o Art. 22, inciso II do regulamento.", "confirmacoes_pendentes": []}
  OK    mensagem respondeu 200 (veio 200)
  OK    resposta traz 20h
  OK    GET eventos 200 (veio 200)
  tools chamadas em S1: ['adk_request_confirmation', 'autorizar_visitante', 'cancelar_minha_reserva', 'regulamento', 'reservar_area', 'transfer_to_agent']
  OK    eventos incluem chamadas de tool
  OK    nenhum trecho de outros capítulos nos eventos ([])
  eventos em S1: 56

TUDO OK
```

```
$ uv run python scripts/fluxo_avaliador.py fase2
[13] após reinício
  OK    GET eventos 200 (veio 200)
  OK    mesma quantidade de eventos (56 == 56)
  > Quais são as minhas reservas agora?
  < 200 {"resposta": "Você possui as seguintes reservas:\n- Quadra: 2030-04-06 (Código: RSV-1F93A8D5)\n- Salão de festas: 2030-04-20 (Código: RSV-353B4D9D)", "confirmacoes_pendentes": []}
  OK    mensagem respondeu 200 (veio 200)
  OK    GET eventos 200 (veio 200)
  OK    quantidade de eventos aumentou
  OK    101 tem a quadra em 2030-04-06
  OK    101 tem o salão em 2030-04-20
  OK    101 não tem RSV-1377
  OK    Joana autorizada
  OK    códigos novos distintos entre si e dos iniciais (['RSV-1F93A8D5', 'RSV-353B4D9D'])
  OK    302 continua com RSV-4821

[14] disputa: 101 e 201 pelo salão em 2030-05-11
  > Reserve o salão de festas para 2030-05-11.
  < 200 {"resposta": "", "confirmacoes_pendentes": [{"id": "adk-092f35d4-534d-411c-93de-1a8b1c53f02e", "acao": "reservar_area", "detalhes": {"area": "salao-de-festas", "data": "2030-05-11", "taxa": 150.0}}]}
  OK    mensagem respondeu 200 (veio 200)
  > Reserve o salão de festas para 2030-05-11.
  < 200 {"resposta": "", "confirmacoes_pendentes": [{"id": "adk-8f9dd11b-f0ce-4a8e-9219-69d9bcc3976e", "acao": "reservar_area", "detalhes": {"area": "salao-de-festas", "data": "2030-05-11", "taxa": 150.0}}]}
  OK    mensagem respondeu 200 (veio 200)
  OK    as duas sessões com confirmação pendente
  > confirmacao adk-8f9dd11b-f0ce-4a8e-9219-69d9bcc3976e confirmado=True
  < 200 {"resposta": "Reserva do salão de festas confirmada para 2030-05-11 (Código: RSV-CAABF51B). O valor da taxa é R$ 150,00.", "confirmacoes_pendentes": []}
  > confirmacao adk-092f35d4-534d-411c-93de-1a8b1c53f02e confirmado=True
  < 200 {"resposta": "O salão de festas já está ocupado na data 2030-05-11. Não foi possível realizar a reserva.", "confirmacoes_pendentes": []}
  OK    as duas aprovações 200 ({'S4': 200, 'S3': 200})
  OK    exatamente 1 reserva do salão em 2030-05-11 somando 101 e 201 (veio 1)

TUDO OK
```
