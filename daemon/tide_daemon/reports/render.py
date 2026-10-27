"""HTML raportu porannego: index.html (przegląd) i strona każdego projektu-repozytorium z ruchem.

Strony są samodzielne: CSS z palety wpisany w plik, czcionki Geist/Geist Mono z systemu
(@font-face z local(), zapasowo czcionka systemowa), bez sieci i bez zewnętrznych plików. Jedyny
skrypt to przycisk „Kopiuj”, a Content-Security-Policy pozwala tylko na niego (skrót SHA-256) —
nawet gdyby coś umknęło escapowaniu, wstrzyknięty skrypt się nie wykona.

Escapowanie: każdy tekst z zewnątrz (tematy commitów, autorzy, nazwy gałęzi i plików, tytuły
sesji, wiadomości agentów, ścieżki) przechodzi przez esc() — także w atrybutach.

Układ indeksu: przegląd — karta na projekt-repozytorium z 1–2 zdaniami „co się działo”, licznikami
(zrobione, w toku, czeka, porządki) i pozycjami „Czeka na mnie” (projekty, w których coś czeka, pierwsze
i wyróżnione), zwinięta sekcja „Poza projektami” (sesje spoza repozytoriów) i zwinięte „Szczegóły”.
Strona projektu: na górze jego „Na daily” (standup.py, summarize.py) — praca: Zrobione / W toku /
Czeka na mnie / Dalej i „Na czym polega”, zwinięte „Porządki”, „Kopiuj jako tekst” tylko dla tego
projektu; surowe dane (plany, sesje, commity po gałęziach, worktrees) zwinięte niżej w „Szczegóły”.

Linki do programów: zamiast własnego schematu tide:// (który mógłby wywołać każda strona w
przeglądarce) raport daje ścieżki z przyciskiem „Kopiuj”, gotowe polecenia (Konsole, Rider,
wznowienie sesji) do skopiowania i zwykły link file:// do katalogu.
"""
import base64
import hashlib
import html
import json
import os
import re
import shlex
import urllib.parse
from datetime import datetime

from .. import codex_homes
from . import standup as standup_mod
from .schedule import local

DAYS = ("poniedziałek", "wtorek", "środa", "czwartek", "piątek", "sobota", "niedziela")
DAYS_SHORT = ("pon.", "wt.", "śr.", "czw.", "pt.", "sob.", "niedz.")
MONTHS = ("stycznia", "lutego", "marca", "kwietnia", "maja", "czerwca", "lipca", "sierpnia", "września",
          "października", "listopada", "grudnia")
MONTHS_SHORT = ("sty", "lut", "mar", "kwi", "maj", "cze", "lip", "sie", "wrz", "paź", "lis", "gru")
TOOLS = {"claude": "Claude", "codex": "Codex"}
TOOL_COLOR = {"claude": "lavender", "codex": "sky"}
STATE_WORDS = {"waiting": "czeka", "working": "pracuje", "done": "skończył", "idle": "bezczynna",
               "ended": "zakończona"}
REASON_WORDS = {"approval": "na zgodę", "question": "na odpowiedź", "background": "zadanie w tle",
                "scheduled": "zaplanowane"}
EXCERPT = 700
COMMITS_SHOWN = 40
RUNS_SHOWN = 6
FILES_SHOWN = 40

SCRIPT = """document.addEventListener("click",function(ev){
var b=ev.target.closest("button[data-copy]");if(!b)return;var t=b.getAttribute("data-copy");
function ok(){b.classList.add("done");b.textContent="Skopiowano";
setTimeout(function(){b.classList.remove("done");b.textContent=b.getAttribute("data-label")},1400)}
function fallback(){var a=document.createElement("textarea");a.value=t;a.setAttribute("readonly","");
a.style.position="fixed";a.style.opacity="0";document.body.appendChild(a);a.select();
try{document.execCommand("copy");ok()}catch(e){}a.remove()}
if(navigator.clipboard&&navigator.clipboard.writeText){navigator.clipboard.writeText(t).then(ok,fallback)}else{fallback()}
});"""
SCRIPT_HASH = base64.b64encode(hashlib.sha256(SCRIPT.encode("utf-8")).digest()).decode("ascii")
CSP = (f"default-src 'none'; style-src 'unsafe-inline'; script-src 'sha256-{SCRIPT_HASH}'; img-src data:; "
       "base-uri 'none'; form-action 'none'")


# --- tekst ------------------------------------------------------------------------------------

def esc(value) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def plural(n: int, one: str, few: str, many: str) -> str:
    if n == 1:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def count(n: int, one: str, few: str, many: str) -> str:
    return f"{n} {plural(n, one, few, many)}"


def clock(ts: float) -> str:
    return local(ts).strftime("%H:%M") if ts else ""


def day_time(ts: float) -> str:
    """„sob. 24 paź 21:40”."""
    if not ts:
        return ""
    dt = local(ts)
    return f"{DAYS_SHORT[dt.weekday()]} {dt.day} {MONTHS_SHORT[dt.month - 1]} {dt:%H:%M}"


def long_date(day: str) -> str:
    """„poniedziałek, 26 października 2026”."""
    try:
        dt = datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        return day
    return f"{DAYS[dt.weekday()]}, {dt.day} {MONTHS[dt.month - 1]} {dt.year}"


def short_date(day: str) -> str:
    try:
        dt = datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        return day
    return f"{DAYS_SHORT[dt.weekday()]} {dt.day} {MONTHS_SHORT[dt.month - 1]}"


def duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds} s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} h {minutes} min" if minutes else f"{hours} h"
    days, hours = divmod(hours, 24)
    return f"{count(days, 'dzień', 'dni', 'dni')} {hours} h" if hours else count(days, "dzień", "dni", "dni")


def rrule_text(rrule: str) -> str:
    """„RRULE:FREQ=MINUTELY;INTERVAL=30” → „co 30 min”."""
    parts = dict(p.split("=", 1) for p in re.sub(r"^RRULE:", "", rrule or "").split(";") if "=" in p)
    freq, interval = parts.get("FREQ", ""), parts.get("INTERVAL", "1")
    n = int(interval) if interval.isdigit() and int(interval) > 0 else 1
    if freq == "MINUTELY":
        return f"co {n} min"
    if freq == "HOURLY":
        return "co godzinę" if n == 1 else f"co {n} h"
    if freq == "DAILY":
        return "codziennie" if n == 1 else f"co {n} dni"
    if freq == "WEEKLY":
        return "co tydzień" if n == 1 else f"co {n} tyg."
    return rrule or ""


