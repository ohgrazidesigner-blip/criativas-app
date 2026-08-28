# Status de entrega

**Product Design baseline:** MVP v1.4, 2024, aceitação interna concluída.

**Software:** implementação funcional v1.0 criada a partir do handoff. A suíte automatizada valida os fluxos críticos e a aplicação inicia em banco novo via Alembic.

## Validado automaticamente

- custo incompleto bloqueia confirmação;
- snapshots não mudam após alteração futura de custo;
- margem baixa exige override gerencial justificado;
- compra não altera estoque;
- recebimento altera ledger e reavalia reservas;
- compra multi-item + recebimento por item;
- produção bloqueada/pronta/em andamento/concluída;
- perda e consumo extra separados;
- conclusão libera fulfillment;
- falha de entrega mantém pedido comercial confirmado;
- páginas Operational omitem valores financeiros;
- pagamento parcial;
- PromiseChange preserva histórico;
- estorno por reversão sem apagar pagamento;
- cancelamento preserva ledger e histórico.

## Infraestrutura externa

O código está versionado no repositório dedicado da Criativas e pronto para Docker + PostgreSQL. A infraestrutura de produção, banco remoto, domínio e certificado são etapas separadas de deploy e não ficam gravadas no código-fonte.
