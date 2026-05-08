# RAG Agent — Local Autonomous AI Project Builder

An autonomous agent that runs on your personal computer. Given a prompt, it:

1. **Generates** a complete Python/PyTorch project from scratch
2. **Tests** it automatically with pytest
3. **Evaluates** model performance (accuracy, loss, F1)
4. **Fixes bugs** autonomously using LLM analysis of tracebacks
5. **Improves** model architecture if performance is below threshold
6. **Visualizes** training curves, metrics, and model graphs
7. **Indexes** finished projects into a vector store (RAG) for future reference

---

## Quick Start

```bash
# 1. Install Ollama and pull a model
brew install ollama         # macOS
ollama pull codellama:13b
ollama serve                # keep running in Terminal 1

# 2. Set up Python environment
python3 -m venv rag_agent_env
source rag_agent_env/bin/activate
pip install -r requirements.txt
pip install torch --index-url https://download.pytorch.org/whl/cpu

# 3. Start the agent server (Terminal 2)
python server.py

# 4. Submit tasks (Terminal 3)
python cli.py run "Build a PyTorch CNN for MNIST, target 95% accuracy"
python cli.py status
python cli.py logs --lines 50
```

---

## Directory Layout

```
rag_agent/
├── core/agent.py          ← main agent orchestrator
├── tools/
│   ├── code_gen.py        ← LLM-powered project generator
│   ├── test_runner.py     ← pytest runner + smoke tester
│   ├── evaluator.py       ← metrics parser + score computer
│   ├── bug_fixer.py       ← LLM-powered bug fixer
│   ├── model_improver.py  ← LLM-powered architecture improver
│   └── visualizer.py      ← matplotlib chart generator
├── rag/store.py           ← ChromaDB vector store
├── server.py              ← FastAPI REST server (port 8765)
├── cli.py                 ← command-line interface
├── config.json            ← your settings
├── projects/              ← generated projects (auto-created)
├── logs/                  ← agent.log, state.json
└── models_store/          ← ChromaDB persistence
```

---

## Configuration (config.json)

| Key | Default | Description |
|-----|---------|-------------|
| `llm_backend` | `"ollama"` | `"ollama"` or `"openai"` |
| `model` | `"codellama:13b"` | Ollama model name or OpenAI model |
| `ollama_host` | `"http://localhost:11434"` | Ollama server URL |
| `performance_threshold` | `0.80` | Minimum score to stop iterating (0–1) |
| `max_iterations` | `5` | Max fix/improve cycles per task |

---

## REST API

The server exposes these endpoints:

```
POST /tasks              Submit a new task
GET  /tasks              List all tasks (last 50)
GET  /tasks/{id}         Get task details and metrics
GET  /tasks/{id}/visualizations  Get chart file paths
GET  /status             Agent status + totals
GET  /logs               Recent log lines
POST /rag/ingest         Add documents to RAG store
GET  /docs               Interactive API docs (Swagger UI)
```

---

## CLI Commands

```bash
python cli.py run "your prompt here"      # submit a task and watch it
python cli.py status                       # show agent status
python cli.py task <task_id>              # inspect a specific task
python cli.py logs --lines 100            # tail the log
python cli.py ingest /path/to/code        # index existing code into RAG
```

---

## Example Prompts

### PyTorch — Image Classification
```
Build a PyTorch ResNet-style CNN for CIFAR-10. Use data augmentation, 
batch normalization, and cosine LR scheduling. Target: 85%+ test accuracy. 
Save model to models/cifar10.pt. Write per-epoch training_history.json.
```

### PyTorch — Time Series
```
Build a PyTorch LSTM to forecast a synthetic sine wave 7 steps ahead 
using a 30-step sliding window. Evaluate with MSE and MAE. Save model and 
plot predictions vs ground truth in visualizations/.
```

### PyTorch — VAE
```
Build a variational autoencoder in PyTorch on MNIST. Train for 20 epochs.
Log reconstruction_loss and kl_loss per epoch in training_history.json. 
Save 16 reconstructed samples as a grid image.
```

### Scikit-learn — Regression
```
Build a scikit-learn pipeline for Boston housing price prediction.
Compare LinearRegression, RandomForest, and GradientBoosting.
Output best model R2 and RMSE in metrics.json. Create feature importance chart.
```

---

## How Generated Projects Are Evaluated

The agent reads these files written by the generated project:

- **metrics.json** — `{"accuracy": 0.94, "loss": 0.18}`
- **training_history.json** — `[{"epoch": 1, "loss": 0.8, "accuracy": 0.72}, ...]`

A composite score is computed (0–1). If it's below `performance_threshold`, 
the agent sends the model code back to the LLM for architecture improvements 
and reruns. This loop continues until the score passes or `max_iterations` is hit.

---

## Adding Your Own Code to the RAG Store

Ingest your existing projects so the agent can reference your coding patterns:

```bash
python cli.py ingest ~/projects/my_pytorch_models
python cli.py ingest ~/projects/sklearn_experiments
```

Or use the API:

```bash
curl -X POST http://localhost:8765/rag/ingest \
  -H "Content-Type: application/json" \
  -d '{"texts": ["# my_model.py\nimport torch..."]}'
```

---

## Hardware Requirements

| Tier | RAM | GPU | Model | Speed |
|------|-----|-----|-------|-------|
| Minimum | 8GB | None | codellama:7b | ~5 min/task |
| Recommended | 16GB | 8GB VRAM | codellama:13b | ~60s/task |
| Optimal | 32GB+ | 24GB VRAM | deepseek-coder:33b | ~20s/task |

---

## Troubleshooting

**"Cannot connect to agent server"** → Run `python server.py` in a separate terminal.

**"Ollama connection refused"** → Run `ollama serve` in a separate terminal.

**Tasks stuck in "running"** → Check `python cli.py logs` for errors. The LLM may be generating invalid JSON — try a larger model.

**Low performance scores** → Lower `performance_threshold` in config.json, or use a more capable model like `deepseek-coder:33b`.

**ChromaDB import error** → Run `pip install chromadb sentence-transformers`. The agent falls back to TF-IDF automatically if chromadb is missing.