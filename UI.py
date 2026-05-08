"""
rag_agent/ui.py  —  v5.0
All terminal rendering for RAG Agent CLI.
Uses `rich` for panels, tables, progress bars, syntax highlighting.

Install: pip install rich
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
import random
import time
from threading import Thread, Event
import random
import time
from threading import Thread, Event
import random
import time
from threading import Thread, Event

from rich import box
from rich.align import Align
from rich.columns import Columns
from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.rule import Rule
from rich.syntax import Syntax
from rich.text import Text
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

# ═══════════════════════════════════════════════════════════════════════════════
# CONSOLE & THEME
# ═══════════════════════════════════════════════════════════════════════════════

AGENT_THEME = Theme({
    "info":      "cyan",
    "ok":        "bold green",
    "warn":      "bold yellow",
    "err":       "bold red",
    "agent":     "bold blue",
    "dim":       "dim white",
    "phase":     "magenta",
    "task_id":   "yellow",
    "status.running": "bold yellow",
    "status.done":    "bold green",
    "status.error":   "bold red",
    "status.pending": "dim white",
    "mem.stm":   "bold cyan",
    "mem.ltm":   "bold blue",
    "mem.fact":  "yellow",
    "mem.ep":    "dim white",
    "cat.file":       "cyan",
    "cat.solution":   "green",
    "cat.project":    "yellow",
    "cat.error":      "red",
    "cat.general":    "white",
    "cat.preference": "magenta",
})

console = Console(theme=AGENT_THEME, highlight=False)

MAX_FIX_RETRIES = 5

# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")

def _status_style(s: str) -> str:
    return {"running": "status.running", "done": "status.done",
            "error": "status.error"}.get(s, "status.pending")

def _cat_style(cat: str) -> str:
    return {
        "file": "cat.file", "solution": "cat.solution",
        "project": "cat.project", "error": "cat.error",
        "preference": "cat.preference",
    }.get(cat, "cat.general")


# ═══════════════════════════════════════════════════════════════════════════════
# PIXEL CAT ANIMATION — Easter Egg 🐱
# ═══════════════════════════════════════════════════════════════════════════════

CAT_SPRITES = {
    "walk_right": [
        "  /\\_/\\  ",
        " ( o.o ) ",
        "  > ^ <  ",
    ],
    "walk_left": [
        "  /\\_/\\  ",
        " ( o.o ) ",
        "  > ^ <  ",
    ],
    "sit": [
        "  /\\_/\\  ",
        " ( ^.^ ) ",
        "  )   (  ",
    ],
    "play": [
        "  /\\_/\\  ",
        " ( @.@ ) ",
        "  > ~ <  ",
    ],
    "sleep": [
        "  /\\_/\\  ",
        " ( -.- ) ",
        "  (   )  ",
    ],
    "jump": [
        "   /\\_/\\ ",
        "  ( o.o )",
        "   > ^ < ",
    ],
}

class PixelCat:
    """A playful pixel cat that runs around the terminal."""
    
    def __init__(self, console_width: int = 120, console_height: int = 30):
        self.width = console_width
        self.height = console_height
        self.x = random.randint(10, console_width - 20)
        self.y = random.randint(5, console_height - 10)
        self.vx = random.choice([-1, 1])
        self.vy = 0
        self.state = "walk_right"
        self.counter = 0
        self.is_running = False
        self.stop_event = Event()
        self.thread = None
        self.targets = ["[", "]", "{", "}", "(", ")", "<", ">", "*", "#", "@"]
        self.caught_chars = []
        
    def get_sprite(self) -> list[str]:
        return CAT_SPRITES.get(self.state, CAT_SPRITES["walk_right"])
    
    def update_position(self):
        self.counter += 1
        
        if self.counter % 50 == 0:
            self.state = random.choice(["walk_right", "walk_left", "sit", "play", "jump"])
            
        if self.counter % 100 == 0:
            self.vx = random.choice([-1, 0, 1])
            self.vy = random.choice([-1, 0, 1])
        
        if self.state == "walk_right":
            self.vx = 1
            self.vy = 0
        elif self.state == "walk_left":
            self.vx = -1
            self.vy = 0
        elif self.state == "jump":
            self.vy = -2
        elif self.state in ["sit", "sleep"]:
            self.vx = 0
            self.vy = 0
            
        self.x += self.vx
        self.y += self.vy
        
        if self.x <= 0:
            self.x = 0
            self.vx = 1
            self.state = "walk_right"
        elif self.x >= self.width - 10:
            self.x = self.width - 10
            self.vx = -1
            self.state = "walk_left"
            
        if self.y <= 0:
            self.y = 0
            self.vy = 1
        elif self.y >= self.height - 5:
            self.y = self.height - 5
            self.vy = 0
            
        if self.state == "jump":
            self.vy += 1
            if self.y >= self.height - 5:
                self.state = "walk_right"
    
    def start(self):
        if not self.is_running:
            self.is_running = True
            self.stop_event.clear()
            self.thread = Thread(target=self._run, daemon=True)
            self.thread.start()
    
    def stop(self):
        self.is_running = False
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=1)
    
    def _run(self):
        while self.is_running and not self.stop_event.is_set():
            self.update_position()
            time.sleep(0.1)

class CatOverlay:
    """Manages pixel cat overlay on console."""
    
    def __init__(self, console_instance):
        self.console = console_instance
        self.cat = PixelCat(console_instance.width, console_instance.height)
        self.enabled = False
        
    def enable(self):
        self.enabled = True
        self.cat.start()
        
    def disable(self):
        self.enabled = False
        self.cat.stop()
        
    def toggle(self):
        if self.enabled:
            self.disable()
        else:
            self.enable()
    
    def get_status(self) -> str:
        if not self.enabled:
            return "[dim]Cat is sleeping 😴[/dim]"
        state = self.cat.state.replace("_", " ").title()
        return f"[cyan]🐱 {state} at ({int(self.cat.x)}, {int(self.cat.y)}) | Caught: {len(self.cat.caught_chars)}[/cyan]"

# Global cat instance
_cat_overlay: Optional[CatOverlay] = None

def init_cat():
    global _cat_overlay
    if _cat_overlay is None:
        _cat_overlay = CatOverlay(console)
    return _cat_overlay

def toggle_cat() -> str:
    global _cat_overlay
    if _cat_overlay is None:
        _cat_overlay = init_cat()
    _cat_overlay.toggle()
    return _cat_overlay.get_status()

def get_cat_status() -> str:
    global _cat_overlay
    if _cat_overlay and _cat_overlay.enabled:
        return _cat_overlay.get_status()
    return "[dim]Cat is sleeping 😴[/dim]"

def render_cat_demo():
    """Demo the pixel cat animation."""
    cat = init_cat()
    cat.enable()
    
    sample = """
    [AGENT] Processing your request...
    {status: "running", progress: 45%}
    <task> Analyzing code structure </task>
    (function() { return "Hello World"; })
    *** IMPORTANT *** Check these brackets: []{}()<>
    
    🐱 The pixel cat is running around!
    Watch it interact with special characters!
    """
    
    with Live(console=console, refresh_per_second=10) as live:
        for _ in range(100):
            panel = Panel(
                Text(sample),
                title="[bold cyan]🐱 Pixel Cat Demo[/bold cyan]",
                subtitle=cat.get_status(),
                border_style="cyan"
            )
            live.update(panel)
            time.sleep(0.1)
    
    cat.disable()



# ═══════════════════════════════════════════════════════════════════════════════
# PIXEL CAT ANIMATION — Easter Egg 🐱
# ═══════════════════════════════════════════════════════════════════════════════

CAT_SPRITES = {
    "walk_right": [
        "  /\\_/\\  ",
        " ( o.o ) ",
        "  > ^ <  ",
    ],
    "walk_left": [
        "  /\\_/\\  ",
        " ( o.o ) ",
        "  > ^ <  ",
    ],
    "sit": [
        "  /\\_/\\  ",
        " ( ^.^ ) ",
        "  )   (  ",
    ],
    "play": [
        "  /\\_/\\  ",
        " ( @.@ ) ",
        "  > ~ <  ",
    ],
    "sleep": [
        "  /\\_/\\  ",
        " ( -.- ) ",
        "  (   )  ",
    ],
    "jump": [
        "   /\\_/\\ ",
        "  ( o.o )",
        "   > ^ < ",
    ],
}

class PixelCat:
    """A playful pixel cat that runs around the terminal."""
    
    def __init__(self, console_width: int = 120, console_height: int = 30):
        self.width = console_width
        self.height = console_height
        self.x = random.randint(10, console_width - 20)
        self.y = random.randint(5, console_height - 10)
        self.vx = random.choice([-1, 1])
        self.vy = 0
        self.state = "walk_right"
        self.counter = 0
        self.is_running = False
        self.stop_event = Event()
        self.thread = None
        self.targets = ["[", "]", "{", "}", "(", ")", "<", ">", "*", "#", "@"]
        self.caught_chars = []
        
    def get_sprite(self) -> list[str]:
        return CAT_SPRITES.get(self.state, CAT_SPRITES["walk_right"])
    
    def update_position(self):
        self.counter += 1
        
        if self.counter % 50 == 0:
            self.state = random.choice(["walk_right", "walk_left", "sit", "play", "jump"])
            
        if self.counter % 100 == 0:
            self.vx = random.choice([-1, 0, 1])
            self.vy = random.choice([-1, 0, 1])
        
        if self.state == "walk_right":
            self.vx = 1
            self.vy = 0
        elif self.state == "walk_left":
            self.vx = -1
            self.vy = 0
        elif self.state == "jump":
            self.vy = -2
        elif self.state in ["sit", "sleep"]:
            self.vx = 0
            self.vy = 0
            
        self.x += self.vx
        self.y += self.vy
        
        if self.x <= 0:
            self.x = 0
            self.vx = 1
            self.state = "walk_right"
        elif self.x >= self.width - 10:
            self.x = self.width - 10
            self.vx = -1
            self.state = "walk_left"
            
        if self.y <= 0:
            self.y = 0
            self.vy = 1
        elif self.y >= self.height - 5:
            self.y = self.height - 5
            self.vy = 0
            
        if self.state == "jump":
            self.vy += 1
            if self.y >= self.height - 5:
                self.state = "walk_right"
    
    def start(self):
        if not self.is_running:
            self.is_running = True
            self.stop_event.clear()
            self.thread = Thread(target=self._run, daemon=True)
            self.thread.start()
    
    def stop(self):
        self.is_running = False
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=1)
    
    def _run(self):
        while self.is_running and not self.stop_event.is_set():
            self.update_position()
            time.sleep(0.1)

class CatOverlay:
    """Manages pixel cat overlay on console."""
    
    def __init__(self, console_instance):
        self.console = console_instance
        self.cat = PixelCat(console_instance.width, console_instance.height)
        self.enabled = False
        
    def enable(self):
        self.enabled = True
        self.cat.start()
        
    def disable(self):
        self.enabled = False
        self.cat.stop()
        
    def toggle(self):
        if self.enabled:
            self.disable()
        else:
            self.enable()
    
    def get_status(self) -> str:
        if not self.enabled:
            return "[dim]Cat is sleeping 😴[/dim]"
        state = self.cat.state.replace("_", " ").title()
        return f"[cyan]🐱 {state} at ({int(self.cat.x)}, {int(self.cat.y)}) | Caught: {len(self.cat.caught_chars)}[/cyan]"

# Global cat instance
_cat_overlay: Optional[CatOverlay] = None

def init_cat():
    global _cat_overlay
    if _cat_overlay is None:
        _cat_overlay = CatOverlay(console)
    return _cat_overlay

def toggle_cat() -> str:
    global _cat_overlay
    if _cat_overlay is None:
        _cat_overlay = init_cat()
    _cat_overlay.toggle()
    return _cat_overlay.get_status()

def get_cat_status() -> str:
    global _cat_overlay
    if _cat_overlay and _cat_overlay.enabled:
        return _cat_overlay.get_status()
    return "[dim]Cat is sleeping 😴[/dim]"

def render_cat_demo():
    """Demo the pixel cat animation."""
    cat = init_cat()
    cat.enable()
    
    sample = """
    [AGENT] Processing your request...
    {status: "running", progress: 45%}
    <task> Analyzing code structure </task>
    (function() { return "Hello World"; })
    *** IMPORTANT *** Check these brackets: []{}()<>
    
    🐱 The pixel cat is running around!
    Watch it interact with special characters!
    """
    
    with Live(console=console, refresh_per_second=10) as live:
        for _ in range(100):
            panel = Panel(
                Text(sample),
                title="[bold cyan]🐱 Pixel Cat Demo[/bold cyan]",
                subtitle=cat.get_status(),
                border_style="cyan"
            )
            live.update(panel)
            time.sleep(0.1)
    
    cat.disable()



# ═══════════════════════════════════════════════════════════════════════════════
# PIXEL CAT ANIMATION — Easter Egg 🐱
# ═══════════════════════════════════════════════════════════════════════════════

CAT_SPRITES = {
    "walk_right": [
        "  /\\_/\\  ",
        " ( o.o ) ",
        "  > ^ <  ",
    ],
    "walk_left": [
        "  /\\_/\\  ",
        " ( o.o ) ",
        "  > ^ <  ",
    ],
    "sit": [
        "  /\\_/\\  ",
        " ( ^.^ ) ",
        "  )   (  ",
    ],
    "play": [
        "  /\\_/\\  ",
        " ( @.@ ) ",
        "  > ~ <  ",
    ],
    "sleep": [
        "  /\\_/\\  ",
        " ( -.- ) ",
        "  (   )  ",
    ],
    "jump": [
        "   /\\_/\\ ",
        "  ( o.o )",
        "   > ^ < ",
    ],
}

class PixelCat:
    """A playful pixel cat that runs around the terminal."""
    
    def __init__(self, console_width: int = 120, console_height: int = 30):
        self.width = console_width
        self.height = console_height
        self.x = random.randint(10, console_width - 20)
        self.y = random.randint(5, console_height - 10)
        self.vx = random.choice([-1, 1])
        self.vy = 0
        self.state = "walk_right"
        self.counter = 0
        self.is_running = False
        self.stop_event = Event()
        self.thread = None
        self.targets = ["[", "]", "{", "}", "(", ")", "<", ">", "*", "#", "@"]
        self.caught_chars = []
        
    def get_sprite(self) -> list[str]:
        return CAT_SPRITES.get(self.state, CAT_SPRITES["walk_right"])
    
    def update_position(self):
        self.counter += 1
        
        if self.counter % 50 == 0:
            self.state = random.choice(["walk_right", "walk_left", "sit", "play", "jump"])
            
        if self.counter % 100 == 0:
            self.vx = random.choice([-1, 0, 1])
            self.vy = random.choice([-1, 0, 1])
        
        if self.state == "walk_right":
            self.vx = 1
            self.vy = 0
        elif self.state == "walk_left":
            self.vx = -1
            self.vy = 0
        elif self.state == "jump":
            self.vy = -2
        elif self.state in ["sit", "sleep"]:
            self.vx = 0
            self.vy = 0
            
        self.x += self.vx
        self.y += self.vy
        
        if self.x <= 0:
            self.x = 0
            self.vx = 1
            self.state = "walk_right"
        elif self.x >= self.width - 10:
            self.x = self.width - 10
            self.vx = -1
            self.state = "walk_left"
            
        if self.y <= 0:
            self.y = 0
            self.vy = 1
        elif self.y >= self.height - 5:
            self.y = self.height - 5
            self.vy = 0
            
        if self.state == "jump":
            self.vy += 1
            if self.y >= self.height - 5:
                self.state = "walk_right"
    
    def start(self):
        if not self.is_running:
            self.is_running = True
            self.stop_event.clear()
            self.thread = Thread(target=self._run, daemon=True)
            self.thread.start()
    
    def stop(self):
        self.is_running = False
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=1)
    
    def _run(self):
        while self.is_running and not self.stop_event.is_set():
            self.update_position()
            time.sleep(0.1)

class CatOverlay:
    """Manages pixel cat overlay on console."""
    
    def __init__(self, console_instance):
        self.console = console_instance
        self.cat = PixelCat(console_instance.width, console_instance.height)
        self.enabled = False
        
    def enable(self):
        self.enabled = True
        self.cat.start()
        
    def disable(self):
        self.enabled = False
        self.cat.stop()
        
    def toggle(self):
        if self.enabled:
            self.disable()
        else:
            self.enable()
    
    def get_status(self) -> str:
        if not self.enabled:
            return "[dim]Cat is sleeping 😴[/dim]"
        state = self.cat.state.replace("_", " ").title()
        return f"[cyan]🐱 {state} at ({int(self.cat.x)}, {int(self.cat.y)}) | Caught: {len(self.cat.caught_chars)}[/cyan]"

# Global cat instance
_cat_overlay: Optional[CatOverlay] = None

def init_cat():
    global _cat_overlay
    if _cat_overlay is None:
        _cat_overlay = CatOverlay(console)
    return _cat_overlay

def toggle_cat() -> str:
    global _cat_overlay
    if _cat_overlay is None:
        _cat_overlay = init_cat()
    _cat_overlay.toggle()
    return _cat_overlay.get_status()

def get_cat_status() -> str:
    global _cat_overlay
    if _cat_overlay and _cat_overlay.enabled:
        return _cat_overlay.get_status()
    return "[dim]Cat is sleeping 😴[/dim]"

def render_cat_demo():
    """Demo the pixel cat animation."""
    cat = init_cat()
    cat.enable()
    
    sample = """
    [AGENT] Processing your request...
    {status: "running", progress: 45%}
    <task> Analyzing code structure </task>
    (function() { return "Hello World"; })
    *** IMPORTANT *** Check these brackets: []{}()<>
    
    🐱 The pixel cat is running around!
    Watch it interact with special characters!
    """
    
    with Live(console=console, refresh_per_second=10) as live:
        for _ in range(100):
            panel = Panel(
                Text(sample),
                title="[bold cyan]🐱 Pixel Cat Demo[/bold cyan]",
                subtitle=cat.get_status(),
                border_style="cyan"
            )
            live.update(panel)
            time.sleep(0.1)
    
    cat.disable()


# ═══════════════════════════════════════════════════════════════════════════════
# BASIC PRINT PRIMITIVES  (used by cli.py)
# ═══════════════════════════════════════════════════════════════════════════════

def print_info(text: str) -> None:
    console.print(f"[dim][[/dim][info]{_now()}[/info][dim]][/dim]  {text}")

def print_ok(text: str) -> None:
    console.print(f"[ok]✔  {text}[/ok]")

def print_warn(text: str) -> None:
    console.print(f"[warn]⚠  {text}[/warn]")

def print_err(text: str) -> None:
    console.print(f"[err]✘  {text}[/err]")

def print_rule(title: str = "") -> None:
    console.rule(f"[bold cyan]{title}[/bold cyan]" if title else "")

# ═══════════════════════════════════════════════════════════════════════════════
# MAIN MENU
# ═══════════════════════════════════════════════════════════════════════════════

def render_main_menu() -> None:
    console.print()
    console.print("RAG AGENT  v5.0")
    console.print("Autonomous AI Agent - System Control + Dual-Layer Memory")
    console.print()
    console.print(" [1] Chatbot Mode")
    console.print(" [2] Project / Agent Mode")
    console.print(" [3] Service / Tooling")
    console.print(" [4] View History")
    console.print(" [5] View Memory")
    console.print(" [q] Exit", markup=False)
    console.print()
    console.print(" Tip: Type '/cat' in chatbot mode for a surprise!")
    console.print()
def render_chatbot_help() -> None:
    t = Table(show_header=False, box=box.SIMPLE, padding=(0, 1))
    t.add_column(style="bold cyan", width=30)
    t.add_column(style="dim")

    rows = [
        ("[bold]File & Shell[/bold]", ""),
        ("/cwd [path]",        "Get or set working directory"),
        ("/ls [path]",         "List directory"),
        ("/read <file>",       "Read file content"),
        ("/shell <cmd>",       "Run shell command"),
        ("", ""),
        ("[bold]Session[/bold]", ""),
        ("/reset",             "Clear session (STM → LTM)"),
        ("/history",           "Active conversation window"),
        ("/execlog",           "Code execution trace"),
        ("/session <id>",      "Switch session"),
        ("", ""),
        ("[bold]Memory[/bold]", ""),
        ("/memory",            "Full STM + LTM snapshot"),
        ("/memory facts [cat]","List LTM facts"),
        ("/memory recall <q>", "Search LTM"),
        ("/remember k=v [|cat]","Store fact to LTM"),
        ("", ""),
        ("[bold]System Control[/bold]", ""),
        ("/screenshot [path]", "Take screenshot"),
        ("/mouse move x y",    "Move mouse"),
        ("/mouse click x y",   "Left-click"),
        ("/key <keys>",        "Press key(s) e.g. ctrl+s"),
        ("/type <text>",       "Type text"),
        ("", ""),
        ("/help",              "Show this help"),
        ("/mode",              "Return to main menu"),
        ("/model [name]",      "List or switch agent / supervisor model"),
        ("/exit",              "Quit"),
    ]
    for cmd, desc in rows:
        t.add_row(cmd, desc)

    console.print(Panel(t, title="[bold]Commands[/bold]",
                        border_style="dim", padding=(0, 1)))

def render_agent_reply(reply: str, exec_count: int = 0, action_count: int = 0,
                       exec_results: list = None) -> None:
    # Render agent reply — detect code blocks and highlight them
    console.print()

    # Strip [ACTION]...[/ACTION] — đã được server xử lý, không hiển thị cho user
    import re
    clean_reply = re.sub(r'\[ACTION\].*?\[/ACTION\]', '', reply,
                         flags=re.DOTALL | re.IGNORECASE).strip()
    if not clean_reply:
        clean_reply = "(đang xử lý...)"

    # Split by ```python blocks for syntax highlighting
    parts = re.split(r"(```(?:\w+)?\n.*?```)", clean_reply, flags=re.DOTALL)
    reply_lines: list[Any] = []
    for part in parts:
        m = re.match(r"```(\w+)?\n(.*?)```", part, re.DOTALL)
        if m:
            lang = m.group(1) or "python"
            code = m.group(2)
            reply_lines.append(Syntax(code, lang, theme="monokai",
                                      line_numbers=False, word_wrap=True))
        else:
            if part.strip():
                reply_lines.append(part.rstrip())

    # Build content
    content_parts = []
    for item in reply_lines:
        if isinstance(item, str):
            try:
                # Escape [ ] để tránh rich hiểu nhầm là markup tags
                # Agent hay trả về [ACTION], [TOOL], [/ACTION]... gây MarkupError
                safe = item.replace("[", "\\[")
                content_parts.append(Text.from_markup(safe))
            except Exception:
                # Fallback: render plain text nếu vẫn còn ký tự lỗi
                content_parts.append(Text(item))
        else:
            content_parts.append(item)

    # Use Panel per content block
    try:
        group = Group(*content_parts) if len(content_parts) > 1 else (
            content_parts[0] if content_parts else Text(reply)
        )
    except Exception:
        group = Text(reply)
    # Add cat status to subtitle if enabled
    global _cat_overlay
    subtitle = None
    if _cat_overlay and _cat_overlay.enabled:
        subtitle = _cat_overlay.get_status()

    console.print(Panel(group, title="[bold blue]Agent[/bold blue]",
                        subtitle=subtitle,
                        border_style="blue", padding=(1, 2)))

    # Stats row
    if exec_count or action_count:
        stats = []
        if exec_count:   stats.append(f"[green]{exec_count} code block(s) executed[/green]")
        if action_count: stats.append(f"[yellow]{action_count} file/system action(s)[/yellow]")
        console.print("  " + "  |  ".join(stats))

    # Exec results
    if exec_results:
        for i, r in enumerate(exec_results, 1):
            ok      = r.get("success", False)
            style   = "ok" if ok else "err"
            label   = "OK" if ok else "ERR"
            summary = r.get("summary", "")[:400]
            console.print(Panel(
                Syntax(summary, "text", theme="monokai", word_wrap=True),
                title=f"[{style}]Exec {i}: {label}[/{style}]",
                border_style="green" if ok else "red",
                padding=(0, 1),
            ))
    console.print()

def render_chat_status_bar(stm_turns: Any, history_len: Any,
                           exec_count: int, action_count: int) -> None:
    t = Table.grid(padding=(0, 3))
    t.add_column(style="dim", justify="right")
    t.add_column()
    t.add_row("STM turns",  f"[cyan]{stm_turns}[/cyan]")
    t.add_row("Window",     f"[cyan]{history_len}[/cyan]")
    t.add_row("Exec",       f"[green]{exec_count}[/green]")
    t.add_row("Actions",    f"[yellow]{action_count}[/yellow]")
    console.print(t)

# ═══════════════════════════════════════════════════════════════════════════════
# PROJECT DASHBOARD
# ═══════════════════════════════════════════════════════════════════════════════

PIPELINE_PHASES = [
    ("RAG Retrieval",    10),
    ("Code Generation",  20),
    ("Self-Debate",      30),
    ("Running Tests",    50),
    ("Bug Fixing",       60),
    ("Self-Fix Thread",  65),
    ("Evaluating",       80),
    ("Model Improving",  88),
    ("Visualising",      95),
    ("Complete",        100),
]

def _pipeline_table(current_phase: str, pct: float) -> Table:
    t = Table(show_header=False, box=box.SIMPLE_HEAD, padding=(0, 1),
              border_style="dim")
    t.add_column("", width=3)
    t.add_column("Phase", style="dim", width=20)
    t.add_column("", width=6)

    for phase_name, phase_pct in PIPELINE_PHASES:
        if pct >= phase_pct:
            icon  = "[green]✔[/green]"
            style = "green"
        elif phase_name == current_phase or (
            pct >= phase_pct - 15 and pct < phase_pct
        ):
            icon  = "[yellow]▶[/yellow]"
            style = "bold yellow"
        else:
            icon  = "[dim]○[/dim]"
            style = "dim"
        t.add_row(icon, f"[{style}]{phase_name}[/{style}]",
                  f"[dim]{phase_pct}%[/dim]")
    return t


def build_dashboard(
    task: str,
    task_id: str,
    status: str,
    phase: str,
    pct: float,
    elapsed: float,
    fix_attempt: int,
    last_event: str,
    fix_status: str = "",
) -> Panel:
    """Return a rich Panel — caller embeds in Live()."""

    status_style = _status_style(status)
    status_text  = Text(status.upper(), style=status_style)
    if fix_status:
        status_text.append(f"  [{fix_status}]", style="magenta")

    # Progress bar
    prog = Progress(
        TextColumn("[cyan]{task.description}"),
        BarColumn(bar_width=36, style="dim", complete_style="green",
                  finished_style="bold green"),
        TaskProgressColumn(),
        TimeElapsedColumn(),
        expand=False,
    )
    prog_task = prog.add_task(phase, total=100, completed=pct)
    _ = prog_task  # already added

    # Left column: task info
    left = Table.grid(padding=(0, 1))
    left.add_column(style="dim",    width=10)
    left.add_column(style="white",  width=52)
    left.add_row("Task",    Text(task[:60], overflow="ellipsis"))
    left.add_row("ID",      Text(task_id, style="yellow"))
    left.add_row("Status",  status_text)
    left.add_row("Phase",   Text(phase, style="phase"))
    left.add_row("Elapsed", Text(f"{elapsed:.0f}s", style="cyan")
                 + Text(f"   Fix: {fix_attempt}/{MAX_FIX_RETRIES}",
                        style="yellow"))
    left.add_row("Progress", prog)
    left.add_row("Event",   Text(last_event[:60], style="dim"))

    # Right column: pipeline tracker
    right = _pipeline_table(phase, pct)

    cols = Columns([left, right], padding=(0, 4))
    return Panel(cols, title="[bold blue]PROJECT AGENT[/bold blue]",
                 border_style="blue", padding=(1, 2))


class DashboardLive:
    """Context manager wrapping rich Live for the project dashboard."""

    def __init__(self) -> None:
        self._live: Optional[Live] = None
        self._panel: Optional[Panel] = None

    def __enter__(self) -> "DashboardLive":
        self._panel = Panel("Initialising ...", border_style="blue")
        self._live  = Live(self._panel, console=console, refresh_per_second=4,
                           vertical_overflow="visible")
        self._live.__enter__()
        return self

    def update(self, **kwargs) -> None:
        self._panel = build_dashboard(**kwargs)
        if self._live:
            self._live.update(self._panel)

    def __exit__(self, *args) -> None:
        if self._live:
            self._live.__exit__(*args)


def render_project_result(result: dict) -> None:
    t = Table(show_header=False, box=box.ROUNDED, border_style="green",
              padding=(0, 2))
    t.add_column(style="dim",   width=14)
    t.add_column(style="white")
    score = result.get("score", "N/A")
    passed = result.get("passed", False)
    t.add_row("Score",   f"[{'green' if passed else 'red'}]{score}[/]")
    t.add_row("Passed",  f"[{'green' if passed else 'red'}]{'YES' if passed else 'NO'}[/]")
    t.add_row("Project", str(result.get("project_dir", "N/A")))
    if result.get("note"):
        t.add_row("Note",  result["note"])
    console.print(Panel(t, title="[bold green]✔ Task Complete[/bold green]",
                        border_style="green", padding=(1, 2)))

def render_project_error(error: str, attempts: int) -> None:
    console.print(Panel(
        f"[red]{error[:400]}[/red]",
        title=f"[bold red]✘ Task Failed after {attempts} attempt(s)[/bold red]",
        border_style="red", padding=(1, 2),
    ))

# ═══════════════════════════════════════════════════════════════════════════════
# STATUS
# ═══════════════════════════════════════════════════════════════════════════════

def render_status(info: dict, mstats: dict) -> None:
    console.rule("[bold cyan]Agent Status[/bold cyan]")

    # Server panel
    srv = Table.grid(padding=(0, 2))
    srv.add_column(style="dim",   width=12)
    srv.add_column()
    if info:
        srv.add_row("Active",   f"[yellow]{info.get('active',0)}[/yellow]")
        srv.add_row("Done",     f"[green]{info.get('done',0)}[/green]")
        srv.add_row("Errors",   f"[red]{info.get('errors',0)}[/red]")
        srv.add_row("Sessions", str(info.get("sessions",0)))
    else:
        srv.add_row("Server", "[red]not responding — run: python server.py[/red]")
    console.print(Panel(srv, title="[bold]Server[/bold]", border_style="cyan",
                        padding=(1, 2)))

    if not mstats:
        return

    ts = mstats.get("task_stats", {})
    st = mstats.get("ltm_stats",  {})

    ltm = Table.grid(padding=(0, 2))
    ltm.add_column(style="dim",  width=14)
    ltm.add_column()
    ltm.add_row("Tasks",
                f"{ts.get('total',0)}"
                f"  ([green]{ts.get('passed',0)} passed[/green]"
                f" / [red]{ts.get('failed',0)} failed[/red])")
    ltm.add_row("Chat turns",  str(st.get("chat_turns","0")))
    ltm.add_row("Sessions",    str(st.get("sessions","0")))
    console.print(Panel(ltm, title="[bold]Long-Term Memory[/bold]",
                        border_style="blue", padding=(1, 2)))

    recent = mstats.get("recent_tasks", [])
    if recent:
        t = Table(box=box.SIMPLE, border_style="dim", padding=(0, 1))
        t.add_column("",        width=4)
        t.add_column("Time",    style="dim",   width=16)
        t.add_column("Task",    style="white",  width=55)
        t.add_column("Score",   style="cyan",   width=6)
        for task in recent[-8:]:
            ok    = task.get("passed")
            icon  = "[green]✔[/green]" if ok else "[red]✘[/red]"
            ts2   = task.get("created_at","")[:16]
            score = str(task.get("score",""))[:5] or "-"
            t.add_row(icon, ts2, task.get("task","")[:55], score)
        console.print(Panel(t, title="[bold]Recent Tasks[/bold]",
                            border_style="dim"))

# ═══════════════════════════════════════════════════════════════════════════════
# MEMORY
# ═══════════════════════════════════════════════════════════════════════════════

def render_memory_snapshot(snap: dict) -> None:
    stm = snap.get("stm", {})
    ltm = snap.get("ltm", {})

    # STM
    stm_t = Table.grid(padding=(0, 2))
    stm_t.add_column(style="dim",   width=16)
    stm_t.add_column()
    stm_t.add_row("Session",       f"[yellow]{snap.get('session_id','?')}[/yellow]")
    stm_t.add_row("Active turns",  f"[cyan]{stm.get('active_turns',0)}[/cyan]"
                                   f"  (total: {stm.get('total_turns',0)})")
    stm_t.add_row("Exec trace",    str(stm.get("exec_trace_len",0)))
    ctx = stm.get("working_ctx", {})
    for k, v in ctx.items():
        stm_t.add_row(f"  {k}", str(v)[:70])
    console.print(Panel(stm_t,
                        title="[mem.stm]SHORT-TERM MEMORY  (RAM — current session)[/mem.stm]",
                        border_style="cyan", padding=(1, 2)))

    # LTM stats
    stats = ltm.get("stats",      {})
    tstat = ltm.get("task_stats", {})
    ltm_t = Table.grid(padding=(0, 2))
    ltm_t.add_column(style="dim", width=16)
    ltm_t.add_column()
    ltm_t.add_row("Chat turns", str(stats.get("chat_turns","0")))
    ltm_t.add_row("Sessions",   str(stats.get("sessions","0")))
    ltm_t.add_row("Tasks",
                  f"{tstat.get('total',0)}"
                  f"  ([green]{tstat.get('passed',0)} passed[/green]"
                  f" / [red]{tstat.get('failed',0)} failed[/red])")
    console.print(Panel(ltm_t,
                        title="[mem.ltm]LONG-TERM MEMORY  (SQLite — persistent)[/mem.ltm]",
                        border_style="blue", padding=(1, 2)))

    # Episodes
    episodes = ltm.get("recent_episodes", [])
    if episodes:
        ep_t = Table(show_header=False, box=box.SIMPLE, padding=(0, 1))
        ep_t.add_column("Time",    style="dim",       width=17)
        ep_t.add_column("Summary", style="dim white")
        for ep in episodes:
            ep_t.add_row(ep.get("created_at","")[:16],
                         ep.get("summary","")[:90])
        console.print(Panel(ep_t, title="[bold]Episode Summaries[/bold]",
                            border_style="dim"))

    # Facts
    facts = ltm.get("recent_facts", [])
    if facts:
        render_facts_table(facts)

def render_facts_table(facts: list[dict],
                       title: str = "Stored Facts") -> None:
    t = Table(box=box.ROUNDED, border_style="dim", padding=(0, 1))
    t.add_column("Cat",      width=10)
    t.add_column("Key",      style="yellow",    width=22, overflow="fold")
    t.add_column("Value",    style="white",     width=45, overflow="fold")
    t.add_column("Updated",  style="dim",       width=16)
    t.add_column("Imp",      style="dim cyan",  width=4)

    for f in facts:
        cat   = f.get("category","?")
        style = _cat_style(cat)
        t.add_row(
            f"[{style}]{cat}[/{style}]",
            f.get("key",""),
            f.get("value","")[:60],
            f.get("updated_at","")[:16],
            f"{f.get('importance',0):.1f}",
        )
    console.print(Panel(t, title=f"[bold]{title}[/bold]", border_style="dim"))

def render_recall_results(query: str, facts: list[dict]) -> None:
    if not facts:
        print_warn(f"No results for '{query}'")
        return
    console.print(f"\n  [bold]Recall:[/bold] [cyan]{query}[/cyan]"
                  f"  →  [dim]{len(facts)} result(s)[/dim]")
    render_facts_table(facts, title=f"Recall: {query}")

# ═══════════════════════════════════════════════════════════════════════════════
# HISTORY / EXEC LOG
# ═══════════════════════════════════════════════════════════════════════════════

def render_history(history: list[dict], working_ctx: dict,
                   stm_turns: int) -> None:
    t = Table(box=box.SIMPLE_HEAD, border_style="dim", padding=(0, 1))
    t.add_column("Role",    width=8)
    t.add_column("Message", overflow="fold")

    for h in history[-12:]:
        role  = h.get("role","?")
        style = "green" if role == "user" else "blue"
        text  = h.get("content","")[:120].replace("\n"," ")
        t.add_row(f"[{style}]{role}[/{style}]", text)

    ctx_lines = "\n".join(f"  [dim]{k}[/dim]: {str(v)[:70]}"
                          for k, v in working_ctx.items())
    content = t
    if ctx_lines:
        content = RGroup(t, Text(""), Text("Working context:", style="bold"),
                         Text(ctx_lines))

    console.print(Panel(content,
                        title=f"[bold]Conversation Window  (STM — {stm_turns} turns total)[/bold]",
                        border_style="cyan", padding=(1, 1)))

def render_exec_log(logs: list[dict]) -> None:
    t = Table(box=box.SIMPLE_HEAD, border_style="dim", padding=(0, 1))
    t.add_column("#",       width=3,  style="dim")
    t.add_column("Time",    width=16, style="dim")
    t.add_column("",        width=4)
    t.add_column("Code",    width=40, overflow="fold")
    t.add_column("Result",  overflow="fold")

    for i, e in enumerate(logs[-15:], 1):
        ok    = e.get("success", False)
        icon  = "[green]OK[/green]" if ok else "[red]ERR[/red]"
        t.add_row(str(i), e.get("time","")[:16], icon,
                  e.get("code","")[:40],
                  e.get("result","")[:60])

    console.print(Panel(t, title="[bold]Execution Trace[/bold]",
                        border_style="dim"))

# ═══════════════════════════════════════════════════════════════════════════════
# TASK DETAIL
# ═══════════════════════════════════════════════════════════════════════════════

def render_task_detail(info: dict) -> None:
    status = info.get("status","?")
    t = Table.grid(padding=(0, 2))
    t.add_column(style="dim",   width=10)
    t.add_column()
    t.add_row("Status",  Text(status.upper(), style=_status_style(status)))
    t.add_row("Phase",   Text(f"{info.get('phase','?')}  ({info.get('pct',0)}%)",
                              style="phase"))
    if info.get("task"):
        t.add_row("Task", info["task"][:100])
    r = info.get("result", {})
    if r:
        t.add_row("Score",  str(r.get("score","N/A")))
        t.add_row("Passed", "[green]YES[/green]" if r.get("passed") else "[red]NO[/red]")
        t.add_row("Dir",    str(r.get("project_dir","N/A")))
        if r.get("note"):
            t.add_row("Note", r["note"])
    if info.get("error"):
        t.add_row("Error", f"[red]{info['error'][:200]}[/red]")
    if info.get("fix_status"):
        t.add_row("Fix",   f"[yellow]{info['fix_status']}[/yellow]")

    console.print(Panel(t, title=f"[bold]Task — {info.get('task_id','?')}[/bold]",
                        border_style=_status_style(status).split(".")[-1],
                        padding=(1, 2)))

# ═══════════════════════════════════════════════════════════════════════════════
# LOGS
# ═══════════════════════════════════════════════════════════════════════════════

def render_logs(lines: list[str]) -> None:
    console.rule("[bold cyan]Server Log[/bold cyan]")
    for line in lines:
        s = line.strip()
        if   "ERROR"   in s or "FAILED"   in s: console.print(f"[red]{s}[/red]")
        elif "WARN"    in s:                     console.print(f"[yellow]{s}[/yellow]")
        elif "DEBATE"  in s or "SELF-FIX" in s: console.print(f"[magenta]{s}[/magenta]")
        elif "DONE"    in s or "SUCCESS"  in s: console.print(f"[green]{s}[/green]")
        elif "EXEC"    in s or "ACTION"   in s: console.print(f"[cyan]{s}[/cyan]")
        elif "LTM"     in s or "STM"      in s: console.print(f"[blue]{s}[/blue]")
        else:                                    console.print(f"[dim]{s}[/dim]")

# ═══════════════════════════════════════════════════════════════════════════════
# FS HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def render_dir_listing(resp: dict) -> None:
    path    = resp.get("summary","").splitlines()[0] if resp.get("summary") else "?"
    folders = resp.get("folders", [])
    files   = resp.get("files",   [])

    t = Table(box=box.SIMPLE, border_style="dim", padding=(0, 1))
    t.add_column("",     width=3)
    t.add_column("Name", overflow="fold")

    for f in folders:
        t.add_row("[blue]📁[/blue]", f"[blue]{f}[/blue]")
    for f in files:
        t.add_row("[dim]📄[/dim]", f)

    console.print(Panel(t, title=f"[bold]{path}[/bold]  "
                        f"[dim]({len(folders)} dirs, {len(files)} files)[/dim]",
                        border_style="dim"))

def render_file_content(path: str, content: str) -> None:
    # Guess language from extension
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else "text"
    lang_map = {"py":"python","js":"javascript","ts":"typescript",
                "json":"json","yaml":"yaml","yml":"yaml",
                "sh":"bash","md":"markdown","html":"html","css":"css"}
    lang = lang_map.get(ext, "text")
    lines = content.splitlines()
    preview = "\n".join(lines[:80])
    if len(lines) > 80:
        preview += f"\n... ({len(lines)} lines total)"
    console.print(Panel(
        Syntax(preview, lang, theme="monokai", line_numbers=True, word_wrap=True),
        title=f"[bold]{path}[/bold]",
        border_style="dim",
    ))

# ═══════════════════════════════════════════════════════════════════════════════
# PROJECT REPL HEADER
# ═══════════════════════════════════════════════════════════════════════════════

def render_project_header() -> None:
    pipeline = (
        "[dim]RAG Retrieval[/dim]  →  "
        "[dim]Code Generation[/dim]  →  "
        "[dim]Self-Debate[/dim]  →  "
        "[dim]Test[/dim]  →  "
        "[dim]Self-Fix[/dim]  →  "
        "[dim]Evaluate[/dim]  →  "
        "[dim]Model Improve[/dim]"
    )
    console.print(Panel(
        pipeline,
        title="[bold blue]PROJECT AGENT MODE[/bold blue]",
        subtitle="[dim]type 'menu' to go back[/dim]",
        border_style="blue",
        padding=(1, 2),
    ))
    console.print()

# ═══════════════════════════════════════════════════════════════════════════════
# SPINNER  (for short waits)
# ═══════════════════════════════════════════════════════════════════════════════

def make_spinner(label: str) -> Progress:
    p = Progress(
        SpinnerColumn(),
        TextColumn(f"[cyan]{label}[/cyan]"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    )
    return p

# ═══════════════════════════════════════════════════════════════════════════════
# WRAPPER FUNCTIONS — dùng bởi cli.py
# ═══════════════════════════════════════════════════════════════════════════════

def render_dashboard(task: str, task_id: str, status: str, phase: str,
                     pct: float, elapsed: float, fix_attempt: int,
                     last_event: str, fix_status: str = "") -> None:
    """In dashboard trực tiếp (không dùng Live context) — dùng trong polling loop."""
    panel = build_dashboard(task, task_id, status, phase, pct,
                            elapsed, fix_attempt, last_event, fix_status)
    console.clear()
    console.print(panel)


def render_memory_snapshot(snap: dict) -> None:
    """Pretty-print /chat/memory/{session_id} bằng rich panels."""
    stm  = snap.get("stm", {})
    ltm  = snap.get("ltm", {})

    # STM panel
    stm_t = Table.grid(padding=(0, 2))
    stm_t.add_column(style="dim",  width=16)
    stm_t.add_column()
    stm_t.add_row("Session",      f"[yellow]{snap.get('session_id','?')}[/yellow]")
    stm_t.add_row("Active turns", f"[mem.stm]{stm.get('active_turns',0)}[/mem.stm]"
                                  f"  [dim](total: {stm.get('total_turns',0)})[/dim]")
    ctx = stm.get("working_ctx", {})
    for k, v in ctx.items():
        stm_t.add_row(k, str(v)[:80])
    console.print(Panel(stm_t,
                        title="[mem.stm]SHORT-TERM MEMORY  (RAM — session)[/mem.stm]",
                        border_style="cyan", padding=(1, 2)))

    # LTM panel
    stats = ltm.get("stats", {})
    tstat = ltm.get("task_stats", {})
    ltm_t = Table.grid(padding=(0, 2))
    ltm_t.add_column(style="dim", width=16)
    ltm_t.add_column()
    ltm_t.add_row("Chat turns",   str(stats.get("chat_turns", "0")))
    ltm_t.add_row("Sessions",     str(stats.get("sessions", "0")))
    ltm_t.add_row("Tasks total",  str(tstat.get("total", 0)))
    ltm_t.add_row("Passed/Failed",
                  f"[green]{tstat.get('passed',0)} passed[/green]  /  "
                  f"[red]{tstat.get('failed',0)} failed[/red]")
    console.print(Panel(ltm_t,
                        title="[mem.ltm]LONG-TERM MEMORY  (SQLite — persistent)[/mem.ltm]",
                        border_style="blue", padding=(1, 2)))

    # Episodes
    episodes = ltm.get("recent_episodes", [])
    if episodes:
        ep_t = Table(box=box.SIMPLE_HEAD, border_style="dim", padding=(0, 1))
        ep_t.add_column("Time",    style="dim", width=16)
        ep_t.add_column("Summary", overflow="fold")
        for ep in episodes:
            ep_t.add_row(ep.get("created_at","")[:16], ep.get("summary","")[:90])
        console.print(Panel(ep_t, title="[bold]Episode Summaries[/bold]",
                            border_style="dim"))

    # Facts
    facts = ltm.get("recent_facts", [])
    if facts:
        render_facts_table(facts)


def render_status(info: Optional[dict], mstats: Optional[dict]) -> None:
    """In server status + LTM stats."""
    print_rule("Agent Status")

    srv_t = Table.grid(padding=(0, 2))
    srv_t.add_column(style="dim", width=12)
    srv_t.add_column()
    if info:
        srv_t.add_row("Active",   f"[yellow]{info.get('active',0)}[/yellow]")
        srv_t.add_row("Done",     f"[green]{info.get('done',0)}[/green]")
        srv_t.add_row("Errors",   f"[red]{info.get('errors',0)}[/red]")
        srv_t.add_row("Sessions", str(info.get("sessions", 0)))
        border = "green"
        title  = "[bold]Server[/bold]"
    else:
        srv_t.add_row("", "[warn]Server not responding — run: python server.py[/warn]")
        border = "red"
        title  = "[bold]Server[/bold]"
    console.print(Panel(srv_t, title=title, border_style=border, padding=(1, 2)))

    if mstats:
        ts  = mstats.get("task_stats", {})
        st  = mstats.get("ltm_stats", {})
        mem_t = Table.grid(padding=(0, 2))
        mem_t.add_column(style="dim", width=14)
        mem_t.add_column()
        mem_t.add_row("Tasks",
                      f"{ts.get('total',0)}  "
                      f"([green]{ts.get('passed',0)} passed[/green] / "
                      f"[red]{ts.get('failed',0)} failed[/red])")
        mem_t.add_row("Chat turns", str(st.get("chat_turns","0")))
        mem_t.add_row("Sessions",   str(st.get("sessions","0")))
        console.print(Panel(mem_t, title="[mem.ltm]Long-Term Memory[/mem.ltm]",
                            border_style="blue", padding=(1, 2)))

        recent = mstats.get("recent_tasks", [])
        if recent:
            rt = Table(box=box.SIMPLE_HEAD, border_style="dim", padding=(0, 1))
            rt.add_column("",       width=5)
            rt.add_column("Time",   style="dim", width=16)
            rt.add_column("Task",   overflow="fold")
            for t2 in recent[-6:]:
                ok    = t2.get("passed")
                icon  = "[green]OK[/green]" if ok else "[red]FAIL[/red]"
                rt.add_row(icon, t2.get("created_at","")[:16], t2.get("task","")[:55])
            console.print(Panel(rt, title="[bold]Recent Tasks[/bold]",
                                border_style="dim"))


def render_chatbot_header(model: str = "", provider: str = "", title: str = "CHATBOT MODE") -> None:
    header = title
    meta = " | ".join(part for part in [provider, model] if part)
    console.print()
    console.print(Align.center(Text(header, style="bold cyan")))
    if meta:
        console.print(Align.center(Text(meta, style="dim")))
    console.print()


# ═══════════════════════════════════════════════════════════════════════════════
# CHAT MODE PROGRESS BAR  —  supervisor-driven progress while agent works
# ═══════════════════════════════════════════════════════════════════════════════

# Available models for discussion/selection (used by CLI model-switcher)
AVAILABLE_MODELS = [
    "claude-opus-4-6",
    "claude-sonnet-4-6",
    "gpt-5.5",
    "gpt-5.4",
]

AVAILABLE_SUPERVISOR_MODELS = [
    "gpt-5.5",
    "gpt-5.4",
    "claude-opus-4-6",
    "claude-sonnet-4-6",
]


class ChatProgressLive:
    """
    Context manager: hiển thị progress bar real-time trong khi agent xử lý.

    Timer đếm liên tục (không reset) vì Progress object được tạo một lần
    và chỉ update task description/completed, không tạo lại.
    """

    def __init__(self, session_id: str = "default") -> None:
        self.session_id = session_id
        self._live: Optional[Live] = None
        self._start = time.time()
        # Tạo Progress một lần duy nhất — timer sẽ đếm từ đây
        self._prog = Progress(
            TextColumn("[cyan]{task.description}"),
            BarColumn(bar_width=40, style="dim", complete_style="green",
                      finished_style="bold green"),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            expand=False,
        )
        self._task_id = self._prog.add_task("Starting…", total=100, completed=0)
        self._phase  = "Starting…"
        self._score  = 0.0
        self._rounds = 0
        self._pct    = 0.0

    def _build_panel(self) -> Panel:
        """Xây panel với Progress object hiện tại (không tạo mới)."""
        info = Table.grid(padding=(0, 1))
        info.add_column(style="dim",   width=12)
        info.add_column(style="white", width=50)
        if self.session_id:
            info.add_row("Session",  Text(self.session_id, style="yellow"))
        info.add_row("Phase",    Text(self._phase or "—", style="magenta"))
        info.add_row("Score",    Text(f"{self._score:.0%}",
                                      style="green" if self._score >= 1.0 else "yellow"))
        info.add_row("Rounds",   Text(str(self._rounds), style="cyan"))
        info.add_row("Elapsed",  Text(f"{time.time() - self._start:.0f}s", style="dim"))
        info.add_row("Progress", self._prog)
        return Panel(
            info,
            title="[bold blue]CHAT AGENT — Supervisor Progress[/bold blue]",
            border_style="blue" if self._pct < 100 else "green",
            padding=(1, 2),
        )

    def __enter__(self) -> "ChatProgressLive":
        self._live = Live(self._build_panel(), console=console,
                          refresh_per_second=4, vertical_overflow="visible")
        self._live.__enter__()
        return self

    def update(self, pct: float, phase: str, score: float, rounds: int) -> None:
        if not self._live:
            return
        self._pct    = max(0.0, min(100.0, pct))
        self._phase  = phase
        self._score  = score
        self._rounds = rounds
        # Cập nhật task trên Progress object hiện tại — timer không bị reset
        self._prog.update(self._task_id,
                          description=phase or "Processing…",
                          completed=self._pct)
        self._live.update(self._build_panel())

    def __exit__(self, *args) -> None:
        if self._live:
            self._live.__exit__(*args)