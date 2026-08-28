# Criativas, sistema operacional v1.0

Implementação funcional do MVP de Product Design v1.4 da Criativas. O sistema conecta pedido, custo, pagamento, compra, recebimento, estoque, reserva, produção e fulfillment sem transformar a planilha antiga em fonte de verdade.

## O que já funciona

- Autenticação com dois papéis, `MANAGER` e `OPERATIONAL`.
- Visão Geral gerencial e fila operacional.
- Pedidos multi-item, desconto, frete, forma de pagamento esperada, prioridade e data combinada.
- Custo completo por produto: materiais, mão de obra, rateio fixo e perda esperada.
- Hard block de custo incompleto para qualquer papel.
- Override gerencial apenas para margem abaixo da meta, com justificativa.
- CostSnapshot imutável no momento da confirmação.
- Pagamentos parciais e reversão sem exclusão destrutiva.
- Compra multi-item separada de recebimento físico.
- Frete de compra alocado proporcionalmente para custo landed.
- Goods Receipt parcial por item e atualização do custo de referência futuro.
- Inventory Ledger como fonte de verdade, com On Hand, Reserved e Available.
- Reservas com retry automático por ordem de confirmação após entrada de estoque.
- Produção READY/BLOCKED/IN_PROGRESS/COMPLETED/CANCELLED.
- Perda e consumo extra como eventos separados, com efeito físico imediato.
- Conclusão de produção libera automaticamente retirada, entrega ou postagem.
- Fulfillment independente do status comercial.
- Tentativa de entrega malsucedida preserva histórico e retorna a `READY_FOR_DELIVERY`.
- Mudança de data combinada preserva original + histórico de PromiseChange.
- Cancelamento orquestrado preservando ledger, snapshots e histórico.
- Despesas e retiradas pessoais separadas da lucratividade de produto.
- Auditoria e transactional outbox persistentes.
- Reconciliação estrutural no painel de Configurações e via script.
- Layout responsivo, controles com altura mínima de 44 px e navegação mobile.

## Banco

Em desenvolvimento local, se `DATABASE_URL` não for informado, usa SQLite para facilitar testes. Em produção, configure PostgreSQL:

```env
DATABASE_URL=postgresql+psycopg://user:password@host:5432/criativas
```

O `compose.yaml` sobe PostgreSQL 17 + aplicação.

## Executar com Docker

```bash
cp .env.example .env
# altere POSTGRES_PASSWORD, SESSION_SECRET e CRIATIVAS_INITIAL_PASSWORD
docker compose up --build
```

Abra `http://localhost:8000`.

## Executar sem Docker

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
alembic upgrade head
uvicorn app.main:app --reload
```

## Primeiro acesso

O seed cria apenas a empresa, dois usuários e três rótulos de insumos conhecidos. Não injeta vendas, estoque, lucro ou métricas fictícias.

- Manager: `admin@criativas.local`
- Operational: `operacao@criativas.local`
- Senha inicial: valor obrigatório de `CRIATIVAS_INITIAL_PASSWORD` em produção. O script `run_local.sh` usa uma senha apenas de desenvolvimento quando nenhuma variável é informada.

**Antes de qualquer uso real, defina `APP_ENV=production`, use um `SESSION_SECRET` aleatório com pelo menos 32 caracteres, configure `CRIATIVAS_INITIAL_PASSWORD` com uma senha forte e ative `COOKIE_SECURE=1` atrás de HTTPS.**

## Primeira configuração operacional

1. Em Catálogo, confirme os custos atuais dos insumos e crie/ajuste os produtos e fichas técnicas.
2. Em Estoque, faça um ajuste inicial apenas depois de conferir fisicamente cada quantidade. O ajuste entra no ledger com motivo obrigatório.
3. Cadastre clientes e fornecedores.
4. Ajuste margem alvo, valor hora, fixos alocáveis, minutos produtivos e taxas em Configurações.
5. Só então comece a registrar pedidos reais.

A planilha histórica não é importada automaticamente para as tabelas canônicas. Isso é deliberado: o legado contém fórmulas quebradas, custos ausentes e saldos inconsistentes. A migração correta é conferir os saldos atuais e preservar o arquivo antigo como evidência histórica.

## Regras críticas preservadas

- Compra não altera estoque; recebimento altera.
- Reserva não é movimentação física.
- Custo faltante é `INCOMPLETE`, nunca `R$ 0`.
- Margem agregada é `sum(result) / sum(revenue)`, nunca média das margens.
- Retirada pessoal afeta caixa, não lucratividade do produto.
- Falha de entrega não vira `Delivered` nem muda o pedido comercial para um falso status de falha.
- Correções financeiras usam reversão, não delete.
- Mudanças de custo afetam vendas futuras, nunca snapshots já confirmados.

## Qualidade

```bash
pytest -q
python scripts/reconcile.py
```

A suíte cobre hard block de custo incompleto, snapshots, override de margem, compra x recebimento, reservas, perdas, consumo extra, conclusão, falha de entrega, RBAC, pagamentos parciais, PromiseChange, reversão e cancelamento.

## Deploy

O projeto é containerizado e pronto para qualquer host que aceite Docker + PostgreSQL. Este pacote não contém credenciais de nuvem e não cria infraestrutura externa sozinho.

Em produção, configure obrigatoriamente:

```env
APP_ENV=production
DATABASE_URL=postgresql+psycopg://...
SESSION_SECRET=<32+ caracteres aleatórios>
CRIATIVAS_INITIAL_PASSWORD=<senha forte de primeiro acesso>
COOKIE_SECURE=1
```

A aplicação recusa inicialização em produção sem `SESSION_SECRET` e `CRIATIVAS_INITIAL_PASSWORD` válidos.
