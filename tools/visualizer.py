# ===== AGENT PROJECT ROOT BOOTSTRAP =====
from pathlib import Path as _AgentPath
import sys as _AgentSys
_AGENT_PROJECT_ROOT = _AgentPath(__file__).resolve().parents[1]
if str(_AGENT_PROJECT_ROOT) not in _AgentSys.path:
    _AgentSys.path.insert(0, str(_AGENT_PROJECT_ROOT))
# ===== END AGENT PROJECT ROOT BOOTSTRAP =====

"""
rag_agent/tools/visualizer.py
Produces charts from agent metrics and project training history.
"""

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("rag_agent.visualizer")


class Visualizer:
    """Generates PNG charts in the project's visualizations/ directory."""

    def plot_metrics(self, metrics: dict[str, Any], output_dir: Path) -> None:
        """
        Create a bar chart of scalar metrics and, if training_history.json
        exists in the parent project dir, a training-curve plot.
        """
        try:
            import matplotlib
            matplotlib.use("Agg")  # headless backend
            import matplotlib.pyplot as plt
        except ImportError:
            logger.warning("matplotlib not installed — skipping visualisation")
            return

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # 1. Metrics bar chart
        scalar_metrics = {
            k: float(v)
            for k, v in metrics.items()
            if isinstance(v, (int, float)) and k not in ("test_passed",)
        }
        if scalar_metrics:
            fig, ax = plt.subplots(figsize=(8, 4))
            keys = list(scalar_metrics.keys())
            vals = [scalar_metrics[k] for k in keys]
            bars = ax.bar(keys, vals, color="#4C72B0", edgecolor="white")
            ax.set_ylim(0, max(1.0, max(vals) * 1.15))
            ax.set_ylabel("Value")
            ax.set_title("Performance Metrics")
            ax.bar_label(bars, fmt="%.4f", padding=3)
            plt.tight_layout()
            path = output_dir / "metrics_bar.png"
            fig.savefig(path, dpi=120)
            plt.close(fig)
            logger.info("Saved metrics bar chart → %s", path)

        # 2. Training curve (if training_history.json exists)
        project_dir = output_dir.parent
        history_path = project_dir / "training_history.json"
        if history_path.exists():
            self._plot_training_history(history_path, output_dir)

    # ------------------------------------------------------------------

    def _plot_training_history(self, history_path: Path, output_dir: Path) -> None:
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
        except ImportError:
            return

        try:
            with open(history_path) as f:
                history: list[dict] = json.load(f)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not load training_history.json: %s", exc)
            return

        if not history or not isinstance(history, list):
            return

        epochs = [entry.get("epoch", i + 1) for i, entry in enumerate(history)]

        fig, axes = plt.subplots(1, 2, figsize=(12, 4))

        # Loss
        if any("loss" in e for e in history):
            losses = [e.get("loss", None) for e in history]
            val_losses = [e.get("val_loss", None) for e in history]
            ax = axes[0]
            ax.plot(epochs, losses, label="Train Loss", marker="o", markersize=3)
            if any(v is not None for v in val_losses):
                ax.plot(epochs, val_losses, label="Val Loss", marker="s", markersize=3)
            ax.set_xlabel("Epoch")
            ax.set_ylabel("Loss")
            ax.set_title("Training Loss")
            ax.legend()

        # Accuracy
        if any("accuracy" in e for e in history):
            accs = [e.get("accuracy", None) for e in history]
            val_accs = [e.get("val_accuracy", None) for e in history]
            ax = axes[1]
            ax.plot(epochs, accs, label="Train Acc", marker="o", markersize=3)
            if any(v is not None for v in val_accs):
                ax.plot(epochs, val_accs, label="Val Acc", marker="s", markersize=3)
            ax.set_xlabel("Epoch")
            ax.set_ylabel("Accuracy")
            ax.set_title("Training Accuracy")
            ax.legend()

        plt.tight_layout()
        path = output_dir / "training_curves.png"
        fig.savefig(path, dpi=120)
        plt.close(fig)
        logger.info("Saved training curves → %s", path)
