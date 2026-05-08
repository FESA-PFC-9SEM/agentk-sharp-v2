# Técnicas Utilizadas no Desenvolvimento do AgentK-Sharp v2

## Arquitetura e Agentes

- **ReAct (Reasoning + Acting)** — agente alterna entre raciocínio e execução de ferramentas em loop iterativo até produzir resposta final
- **Tool Use / Function Calling** — LLM recebe schemas de ferramentas e decide quando e como chamá-las
- **Pipeline multi-etapas** — análise orquestrada em sequência: Checkov → RAG → geração de patch → revisão semântica → aplicação → diff
- **Multi-model** — modelos diferentes para cada tarefa (chat, geração de código, revisão semântica, embeddings)

## Recuperação e Contexto

- **RAG (Retrieval-Augmented Generation)** — recupera trechos relevantes do CIS Kubernetes Benchmark para fundamentar cada patch gerado
- **Chunking por regex** — divide o PDF em seções usando padrão `N.N` e `N.N.N` como delimitadores naturais
- **Embeddings locais com cosine similarity** — busca semântica implementada com NumPy sem banco vetorial externo
- **Cache de índice em disco** — embeddings serializados em pickle, invalidados automaticamente quando o PDF muda

## Geração de Output

- **JSON Schema constrained generation** — LLM forçado a responder em JSON válido via Pydantic + Ollama format parameter
- **Chain-of-thought guiado** — campo `reasoning` obrigatório no schema de patch força o modelo a justificar antes de agir
- **Temperatura por tarefa** — 0 para patches (determinístico), 0.1 para revisão semântica (maior cobertura)

## Prompt Engineering

- **Role prompting** — persona explícita de especialista em segurança Kubernetes
- **Contexto dinâmico via RAG** — trechos reais do benchmark inseridos no prompt em tempo de execução
- **Regras de normalização no prompt** — instrui o LLM sobre formato canônico de paths YAML

## Análise de Segurança

- **Análise estática com Checkov** — detecta más configurações contra o CIS Kubernetes Benchmark
- **Revisão semântica em dois passes** — security pass (credenciais, exposições) e quality pass (coerência, typos)
- **Geração automática de Kubernetes Secrets** — substitui credenciais hardcoded por referências `secretKeyRef`
- **Classificação de findings** — agrupa problemas por categoria via `checkov_tests_classification.json`

## Engenharia de Software

- **Normalização de paths YAML** — aceita JSON Pointer, dot-notation e formato canônico gerados pelo LLM
- **Patching de YAML multi-documento** — agrupa e aplica patches por recurso (`Kind/name`) e re-serializa
- **Integração com Kubernetes Python client** — operações no cluster sem dependência do binário `kubectl`
- **Execução local de LLMs via Ollama** — sem dependência de APIs externas, modelos trocáveis por variável de ambiente

## Infraestrutura

- **Logging com métricas por chamada** — tokens, load latency e generation latency registrados em cada inferência
- **Batch testing com CSV** — executa análises em múltiplas runs e exporta métricas para experimentos
- **Interface web com Streamlit** — chat com cluster e análise de manifests com download do YAML corrigido