def excerpt(text: str, limit: int = EXCERPT) -> tuple[str, bool]:
    """Początek tekstu do granicy akapitu albo linii (bez ucinania w pół słowa). (wycinek, czy ucięty)."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text, False
    cut = text[:limit]
    for sep in ("\n\n", "\n", ". ", " "):
        pos = cut.rfind(sep)
        if pos > limit * 0.5:
            cut = cut[:pos + (1 if sep == ". " else 0)]
            break
    return cut.rstrip() + " …", True


_CODE = re.compile(r"`([^`\n]{1,300})`")
_BOLD = re.compile(r"\*\*([^*\n]{1,300})\*\*")


def rich(text: str) -> str:
    """Wiadomość agenta jako HTML: najpierw escapowanie całości, potem tylko `kod` i **pogrubienie**
    z markdownu (wzorce działają na już escapowanym tekście, więc nie wprowadzą żadnego znacznika
    z treści). Reszta markdownu zostaje tekstem — odstępy zachowuje white-space: pre-wrap."""
    out = esc(text)
    out = _CODE.sub(lambda m: f"<code>{m.group(1)}</code>", out)
    return _BOLD.sub(lambda m: f"<b>{m.group(1)}</b>", out)


def file_url(path: str) -> str:
    return "file://" + urllib.parse.quote(path, safe="/")


def home_short(path: str, home: str) -> str:
    if home and (path == home or path.startswith(home.rstrip("/") + "/")):
        return "~" + path[len(home.rstrip("/")):]
    return path


# --- paleta i styl ------------------------------------------------------------------------------

def rgba(color: str, alpha: float) -> str:
    color = color.lstrip("#")
    r, g, b = int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)
    return f"rgba({r}, {g}, {b}, {alpha:g})"


def css(palette: dict) -> str:
    bg, text, pastel = palette["background"], palette["text"], palette["pastel"]
    radius, opacity, fonts = palette["radius"], palette["opacity"], palette["fonts"]
    ui, mono = fonts["ui"], fonts["mono"]
    tokens = {
        "--bg": bg["desktop"], "--view": bg["view"], "--window": bg["window"], "--titlebar": bg["titlebar"],
        "--card": bg["card"], "--panel": bg["panel"],
        "--text": text["primary"], "--text2": text["secondary"], "--muted": text["muted"],
        "--accent": palette["accent"],
        "--border": rgba(text["primary"], opacity["border"]), "--outline": rgba(text["primary"], opacity["outline"]),
        "--track": rgba(text["primary"], 0.1),
        "--r-small": f"{radius['small']}px", "--r-tile": f"{radius['tile']}px", "--r-card": f"{radius['card']}px",
        "--r-window": f"{radius['window']}px",
    }
    for name, value in pastel.items():
        tokens[f"--{name}"] = value
        tokens[f"--{name}-bg"] = rgba(value, 0.08)
        tokens[f"--{name}-line"] = rgba(value, 0.3)
    root = ";".join(f"{k}:{v}" for k, v in tokens.items())
    faces = _faces("Tide UI", ui) + _faces("Tide Mono", mono)
    return faces + ":root{" + root + ";--ui:\"Tide UI\"," + json.dumps(ui) + ",system-ui,sans-serif;--mono:\"Tide Mono\"," \
        + json.dumps(mono) + ",ui-monospace,monospace;color-scheme:dark}" + STYLE


def _faces(alias: str, name: str) -> str:
    """@font-face z czcionką zainstalowaną w systemie (local) — bez plików i bez sieci."""
    nospace = name.replace(" ", "")
    out = []
    for weight, style in ((400, "Regular"), (500, "Medium"), (600, "SemiBold")):
        sources = [f'local("{name} {style}")', f'local("{nospace}-{style}")']
        if weight == 400:
            sources.append(f'local("{name}")')
        out.append(f'@font-face{{font-family:"{alias}";font-weight:{weight};src:{",".join(sources)}}}')
    return "".join(out)


STYLE = """
*{box-sizing:border-box}
html{background:var(--bg)}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.55 var(--ui);-webkit-font-smoothing:antialiased;
text-rendering:optimizeLegibility}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
a:focus-visible,button:focus-visible,summary:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:6px}
.wrap{max-width:1480px;margin:0 auto;padding:44px 40px 72px}
.mono,code{font-family:var(--mono);font-size:.92em}
.muted{color:var(--muted)}
.top{display:flex;flex-wrap:wrap;align-items:flex-end;justify-content:space-between;gap:20px 32px;margin-bottom:28px}
.brand{display:flex;align-items:center;gap:12px;color:var(--text2);font-weight:500;font-size:14px;margin-bottom:14px}
.brand svg{width:30px;height:30px;flex:none}
.brand a{color:var(--text2)}
h1{font-size:36px;line-height:1.15;font-weight:600;letter-spacing:-.02em;margin:0}
h2{font-size:13px;font-weight:600;text-transform:uppercase;letter-spacing:.08em;color:var(--text2);margin:36px 0 14px;
display:flex;align-items:center;gap:10px}
h2 .n{font-family:var(--mono);color:var(--muted);font-weight:500;letter-spacing:0}
h3{font-size:16px;font-weight:600;margin:0;line-height:1.35;overflow-wrap:anywhere}
.period{color:var(--muted);font-size:14px;margin-top:6px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:8px 0 4px}
.stat{background:var(--card);border:1px solid var(--border);border-radius:var(--r-tile);padding:14px 16px}
.stat b{display:block;font:600 26px/1.15 var(--mono);color:var(--text);letter-spacing:-.01em}
.stat span{color:var(--text2);font-size:13px}
.stat small{color:var(--muted);font-family:var(--mono);font-size:12px;margin-left:6px}
.stat.hot{background:var(--peach-bg);border-color:var(--peach-line)}
.stat.hot b{color:var(--peach)}
.card{display:block;background:var(--card);border:1px solid var(--border);border-radius:var(--r-card);padding:18px 20px;
color:var(--text);min-width:0}
a.card:hover{text-decoration:none;border-color:var(--outline);background:color-mix(in srgb,var(--card) 92%,var(--text))}
.card.wait{background:var(--peach-bg);border-color:var(--peach-line)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:16px}
.stack{display:grid;gap:14px}
.row{display:flex;align-items:center;gap:8px 12px;flex-wrap:wrap}
.meta{color:var(--muted);font-size:13px;display:flex;flex-wrap:wrap;gap:4px 14px;margin-top:6px}
.meta .mono{font-size:12.5px}
.dot{width:9px;height:9px;border-radius:50%;flex:none;display:inline-block;background:var(--muted)}
.dot.waiting{background:var(--peach)}
.dot.working{background:var(--teal);box-shadow:0 0 0 3px var(--teal-bg)}
.dot.done{background:transparent;border:2px solid var(--sky)}
.dot.idle,.dot.ended{background:transparent;border:2px solid var(--muted)}
.state{font-size:13px;font-weight:500}
.state.waiting{color:var(--peach)}.state.working{color:var(--teal)}.state.done{color:var(--sky)}
.state.idle,.state.ended{color:var(--muted)}
.chip{display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:500;line-height:1;padding:5px 9px;
border-radius:999px;background:var(--track);color:var(--text2);white-space:nowrap}
.chip.mono{font-family:var(--mono);font-weight:500;font-size:11.5px}
.chip.lavender{background:var(--lavender-bg);color:var(--lavender);box-shadow:inset 0 0 0 1px var(--lavender-line)}
.chip.sky{background:var(--sky-bg);color:var(--sky);box-shadow:inset 0 0 0 1px var(--sky-line)}
.chip.teal{background:var(--teal-bg);color:var(--teal);box-shadow:inset 0 0 0 1px var(--teal-line)}
.chip.peach{background:var(--peach-bg);color:var(--peach);box-shadow:inset 0 0 0 1px var(--peach-line)}
.chip.butter{background:var(--butter-bg);color:var(--butter);box-shadow:inset 0 0 0 1px var(--butter-line)}
.chip.mint{background:var(--mint-bg);color:var(--mint);box-shadow:inset 0 0 0 1px var(--mint-line)}
.chip.rose{background:var(--rose-bg);color:var(--rose);box-shadow:inset 0 0 0 1px var(--rose-line)}
.counts{display:flex;flex-wrap:wrap;gap:6px;margin-top:12px}
.pcard h3{font-size:18px}
.pcard .path{color:var(--muted);font-family:var(--mono);font-size:12px;margin-top:2px;overflow-wrap:anywhere}
.waitlist{display:grid;gap:8px}
.waitrow{display:grid;grid-template-columns:auto minmax(0,1fr) auto;align-items:center;gap:6px 14px;
background:var(--peach-bg);border:1px solid var(--peach-line);border-radius:var(--r-tile);padding:12px 16px;color:var(--text)}
a.waitrow:hover{text-decoration:none;background:color-mix(in srgb,var(--peach) 13%,transparent)}
.waitrow .what{min-width:0}
.waitrow .title{font-weight:500;overflow-wrap:anywhere}
.waitrow .when{font-family:var(--mono);font-size:12.5px;color:var(--peach);text-align:right;white-space:nowrap}
.calm{display:flex;align-items:center;gap:10px;color:var(--text2);background:var(--card);border:1px solid var(--border);
border-radius:var(--r-tile);padding:12px 16px}
.calm .dot{background:var(--mint)}
.limits{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px}
.meter{display:grid;grid-template-columns:5.5em minmax(0,1fr) 3.6em;align-items:center;gap:12px;margin-top:10px;font-size:13px}
.meter .bar{height:6px;border-radius:3px;background:var(--track);overflow:hidden}
.meter .fill{display:block;height:100%;border-radius:3px}
.meter .v{font-family:var(--mono);font-weight:600;text-align:right}
.meter .reset{grid-column:2 / 4;color:var(--muted);font-size:12px;margin-top:-6px}
.lvl-mint{background:var(--mint)}.lvl-butter{background:var(--butter)}.lvl-rose{background:var(--rose)}
.t-mint{color:var(--mint)}.t-butter{color:var(--butter)}.t-rose{color:var(--rose)}
details.quiet{background:var(--card);border:1px solid var(--border);border-radius:var(--r-card);padding:4px 20px}
details.quiet summary{cursor:pointer;padding:12px 0;color:var(--text2);font-weight:500}
details.quiet ul{list-style:none;margin:0 0 12px;padding:0;display:grid;gap:2px}
details.quiet li{display:flex;justify-content:space-between;gap:16px;padding:7px 0;border-top:1px solid var(--border);font-size:14px}
details.quiet li span:last-child{color:var(--muted);text-align:right}
.cols{display:grid;grid-template-columns:minmax(0,1fr);gap:8px 32px;align-items:start}
.session .head{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:8px}
.session .head .sp{flex:1}
.msg{white-space:pre-wrap;overflow-wrap:anywhere;font-size:14px;line-height:1.6;color:var(--text2);
background:var(--view);border:1px solid var(--border);border-radius:var(--r-small);padding:12px 14px;margin-top:12px;
max-height:none}
.msg b,.res b{color:var(--text);font-weight:600}
.msg code,.res code{font-size:12.5px;background:var(--track);color:var(--text);padding:1px 5px;border-radius:5px}
.msglabel{font-size:12px;color:var(--muted);margin-top:14px;text-transform:uppercase;letter-spacing:.06em;font-weight:600}
details.more summary{cursor:pointer;color:var(--accent);font-size:13px;margin-top:8px;width:max-content}
details.more[open] summary{margin-bottom:4px}
.runs{list-style:none;margin:10px 0 0;padding:0;display:grid;gap:8px}
.runs li{border-left:2px solid var(--sky-line);padding:2px 0 2px 12px}
.runs .when{font-family:var(--mono);font-size:12.5px;color:var(--text2)}
.runs .res{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13.5px;color:var(--text2);margin-top:2px}
.branch{padding:0;overflow:hidden}
.branch .bhead{padding:14px 18px;display:flex;gap:8px 12px;align-items:center;flex-wrap:wrap;border-bottom:1px solid var(--border)}
.branch .bname{font-family:var(--mono);font-size:13.5px;font-weight:600;overflow-wrap:anywhere}
.branch .bsum{margin-left:auto;font-family:var(--mono);font-size:12.5px;color:var(--muted);white-space:nowrap}
.commits{list-style:none;margin:0;padding:4px 0}
.commit{display:grid;grid-template-columns:6.2em minmax(0,1fr) auto;gap:2px 14px;padding:9px 18px;align-items:baseline}
.commit+.commit{border-top:1px solid var(--border)}
.commit .t{font-family:var(--mono);font-size:12.5px;color:var(--muted)}
.commit .s{overflow-wrap:anywhere}
.commit .s .merge{color:var(--muted)}
.commit .by{grid-column:2;color:var(--muted);font-size:12.5px;display:flex;flex-wrap:wrap;gap:4px 10px;align-items:center}
.commit .st{font-family:var(--mono);font-size:12px;white-space:nowrap;text-align:right}
.add{color:var(--mint)}.del{color:var(--rose)}
.commits details summary{cursor:pointer;color:var(--accent);font-size:13px;padding:8px 18px}
.list{list-style:none;margin:0;padding:0}
.list li{display:flex;flex-wrap:wrap;justify-content:space-between;gap:4px 16px;padding:10px 0;border-top:1px solid var(--border)}
.list li:first-child{border-top:0;padding-top:2px}
.list .l{min-width:0;overflow-wrap:anywhere}
.list .r{color:var(--muted);font-size:13px;font-family:var(--mono)}
.files{list-style:none;margin:8px 0 0;padding:0;font-family:var(--mono);font-size:12.5px;color:var(--text2);
display:grid;gap:3px}
.files .code{display:inline-block;width:2.4em;color:var(--butter)}
.pathbar{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin-top:12px}
.pathbar .p{font-family:var(--mono);font-size:13px;color:var(--text2);background:var(--card);border:1px solid var(--border);
border-radius:var(--r-small);padding:6px 10px;overflow-wrap:anywhere}
button.copy{font:500 12px/1 var(--ui);color:var(--text2);background:var(--track);border:1px solid var(--border);
border-radius:var(--r-small);padding:7px 10px;cursor:pointer}
button.copy:hover{color:var(--text);border-color:var(--outline)}
button.copy.done{color:var(--bg);background:var(--accent);border-color:var(--accent)}
.back{display:inline-flex;gap:8px;align-items:center;color:var(--text2);font-size:14px}
.empty{color:var(--muted);font-size:14px;padding:4px 0}
footer{margin-top:56px;padding-top:18px;border-top:1px solid var(--border);color:var(--muted);font-size:13px;
display:flex;flex-wrap:wrap;gap:8px 24px;justify-content:space-between}
.plan .bar{display:flex;height:6px;border-radius:3px;background:var(--track);overflow:hidden;margin-top:12px}
.plan .bar i{display:block;height:100%}
.tickets{list-style:none;margin:12px 0 0;padding:0;display:grid;gap:2px}
.tickets li{display:grid;grid-template-columns:6.5em 5.6em minmax(0,1fr);gap:4px 12px;padding:6px 0;
border-top:1px solid var(--border);font-size:13.5px;align-items:baseline}
.tickets li:first-child{border-top:0}
.tickets .tid{font-family:var(--mono);font-size:12.5px;color:var(--sky);overflow-wrap:anywhere}
.tickets .st{font-size:12.5px;font-weight:500}
.tickets .tn{grid-column:3;color:var(--muted);font-size:12.5px}
.st-czeka{color:var(--peach)}.st-w-toku{color:var(--teal)}.st-review{color:var(--lavender)}
.st-gotowe{color:var(--mint)}.st-todo,.st-odrzucone{color:var(--muted)}
.dhead{display:flex;align-items:center;gap:10px 14px;flex-wrap:wrap;margin:30px 0 14px}
.dhead h2{margin:0}
.dhead .sp{flex:1}
.dsrc{color:var(--muted);font-size:13px}
.standup{padding:20px 22px}
.standup+.standup{margin-top:14px}
.standup .row h3{font-size:19px}
.standup>.theme:first-child,.standup>.sgroups:first-child{margin-top:0}
.standup h3 a{color:var(--text)}
.theme{color:var(--text2);margin:8px 0 0;max-width:92ch;font-size:14.5px}
.theme b{color:var(--muted);font-weight:500;font-size:12.5px;text-transform:uppercase;letter-spacing:.06em;margin-right:8px}
.sgroups{display:grid;grid-template-columns:minmax(0,1fr);gap:18px 40px;margin-top:16px}
.sg h4{font-size:12px;text-transform:uppercase;letter-spacing:.08em;margin:0 0 8px;font-weight:600}
.sg.done h4{color:var(--mint)}.sg.doing h4{color:var(--teal)}.sg.waiting h4{color:var(--peach)}.sg.next h4{color:var(--text2)}
.sg ul{list-style:none;margin:0;padding:0;display:grid;gap:9px}
.sg li{position:relative;padding-left:16px;line-height:1.5;overflow-wrap:anywhere}
.sg li::before{content:"";position:absolute;left:0;top:.6em;width:6px;height:6px;border-radius:50%;background:var(--muted)}
.sg.done li::before{background:var(--mint)}.sg.doing li::before{background:var(--teal)}
.sg.waiting li::before{background:var(--peach)}
.sg .ref{font-family:var(--mono);font-size:12px;color:var(--sky);margin-right:7px;white-space:nowrap}
.sg .why,.sg .nx{display:block;color:var(--text2);font-size:13.5px;margin-top:1px}
.sg .nx{color:var(--muted)}
.brief{background:var(--card);border:1px solid var(--border);border-radius:var(--r-card);padding:16px 20px;margin:22px 0 0}
.brief h2{margin:0 0 8px}
.brief ul{margin:0;padding-left:18px;display:grid;gap:6px;line-height:1.5;max-width:100ch}
.sg.finished li::before{background:var(--mint)}
.sg.attention h4{color:var(--butter)}
.sg.attention li::before{background:var(--butter)}
.sg .chk{display:block;color:var(--peach);font-size:13px;margin-top:2px}
.sg li.more{color:var(--muted);font-size:13px}
.sg li.more::before{display:none}
.sg.chores li{color:var(--text2);font-size:14px}
.sg.chores li::before{background:var(--muted)}
.ovgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(380px,1fr));gap:16px}
.ov{display:flex;flex-direction:column;padding:18px 20px}
.ov h3{font-size:18px}
.ov h3 a{color:var(--text)}
.ov .headline{margin:8px 0 0;color:var(--text2);font-size:14.5px;line-height:1.55;max-width:80ch}
.ovwait{margin-top:14px;padding-top:12px;border-top:1px solid var(--peach-line)}
.ovwait h4{font-size:12px;text-transform:uppercase;letter-spacing:.08em;margin:0 0 6px;color:var(--peach);font-weight:600}
.ovwait ul{list-style:none;margin:0;padding:0;display:grid;gap:6px;font-size:14px;overflow-wrap:anywhere}
.ovwait .ref{font-family:var(--mono);font-size:12px;color:var(--sky);margin-right:7px}
.ovwait li.more{color:var(--muted)}
.ov .meta{margin-top:auto;padding-top:14px;justify-content:space-between;align-items:center}
.ov .go{font-size:13px}
details.chores{margin-top:18px;border-top:1px solid var(--border)}
details.chores summary{cursor:pointer;padding:12px 0 4px;color:var(--text2);font-size:12px;font-weight:600;
text-transform:uppercase;letter-spacing:.08em;width:max-content;max-width:100%}
details.chores summary .n{font-family:var(--mono);color:var(--muted);margin-left:6px;letter-spacing:0}
details.chores summary .muted{text-transform:none;letter-spacing:0;font-weight:400;margin-left:10px}
details.chores .sg{margin-top:8px}
details.outside{margin-top:28px}
details.outside summary .muted{margin-left:8px;font-weight:400}
.osession{padding:12px 0;border-top:1px solid var(--border)}
.osession .head{display:flex;align-items:center;gap:8px 10px;flex-wrap:wrap}
.osession .head .sp{flex:1}
.osession .head b{font-weight:500;overflow-wrap:anywhere}
.otask{font-size:14px;color:var(--text2);margin-top:6px;overflow-wrap:anywhere}
.otask b{color:var(--muted);font-weight:500;font-size:12px;text-transform:uppercase;letter-spacing:.06em;margin-right:8px}
details.details{margin-top:44px;border-top:1px solid var(--border)}
details.details>summary{cursor:pointer;padding:18px 0 6px;color:var(--text2);font-weight:600;font-size:13px;
text-transform:uppercase;letter-spacing:.08em;width:max-content;max-width:100%}
details.details>summary .muted{text-transform:none;letter-spacing:0;font-weight:400;margin-left:10px}
@media (min-width:900px){.sgroups{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media (min-width:1360px){.cols{grid-template-columns:minmax(0,1.08fr) minmax(0,1fr)}}
@media (min-width:2000px){.wrap{max-width:1720px}body{font-size:16px}}
@media (max-width:700px){
.wrap{padding:24px 16px 48px}h1{font-size:28px}.grid,.ovgrid{grid-template-columns:minmax(0,1fr)}
.waitrow{grid-template-columns:auto minmax(0,1fr)}.waitrow .when{grid-column:2;text-align:left}
.commit{grid-template-columns:minmax(0,1fr) auto;padding:9px 14px}.commit .t{grid-column:1;grid-row:1}
.commit .s{grid-column:1 / 3}.commit .by{grid-column:1 / 3}.commit .st{grid-column:2;grid-row:1}
.meter{grid-template-columns:4.8em minmax(0,1fr) 3.4em}}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
"""

LOGO = ('<svg viewBox="0 0 32 32" aria-hidden="true"><rect width="32" height="32" rx="10" fill="var(--card)"/>'
        '<g fill="none" stroke-width="2.6" stroke-linecap="round">'
        '<path d="M6 11.5q2.5-3 5 0t5 0t5 0t5 0" stroke="var(--teal)"/>'
        '<path d="M6 17q2.5-3 5 0t5 0t5 0t5 0" stroke="var(--lavender)"/>'
        '<path d="M6 22.5q2.5-3 5 0t5 0t5 0t5 0" stroke="var(--peach)"/></g></svg>')


def page(title: str, body: str, palette: dict) -> str:
    return ("<!doctype html>\n<html lang=\"pl\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            f"<meta http-equiv=\"Content-Security-Policy\" content=\"{esc(CSP)}\">"
            "<meta name=\"referrer\" content=\"no-referrer\">"
            f"<meta name=\"color-scheme\" content=\"dark\"><title>{esc(title)}</title>"
            f"<style>{css(palette)}</style></head><body><div class=\"wrap\">{body}</div>"
            f"<script>{SCRIPT}</script></body></html>\n")


def copy_button(text: str, label: str = "Kopiuj") -> str:
    return (f'<button type="button" class="copy" data-copy="{esc(text)}" data-label="{esc(label)}">'
            f'{esc(label)}</button>')


def level(remaining: float) -> str:
    return "mint" if remaining > 40 else "butter" if remaining > 15 else "rose"


def tool_chip(tool: str) -> str:
    return f'<span class="chip {TOOL_COLOR.get(tool, "")}">{esc(TOOLS.get(tool, tool or "?"))}</span>'


def state_of(session) -> tuple[str, str]:
    """(klasa stanu, słowo) — przerwana tura to „przerwana”, nie „skończył”."""
    state = session.state or "idle"
    word = STATE_WORDS.get(state, state)
    if state in ("done", "idle") and session.interrupted:
        word = "przerwana"
    reason = REASON_WORDS.get(session.reason, "")
    if state == "waiting" and reason:
        word = f"czeka {reason}"
    elif state == "working" and session.reason == "background":
        word = "pracuje w tle"
    return state, word


def period_text(report) -> str:
    span = report.end - report.start
    return f"{day_time(report.start)} – {day_time(report.end)} · {duration(span)}"


# --- indeks ---------------------------------------------------------------------------------------

def render_index(report, palette: dict, home: str = "") -> str:
    """Przegląd: karta na projekt (1–2 zdania, liczniki, „Czeka na mnie”), „Poza projektami”
    (zwinięte) i szczegóły (liczby, czekające sesje, projekty bez ruchu, limity)."""
    active, quiet = report.active, report.quiet
    repos, outside = _repos(report), _outside(report)
    commits = sum(p.commits for p in active)
    added = sum(b.added for p in active for b in p.branches)
    deleted = sum(b.deleted for p in active for b in p.branches)
    sessions = sum(len(p.sessions) for p in active)
    runs = sum(p.runs for p in active)
    waiting = report.waiting
    parts = [
        '<header class="top"><div>',
        f'<div class="brand">{LOGO}<span>Tide · raport poranny</span></div>',
        f'<h1>{esc(long_date(report.day).capitalize())}</h1>',
        f'<div class="period">Okres: {esc(period_text(report))}</div></div></header>',
        brief_section(report),
        overview(report, repos),
    ]
    if outside:
        parts.append(_outside_section(report, outside))
    parts += [
        '<details class="details"><summary>Szczegóły<span class="muted">liczby, czekające sesje, projekty bez '
        'ruchu, limity</span></summary>',
        '<section class="stats">',
        _stat(len(waiting), plural(len(waiting), "czeka na ciebie", "czekają na ciebie", "czeka na ciebie"),
              hot=bool(waiting)),
        _stat(len(repos), plural(len(repos), "projekt z ruchem", "projekty z ruchem", "projektów z ruchem")),
        _stat(commits, plural(commits, "commit", "commity", "commitów"),
              f'<small><span class="add">+{added}</span> <span class="del">−{deleted}</span></small>' if commits else ""),
        _stat(sessions, plural(sessions, "sesja agenta", "sesje agentów", "sesji agentów")),
    ]
    if runs:
        parts.append(_stat(runs, plural(runs, "przebieg zadania", "przebiegi zadań", "przebiegów zadań")))
    parts.append("</section>")

    parts.append(f'<h2>Czeka na ciebie <span class="n">{len(waiting)}</span></h2>')
    if waiting:
        parts.append('<div class="waitlist">')
        for project, s in waiting:
            target = f'{project.slug}.html#{session_anchor(s)}' if project.is_repo else f'#{session_anchor(s)}'
            parts.append(
                f'<a class="waitrow" href="{esc(target)}">'
                f'<span class="dot waiting"></span><span class="what"><span class="row">'
                f'<b>{esc(project.name)}</b>{tool_chip(s.tool)}<span class="state waiting">{esc(state_of(s)[1])}</span>'
                f'</span><span class="title">{esc(s.title)}</span></span>'
                f'<span class="when">od {esc(clock(s.since) or "?")} · {esc(duration(report.end - s.since)) if s.since else ""}</span></a>')
        parts.append("</div>")
    else:
        parts.append('<div class="calm"><span class="dot"></span>Nikt na ciebie nie czeka.</div>')

    if quiet:
        parts.append(f'<h2>Bez ruchu <span class="n">{len(quiet)}</span></h2>'
                     f'<details class="quiet"><summary>Pokaż projekty bez ruchu ({len(quiet)})</summary><ul>')
        for p in quiet:
            parts.append(f'<li><span>{esc(p.name)}</span><span>{esc(_standing(p))}</span></li>')
        parts.append("</ul></details>")

    parts.append(_limits(report))
    parts.append("</details>")
    parts.append(_footer(report, home, index=True))
    return page(f"Raport poranny · {short_date(report.day)}", "".join(parts), palette)


def _repos(report) -> list:
    return [p for p in report.active if getattr(p, "is_repo", True)]


def _outside(report) -> list:
    return [p for p in report.active if not getattr(p, "is_repo", True)]


# --- przegląd projektów i „Na daily” ---------------------------------------------------------------

def daily_text(report, projects, brief: bool = False) -> str:
    """Standup podanych projektów jako zwykły tekst (przycisk „Kopiuj jako tekst”); brief — z „Dziś
    w skrócie” na początku (kopiowanie całości). Ten sam generator co w oknie Tide (standup.as_text)."""
    summary = getattr(report, "standup", None) or {}
    heading = f"Na daily · {long_date(report.day)}"
    return standup_mod.as_text(summary, [p.name for p in projects], heading,
                               brief=summary.get("brief") if brief else None, notes=getattr(report, "notes", None))


def brief_section(report) -> str:
    """„Dziś w skrócie”: 3–5 zdań o wszystkich projektach (model albo reguły)."""
    sentences = [b for b in (getattr(report, "standup", None) or {}).get("brief") or [] if b]
    if not sentences:
        return ""
    rows = "".join(f"<li>{esc(b)}</li>" for b in sentences)
    return f'<section class="brief"><h2>Dziś w skrócie</h2><ul>{rows}</ul></section>'


def _finished(notes: list) -> str:
    """„Skończone dziś” — notki po tickecie (reports/notes.py) z ręcznym sprawdzeniem."""
    rows = []
    for note in notes or []:
        check = ""
        if note.get("checklist"):
            check = f'<span class="chk">Sprawdź ręcznie: {esc("; ".join(note["checklist"]))}</span>'
        elif note.get("manual"):
            check = '<span class="chk">Wymaga sprawdzenia ręcznego</span>'
        rows.append(f'<li>{_ref(note)}{esc(note.get("text"))}{check}</li>')
    return f'<div class="sg finished"><h4>{esc(standup_mod.FINISHED[1])}</h4><ul>{"".join(rows)}</ul></div>' if rows else ""


def _source(summary: dict) -> str:
    if summary.get("source") == "model":
        if summary.get("engine") == "local":
            source = f"opis: model lokalny {summary.get('model') or ''}".rstrip()
        else:
            source = f"opis: Claude {str(summary.get('model') or '').capitalize()}"
        if summary.get("note"):
            source += f" · {summary['note']}"
        return source
    return summary.get("note") or "opis z reguł (bez modelu)"


def overview(report, projects) -> str:
    """Indeks: karta na projekt — 1–2 zdania „co się działo”, liczniki (zrobione, w toku, czeka,
    porządki), pozycje „Czeka na mnie” i odnośnik do strony projektu z pełnym „Na daily”. Projekty,
    w których coś czeka, są pierwsze i wyróżnione, a te z samymi porządkami — na końcu."""
    summary = getattr(report, "standup", None) or {}
    by_name = summary.get("projects") or {}
    text = daily_text(report, projects, brief=True)
    out = ['<section class="daily"><div class="dhead"><h2>Projekty <span class="n">'
           f'{len(projects)}</span></h2><span class="dsrc">{esc(_source(summary))}</span><span class="sp"></span>'
           + (copy_button(text, "Kopiuj wszystko jako tekst") if text else "") + "</div>"]
    if not projects:
        out.append('<div class="calm"><span class="dot"></span>W tym okresie nie ma nic do opowiedzenia — '
                   'ani commitów, ani sesji agentów w projektach.</div></section>')
        return "".join(out)
    cards = []
    chores = []
    notes = getattr(report, "notes", None) or {}
    for i, p in enumerate(projects):
        s = by_name.get(p.name) or {}
        waits = [item for item in s.get("waiting") or [] if not item.get("more")]
        hot = bool(waits or p.waiting)
        n = standup_mod.counts(s)
        chores_only = bool(n.get("chores")) and not any(n.get(k) for k in standup_mod.GROUP_KEYS + ("attention",))
        if chores_only and not hot and not notes.get(p.name):
            chores.append(p)   # same porządki — nie karta, tylko nazwa w zwiniętej linii pod kartami
            continue
        cards.append((not hot, chores_only, i, _overview_card(p, s, waits, hot)))
    out.append('<div class="ovgrid">' + "".join(card for *_key, card in sorted(cards)) + "</div>")
    if chores:
        rows = "".join(f'<li><a href="{esc(p.slug)}.html">{esc(p.name)}</a><span>'
                       f'{esc("; ".join(i["text"] for i in (by_name.get(p.name) or {}).get("chores") or [] if not i.get("more")))}'
                       '</span></li>' for p in chores)
        out.append(f'<details class="quiet chores-only"><summary>Porządki: {esc(", ".join(p.name for p in chores))}'
                   f'</summary><ul>{rows}</ul></details>')
    out.append("</section>")
    return "".join(out)


def _overview_card(p, s: dict, waits: list, hot: bool) -> str:
    n = standup_mod.counts(s)
    chips = []
    for key, word, tone in (("done", "zrobione", "mint"), ("doing", "w toku", "teal"), ("waiting", "czeka", "peach"),
                            ("attention", "uwaga", "butter"), ("chores", "porządki", "")):
        if n.get(key):
            chips.append(f'<span class="chip{" " + tone if tone else ""}">{esc(word)} {n[key]}</span>')
    headline = s.get("headline") or s.get("theme") or ""
    body = [f'<article class="card ov{" wait" if hot else ""}">',
            f'<div class="row"><h3><a href="{esc(p.slug)}.html">{esc(p.name)}</a></h3>'
            f'{"".join(tool_chip(t) for t in sorted({x.tool for x in p.sessions}))}</div>']
    if headline:
        body.append(f'<p class="headline">{esc(headline)}</p>')
    else:
        body.append('<p class="headline muted">Ruch bez opisu — szczegóły na stronie projektu.</p>')
    for plan in getattr(p, "plans", None) or ():
        if plan["total"]:
            label = f'{plan["label"]} · ' if plan["label"] else "plan "
            chips.append(f'<span class="chip sky">{esc(label)}{plan["done"]}/{plan["total"]}</span>')
    if chips:
        body.append(f'<div class="counts">{"".join(chips)}</div>')
    if waits:
        rows = "".join(f'<li>{_ref(item)}{esc(item.get("text"))}</li>' for item in waits[:3])
        more = f'<li class="more">i {len(waits) - 3} więcej</li>' if len(waits) > 3 else ""
        body.append(f'<div class="ovwait"><h4>Czeka na mnie</h4><ul>{rows}{more}</ul></div>')
    meta = [count(p.commits, "commit", "commity", "commitów"), count(len(p.sessions), "sesja", "sesje", "sesji")]
    if p.last_ts:
        meta.append(f"ostatnio {day_time(p.last_ts)}")
    body.append(f'<div class="meta"><span>{esc(" · ".join(meta))}</span>'
                f'<a class="go" href="{esc(p.slug)}.html">Na daily i szczegóły →</a></div></article>')
    return "".join(body)


def daily(report, projects, index: bool = False) -> str:
    """Sekcja „Na daily” strony projektu: praca (Zrobione / W toku / Czeka na mnie / Dalej), „Na czym
    polega” i zwinięte „Porządki”, z „Kopiuj jako tekst” tylko dla tych projektów."""
    summary = getattr(report, "standup", None) or {}
    by_name = summary.get("projects") or {}
    text = daily_text(report, projects)
    out = ['<section class="daily"><div class="dhead"><h2>Na daily</h2>'
           f'<span class="dsrc">{esc(_source(summary))}</span><span class="sp"></span>'
           + (copy_button(text, "Kopiuj jako tekst") if text else "") + "</div>"]
    shown = 0
    for p in projects:
        s = by_name.get(p.name)
        if not s or standup_mod.empty(s):
            continue
        shown += 1
        out.append('<article class="card standup">')
        if index:
            out.append(f'<div class="row"><h3><a href="{esc(p.slug)}.html">{esc(p.name)}</a></h3></div>')
        if s.get("theme"):
            out.append(f'<p class="theme"><b>Na czym polega</b>{esc(s["theme"])}</p>')
        groups = [_finished((getattr(report, "notes", None) or {}).get(p.name))] \
            if (getattr(report, "notes", None) or {}).get(p.name) else []
        for key, label in standup_mod.TEXT_ORDER[:-1]:
            items = s.get(key) or []
            if key == "attention" and not items:
                continue
            if not items:
                continue
            rows = "".join(_daily_item(item) for item in items)
            groups.append(f'<div class="sg {key}"><h4>{esc(label)}</h4><ul>{rows}</ul></div>')
        if groups:
            out.append(f'<div class="sgroups">{"".join(groups)}</div>')
        elif s.get("chores"):
            out.append('<div class="empty">Bez zmian w produkcie — same porządki.</div>')
        chores = s.get("chores") or []
        if chores:
            n = standup_mod.counts(s)["chores"]
            rows = "".join(_daily_item(item) for item in chores)
            out.append(f'<details class="chores"><summary>{esc(standup_mod.CHORES[1])} <span class="n">{n}</span>'
                       '<span class="muted">tracker, plany i instrukcje agentów, konfiguracja</span></summary>'
                       f'<div class="sg chores"><ul>{rows}</ul></div></details>')
        out.append("</article>")
    if not shown:
        out.append('<div class="calm"><span class="dot"></span>W tym okresie nie ma nic do opowiedzenia — '
                   'ani skończonej pracy, ani pracy w toku.</div>')
    out.append("</section>")
    return "".join(out)


def _ref(item: dict) -> str:
    return f'<span class="ref">{esc(item["ref"])}</span>' if item.get("ref") else ""


def _daily_item(item: dict) -> str:
    if item.get("more"):
        return f'<li class="more">{esc(item.get("text"))}</li>'
    about = f'<span class="why">{esc(item["about"])}</span>' if item.get("about") else ""
    nxt = f'<span class="nx">dalej: {esc(item["next"])}</span>' if item.get("next") else ""
    return f'<li>{_ref(item)}{esc(item.get("text"))}{about}{nxt}</li>'


def _outside_section(report, projects) -> str:
    """Sesje spoza repozytoriów git (korzeń przestrzeni roboczej, katalogi tymczasowe): bez „Na daily”,
    zwinięte; czekająca sesja jest też w „Czeka na ciebie” (odnośnik do kotwicy tutaj)."""
    sessions = [(p, s) for p in projects for s in p.sessions]
    waiting = sum(1 for _p, s in sessions if s.state == "waiting")
    label = count(len(sessions), "sesja", "sesje", "sesji") + (f" · {waiting} czeka" if waiting else "")
    out = [f'<details class="quiet outside"{" open" if waiting else ""}><summary>Poza projektami '
           f'<span class="muted">{esc(label)} w katalogach bez repozytorium</span></summary><div class="stack">']
    order = {"waiting": 0, "working": 1}
    for p, s in sorted(sessions, key=lambda ps: (order.get(ps[1].state, 2), -ps[1].last_ts)):
        state, word = state_of(s)
        # Sesja, która czeka: o co pyta według obszaru Agents (waitingText), nigdy stare pytanie z tekstu.
        said = standup_mod.clean(getattr(s, "waiting_text", "") or "", 320) if state == "waiting" else \
            standup_mod.first_sentences(s.final_message, 2, 320)
        task = standup_mod.clean(getattr(s, "prompt", "") or "", 300)
        resume = resume_command(s)
        out.append(f'<article class="osession" id="{esc(session_anchor(s))}"><div class="head">'
                   f'<span class="dot {esc(state)}"></span><span class="state {esc(state)}">{esc(word)}</span>'
                   f'{tool_chip(s.tool)}<b>{esc(s.title)}</b><span class="sp"></span>{copy_button(resume, "Wznów")}</div>'
                   f'<div class="meta"><span class="mono">{esc(p.path)}</span><span>ostatnio {esc(day_time(s.last_ts))}</span></div>'
                   + (f'<div class="otask"><b>Zadanie</b>{esc(task)}</div>' if task and task != s.title else "")
                   + (f'<div class="otask"><b>{"Pyta" if state == "waiting" else "Wynik"}</b>{esc(said)}</div>' if said else "")
                   + "</article>")
    out.append("</div></details>")
    return "".join(out)


def _stat(value: int, label: str, extra: str = "", hot: bool = False) -> str:
    return f'<div class="stat{" hot" if hot else ""}"><b>{value}{extra}</b><span>{esc(label)}</span></div>'


def _standing(p) -> str:
    bits = []
    if p.to_clean:
        bits.append(f"{len(p.to_clean)} do sprzątnięcia")
    if p.unpushed:
        bits.append(f"{len(p.unpushed)} z pracą tylko tutaj")
    if p.main_dirty_count:
        bits.append(f"{count(p.main_dirty_count, 'zmiana', 'zmiany', 'zmian')} w głównym katalogu")
    return " · ".join(bits) or "nic do zrobienia"


def _limits(report) -> str:
    snap = report.limits or {}
    out = ['<h2>Limity AI</h2>']
    if not snap.get("ok"):
        message = snap.get("error") or "brak danych"
        out.append(f'<div class="calm"><span class="dot" style="background:var(--muted)"></span>{esc(message)}</div>')
        return "".join(out)
    out.append('<div class="limits">')
    for service in snap.get("services") or ():
        read = service.get("lastRead") or ""
        read_text = f"odczyt {read[11:16]}" if len(read) >= 16 else "brak odczytu"
        rows = []
        for m in service.get("metrics") or ():
            value = max(0.0, min(100.0, float(m["remaining"])))
            lvl = level(value)
            rows.append(f'<div class="meter"><span>{esc(m["label"])}</span><span class="bar">'
                        f'<span class="fill lvl-{lvl}" style="width:{value:.0f}%"></span></span>'
                        f'<span class="v t-{lvl}">{value:.0f}%</span>'
                        + (f'<span class="reset">reset {esc(m["reset"])}</span>' if m.get("reset") else "") + "</div>")
        if not rows:
            rows.append('<div class="empty">brak odczytu limitów</div>')
        out.append(f'<div class="card"><div class="row"><h3>{esc(service.get("name"))}</h3>'
                   f'<span class="muted" style="font-size:13px">{esc(read_text)}</span></div>{"".join(rows)}</div>')
    out.append("</div>")
    if report.forecast:
        out.append('<div class="stack" style="margin-top:12px">')
        for item in report.forecast:
            out.append(f'<div class="calm"><b>{esc(item.get("name"))}</b> {esc(item.get("text"))}</div>')
        out.append("</div>")
    return "".join(out)


def _footer(report, home: str, index: bool) -> str:
    prev = ""
    if index and report.previous_day:
        prev = f'<a href="../{esc(report.previous_day)}/index.html">← poprzedni raport ({esc(short_date(report.previous_day))})</a>'
    summary = getattr(report, "standup", None) or {}
    if summary.get("source") == "model" and summary.get("engine") == "local":
        where = ('Dane z tego komputera: git i dzienniki agentów. Opis „Na daily”: model lokalny '
                 f'{esc(summary.get("model"))} ({esc(summary.get("host"))}) — ')
        where += ('fakty z raportu, bez sekretów, idą tylko do tego serwera modeli.' if summary.get("remote")
                  else 'nic nie wychodzi z tego komputera.')
    elif summary.get("source") == "model":
        where = ('Dane z tego komputera: git i dzienniki agentów. Opis „Na daily”: model '
                 f'{esc(summary.get("model"))} przez lokalny Claude Code (fakty z raportu, bez sekretów, idą do Anthropic).')
    else:
        where = 'Tylko dane z tego komputera: git i dzienniki agentów, bez wysyłania czegokolwiek.'
    return (f'<footer><span>Wygenerowano {esc(day_time(report.generated))} przez tide-daemon. {where}</span>'
            f'{prev}</footer>')


# --- strona projektu -------------------------------------------------------------------------------

def resume_command(s) -> str:
    """Polecenie „Wznów” do schowka: `claude --resume`, `codex resume`, a dla sesji Codexa ze snapa
    (~/snap/codex/…) wprost `/snap/bin/codex resume` — tylko Codex ze snapa widzi jej katalog danych."""
    cd = f"cd {shlex.quote(s.cwd)} && "
    if s.tool == "claude":
        return cd + f"claude --resume {shlex.quote(s.session_id)}"
    program = codex_homes.SNAP_CODEX if codex_homes.is_snap(getattr(s, "path", "") or "") else "codex"
    return cd + f"{program} resume {shlex.quote(s.session_id)}"


def session_anchor(session) -> str:
    return "s-" + re.sub(r"[^A-Za-z0-9_-]", "-", session.id)


def render_project(report, project, palette: dict, home: str = "") -> str:
    p = project
    added = sum(b.added for b in p.branches)
    deleted = sum(b.deleted for b in p.branches)
    head = [
        f'<a class="back" href="index.html">← Raport · {esc(short_date(report.day))}</a>',
        '<header class="top"><div>',
        f'<div class="brand" style="margin-top:14px">{LOGO}<span>{esc(period_text(report))}</span></div>',
        f'<h1>{esc(p.name)}</h1>',
        _pathbar(p.path, p.is_repo),
        '</div></header>',
        daily(report, [p], index=False),
        '<details class="details"><summary>Szczegóły<span class="muted">plany, sesje agentów, commity, '
        'worktrees</span></summary>',
        '<section class="stats">',
        _stat(len(p.waiting), plural(len(p.waiting), "czeka na ciebie", "czekają na ciebie", "czeka na ciebie"),
              hot=bool(p.waiting)),
        _stat(p.commits, plural(p.commits, "commit", "commity", "commitów"),
              f'<small><span class="add">+{added}</span> <span class="del">−{deleted}</span></small>' if p.commits else ""),
        _stat(len(p.sessions), plural(len(p.sessions), "sesja agenta", "sesje agentów", "sesji agentów")),
    ]
    if p.runs:
        head.append(_stat(p.runs, plural(p.runs, "przebieg zadania", "przebiegi zadań", "przebiegów zadań")))
    head.append(_stat(len(p.to_clean), "do sprzątnięcia"))
    head.append("</section>")

    left = [_plans(p)]
    left.append(f'<h2>Agenci <span class="n">{len(p.sessions)}</span></h2>')
    if p.sessions:
        order = {"waiting": 0, "working": 1}
        ordered = sorted(p.sessions, key=lambda s: (order.get(s.state, 2), -s.last_ts))
        left.append('<div class="stack">' + "".join(_session_card(s, report) for s in ordered) + "</div>")
    else:
        left.append('<div class="empty">Żadnej sesji agenta w tym okresie.</div>')

    right = [f'<h2>Commity <span class="n">{p.commits}</span></h2>']
    if p.branches:
        right.append('<div class="stack">' + "".join(_branch_card(b, home) for b in p.branches) + "</div>")
    else:
        right.append('<div class="empty">Żadnego commita w tym okresie.</div>')
    right.append(_worktrees(p, home))

    body = "".join(head) + f'<div class="cols"><div>{"".join(left)}</div><div>{"".join(right)}</div></div>' \
        + "</details>" + _footer(report, home, index=False)
    return page(f"{p.name} · raport {short_date(report.day)}", body, palette)


def _plans(p) -> str:
    """„Plany i tickety”: tablica koordynatora albo sam plan z repozytorium. Bez planów — nic."""
    plans = getattr(p, "plans", None) or []
    if not plans:
        return ""
    out = [f'<h2>Plany i tickety <span class="n">{len(plans)}</span></h2><div class="stack">']
    for plan in plans:
        c = plan["counts"]
        meta = []
        if plan["source"] == "board":
            meta.append(esc(f'koordynator · {TOOLS.get(plan["tool"], plan["tool"])}' + (" · działa" if plan["live"] else "")))
        else:
            meta.append(esc("z planu" + (f' · {plan["status"]}' if plan["status"] else "")))
        if plan["planPath"]:
            meta.append(f'<span class="mono">{esc(os.path.basename(plan["planPath"]))}</span>')
        if plan["tracker"] == "youtrack":
            meta.append("YouTrack" + (f' — {esc(plan["trackerError"])}' if plan["trackerError"] else ""))
        meta.append(esc(f'{plan["done"]} z {plan["total"]} gotowe'))
        total = max(1, plan["total"])
        bar = "".join(f'<i style="width:{100 * c[k] / total:.1f}%;background:var(--{color})"></i>'
                      for k, color in (("gotowe", "mint"), ("review", "lavender"), ("w-toku", "teal"),
                                       ("czeka", "peach")) if c[k])
        chips = []
        for key, word, tone in (("czeka", "czeka", "peach"), ("w-toku", "w toku", "teal"),
                                ("review", "review", "lavender"), ("todo", "do zrobienia", ""),
                                ("gotowe", "gotowe", "mint")):
            if c[key]:
                chips.append(f'<span class="chip {tone}">{c[key]} {esc(word)}</span>')
        rows = []
        for t in plan["tickets"]:
            note = f'<span class="tn">{esc(t.get("note"))}</span>' if t.get("note") else ""
            ident = esc(t.get("id") or "—")
            if t.get("url"):
                ident = f'<a href="{esc(t["url"])}">{ident}</a>'
            rows.append(f'<li><span class="tid">{ident}</span><span class="st st-{esc(t.get("state"))}">'
                        f'{esc(t.get("word"))}</span><span>{esc(t.get("title"))}</span>{note}</li>')
        if plan["hidden"]:
            rows.append(f'<li><span></span><span></span><span class="muted">+{plan["hidden"]} więcej</span></li>')
        errors = "".join(f'<div class="empty">plan: {esc(e)}</div>' for e in plan["errors"][:3])
        label = f'<span class="chip mono sky">{esc(plan["label"])}</span>' if plan["label"] else ""
        meta_html = "".join(f"<span>{m}</span>" for m in meta)
        out.append(f'<article class="card plan"><div class="row"><h3>{esc(plan["title"])}</h3>{label}</div>'
                   f'<div class="meta">{meta_html}</div>'
                   f'<div class="bar">{bar}</div><div class="counts">{"".join(chips)}</div>'
                   f'<ul class="tickets">{"".join(rows)}</ul>{errors}</article>')
    out.append("</div>")
    return "".join(out)


def _pathbar(path: str, is_repo: bool) -> str:
    quoted = shlex.quote(path)
    out = [f'<div class="pathbar"><span class="p">{esc(path)}</span>', copy_button(path, "Kopiuj ścieżkę"),
           copy_button(f"konsole --workdir {quoted}", "Konsole"), copy_button(f"rider {quoted}", "Rider"),
           f'<a href="{esc(file_url(path))}" style="font-size:13px">Otwórz katalog</a></div>']
    return "".join(out)


def _session_card(s, report) -> str:
    state, word = state_of(s)
    meta = []
    if s.branch:
        meta.append(f'<span class="mono">{esc(s.branch)}</span>')
    if s.worktree:
        meta.append(f'<span>worktree <span class="mono">{esc(s.worktree)}</span></span>')
    if s.first_ts and state in ("working", "waiting"):
        meta.append(f'<span>trwa {esc(duration(report.end - s.first_ts))}</span>')
    elif s.first_ts and s.last_ts:
        meta.append(f'<span>trwała {esc(duration(s.last_ts - s.first_ts))}</span>')
    meta.append(f'<span>ostatnio {esc(day_time(s.last_ts))}</span>')
    if s.last_action and state in ("working", "waiting"):
        meta.append(f'<span class="mono">{esc(s.last_action)}</span>')
    resume = resume_command(s)
    out = [f'<article class="card session{" wait" if state == "waiting" else ""}" id="{esc(session_anchor(s))}">',
           f'<div class="head"><span class="dot {esc(state)}"></span><span class="state {esc(state)}">{esc(word)}</span>'
           f'{tool_chip(s.tool)}'
           + (f'<span class="chip sky">zaplanowane</span>' if s.automation or s.runs else "")
           + f'<span class="sp"></span>{copy_button(resume, "Wznów")}</div>',
           f'<h3>{esc(s.title)}</h3><div class="meta">{"".join(meta)}</div>']
    if s.final_message:
        shown, cut = excerpt(s.final_message)
        out.append(f'<div class="msglabel">Ostatnia wiadomość · {esc(day_time(s.final_ts))}</div>'
                   f'<div class="msg">{rich(shown)}</div>')
        if cut:
            out.append(f'<details class="more"><summary>Cała wiadomość</summary><div class="msg">{rich(s.final_message)}</div></details>')
    elif state not in ("working", "waiting"):
        out.append('<div class="msglabel">Ostatnia wiadomość</div><div class="empty">brak w dzienniku</div>')
    if s.runs or s.automation:
        out.append(_runs(s))
    out.append("</article>")
    return "".join(out)


def _runs(s) -> str:
    auto = s.automation or {}
    name = auto.get("name") or "Zaplanowane zadanie"
    bits = [rrule_text(auto.get("rrule", ""))]
    if auto.get("status") and auto.get("status") != "ACTIVE":
        bits.append("wstrzymane")
    bits.append(count(len(s.runs), "przebieg", "przebiegi", "przebiegów") + " w okresie")
    out = [f'<div class="msglabel">{esc(name)} · {esc(" · ".join(b for b in bits if b))}</div>']
    if not s.runs:
        return "".join(out)
    runs = sorted(s.runs, key=lambda r: -r.started)
    out.append('<ul class="runs">' + "".join(_run(r) for r in runs[:RUNS_SHOWN]) + "</ul>")
    if len(runs) > RUNS_SHOWN:
        out.append(f'<details class="more"><summary>Starsze przebiegi ({len(runs) - RUNS_SHOWN})</summary>'
                   f'<ul class="runs">{"".join(_run(r) for r in runs[RUNS_SHOWN:])}</ul></details>')
    return "".join(out)


def _run(r) -> str:
    when = day_time(r.started)
    if r.ended:
        when += f" · {duration(r.ended - r.started)}"
    status = " · przerwany" if r.aborted else (" · trwa" if not r.ended else "")
    result, _cut = excerpt(r.result, 320)
    return (f'<li><div class="when">{esc(when + status)}</div>'
            + (f'<div class="res">{rich(result)}</div>' if result else "") + "</li>")


def _branch_card(b, home: str) -> str:
    chips = []
    if b.is_main:
        chips.append('<span class="chip mint">gałąź główna</span>')
    if b.worktree and not b.is_main_checkout:
        name = os.path.basename(b.worktree.rstrip("/"))
        chips.append(f'<span class="chip mono" title="{esc(home_short(b.worktree, home))}">worktree {esc(name)}</span>')
    if b.tool:
        chips.append(tool_chip(b.tool))
    n = len(b.commits)
    commits = sorted(b.commits, key=lambda c: -c.ts)
    rows = "".join(_commit(c) for c in commits[:COMMITS_SHOWN])
    more = ""
    if n > COMMITS_SHOWN:
        more = (f'<details><summary>Starsze commity ({n - COMMITS_SHOWN})</summary><ul class="commits">'
                + "".join(_commit(c) for c in commits[COMMITS_SHOWN:]) + "</ul></details>")
    return (f'<section class="card branch"><div class="bhead"><span class="bname">{esc(b.name)}</span>{"".join(chips)}'
            f'<span class="bsum">{esc(count(n, "commit", "commity", "commitów"))} · '
            f'<span class="add">+{b.added}</span> <span class="del">−{b.deleted}</span></span></div>'
            f'<ul class="commits">{rows}</ul>{more}</section>')


def _commit(c) -> str:
    by = [f'<span>{esc(c.author)}</span>', f'<span class="mono">{esc(c.sha[:8])}</span>']
    if c.agent:
        why = {"trailer": "stopka commita", "author": "autor", "branch": "gałąź agenta",
               "session": "w czasie sesji"}.get(c.agent_why, "")
        by.append(f'<span class="chip {TOOL_COLOR.get(c.agent, "")}" title="{esc(why)}">agent: {esc(TOOLS.get(c.agent, c.agent))}</span>')
    files = count(c.files, "plik", "pliki", "plików")
    stat = (f'<span class="add">+{c.added}</span> <span class="del">−{c.deleted}</span> · {esc(files)}'
            if c.files else '<span class="muted">bez zmian plików</span>')
    subject = (f'<span class="merge">scalenie · </span>{esc(c.subject)}' if c.merge else esc(c.subject))
    when = f"{DAYS_SHORT[local(c.ts).weekday()]} {clock(c.ts)}"
    return (f'<li class="commit"><span class="t" title="{esc(day_time(c.ts))}">{esc(when)}</span>'
            f'<span class="s">{subject}</span><span class="st">{stat}</span>'
            f'<span class="by">{"".join(by)}</span></li>')


def _worktrees(p, home: str) -> str:
    out = [f'<h2>Worktrees i zmiany</h2><div class="stack">']
    out.append('<div class="card"><h3>Do sprzątnięcia</h3>')
    if p.to_clean:
        out.append('<ul class="list" style="margin-top:10px">')
        for w in p.to_clean:
            size = _size(w.get("size", -1))
            out.append(f'<li><span class="l"><span class="mono">{esc(w.get("branch") or w.get("name"))}</span>'
                       f'<div class="muted" style="font-size:12.5px">{esc(home_short(w.get("path", ""), home))}</div></span>'
                       f'<span class="r">{esc(size)}</span></li>')
        out.append('</ul><div class="meta">Scalone z gałęzią główną i czyste — usuniesz je w oknie Worktrees.</div>')
    else:
        out.append('<div class="empty">Nic do sprzątnięcia.</div>')
    out.append("</div>")

    out.append('<div class="card"><h3>Praca tylko w worktree</h3>')
    if p.unpushed:
        out.append('<ul class="list" style="margin-top:10px">')
        for w in p.unpushed:
            bits = []
            if (w.get("unpushed") or 0) > 0:
                bits.append(count(w["unpushed"], "commit tylko tutaj", "commity tylko tutaj", "commitów tylko tutaj"))
            if (w.get("dirty") or 0) > 0:
                bits.append(count(w["dirty"], "zmiana", "zmiany", "zmian"))
            elif w.get("dirty") == -1:
                bits.append("stan nieznany")
            path = w.get("path", "")
            out.append(f'<li><span class="l"><span class="mono">{esc(w.get("branch") or w.get("name"))}</span>'
                       f'<div class="muted" style="font-size:12.5px">{esc(home_short(path, home))}</div></span>'
                       f'<span class="r">{esc(" · ".join(bits))} {copy_button(path, "Kopiuj ścieżkę")}</span></li>')
        out.append("</ul>")
    else:
        out.append('<div class="empty">Wszystko jest w gałęziach albo na serwerze.</div>')
    out.append("</div>")

    if p.is_repo:
        out.append('<div class="card"><h3>Niezacommitowane zmiany w głównym katalogu</h3>')
        if p.main_dirty:
            out.append('<ul class="files">' + "".join(
                f'<li><span class="code">{esc(code)}</span>{esc(path)}</li>' for code, path in p.main_dirty[:FILES_SHOWN])
                + "</ul>")
            if p.main_dirty_count > len(p.main_dirty[:FILES_SHOWN]):
                out.append(f'<div class="meta">i {p.main_dirty_count - len(p.main_dirty[:FILES_SHOWN])} więcej</div>')
        else:
            out.append('<div class="empty">Czysto.</div>')
        out.append("</div>")
    out.append("</div>")
    return "".join(out)


def _size(size) -> str:
    if not isinstance(size, (int, float)) or size < 0:
        return ""
    for unit, factor in (("GB", 1 << 30), ("MB", 1 << 20), ("kB", 1 << 10)):
        if size >= factor:
            return f"{size / factor:.1f} {unit}".replace(".", ",")
    return f"{int(size)} B"
