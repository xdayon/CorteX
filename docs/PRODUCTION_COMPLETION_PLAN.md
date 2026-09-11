# Plano de conclusão para produção

A fila ativa e seus critérios de aceite estão em [ROADMAP](ROADMAP.md).
O plano antigo de dez gates foi preservado em
[history](history/PRODUCTION_COMPLETION_PLAN-before-2026-09-10.md).
Não retomar gates antigos concluídos ou decisões de multicam/legado revogadas.

Ordem atual: confiabilidade de revisão/lote → voz e plano final → desempenho →
organização incremental → acesso privado → avaliação com episódios reais.

Cada sessão escolhe um gate e registra: problema, arquivos, artefato resultante,
comando/teste, resultado e limitação. Orquestrador integra, verifica e atualiza
roadmap/handoff; agentes recebem propriedade exclusiva de arquivos. Limpeza não
pode apagar artefatos ou configurações locais. Teste mockado não comprova GPU,
browser, precisão de voz ou qualidade editorial de um podcast.
