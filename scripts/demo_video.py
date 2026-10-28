"""Records out/cognee-gitgraph-demo.mp4: the real keyless demo, the real 3D page, a synthesized voice.

Order: gate (full tests and typecheck), run the demo and check its stdout says what the voice will
say, synthesize the voice, record at 1280x720 with Playwright, then ffmpeg to 1920x1080 H.264 + AAC
and probe the result. Needs the network (edge-tts via uvx), ffmpeg/ffprobe and Playwright chromium.
DEMO_VIDEO_SKIP_GATE=1 skips the gate for fast iteration; the tests slide then says so.
"""

import contextlib
import html
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from functools import partial
from pathlib import Path

ROOT = Path(__file__).parent.parent
OUT = ROOT / "out" / "cognee-gitgraph-demo.mp4"
VOICE = "en-US-AndrewNeural"
REPO_URL = "https://github.com/chrisbuildsonline/cognee-gitgraph"
CHROMIUM_ARGS = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"]

# What the narration says about the demo; each is checked against the real stdout.
FACTS = {
    "billing at 1 hop": r"^  billing  1 hop  ",
    "orders at 2 hops": r"^  orders  2 hops  ",
    "notifications at 4 hops": r"^  notifications  4 hops  ",
    "make_token changed": r"^Changed symbols \(2\): .*shared/auth\.py::make_token",
    "verify_token changed": r"^Changed symbols \(2\): .*shared/auth\.py::verify_token",
    "billing CI failure": r"^  billing-tests  billing  ",
    "orders CI failure": r"^  orders-tests  orders  ",
    "8 commits": r"^  commits: 8$",
    "15 file changes": r"^  file_changes: 15$",
    "8 symbols": r"^  symbols: 8$",
    "10 references": r"^  references: 10$",
    "2 CI failures": r"^  ci_failures: 2$",
}


def video_problems(probe: dict) -> list[str]:
    """What is wrong with the finished file, from ffprobe's JSON; empty when it is good."""
    streams = probe.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    problems = []
    if video is None:
        problems.append("no video stream")
    else:
        if video.get("width") != 1920:
            problems.append(f"width {video.get('width')}, wanted 1920")
        if video.get("height") != 1080:
            problems.append(f"height {video.get('height')}, wanted 1080")
        if video.get("codec_name") != "h264":
            problems.append(f"video codec {video.get('codec_name')}, wanted h264")
    if audio is None:
        problems.append("no audio stream")
    seconds = float(probe.get("format", {}).get("duration", 0))
    if seconds < 60:
        problems.append(f"too short: {seconds:.1f} s, wanted 60 to 180")
    elif seconds > 180:
        problems.append(f"too long: {seconds:.1f} s, wanted 60 to 180")
    return problems


def parse_pytest_passed(output: str) -> int:
    """The passed count from pytest's last summary line; raises unless the run was all green."""
    line = next(
        (ln for ln in reversed(output.splitlines()) if re.search(r" in \d+(\.\d+)?s", ln)), None
    )
    if line is None:
        raise ValueError("no pytest summary line in the output")
    counts = {word: int(n) for n, word in re.findall(r"(\d+) (\w+)", line)}
    if counts.get("failed") or counts.get("error") or counts.get("errors"):
        raise ValueError(f"pytest was not green: {line.strip()}")
    if not counts.get("passed"):
        raise ValueError(f"pytest passed nothing: {line.strip()}")
    return counts["passed"]


def missing_facts(stdout: str) -> list[str]:
    """The narrated facts the real demo output does not back up; empty when all of them hold."""
    problems = [
        f"missing: {name}" for name, rx in FACTS.items() if not re.search(rx, stdout, re.MULTILINE)
    ]
    if re.search(r"^  search\s", stdout, re.MULTILINE):
        problems.append("search is at risk, the narration says it is not")
    return problems


def assert_facts(stdout: str) -> None:
    problems = missing_facts(stdout)
    if problems:
        raise ValueError("the demo output does not match the narration: " + "; ".join(problems))


def audio_filter(offsets_ms: list[int]) -> str:
    """Voice clip N is ffmpeg input N+1 (input 0 is the recording): delay it, then mix them all."""
    delayed = [f"[{i + 1}:a]adelay={ms}|{ms}[a{i}]" for i, ms in enumerate(offsets_ms)]
    names = "".join(f"[a{i}]" for i in range(len(offsets_ms)))
    mix = f"{names}amix=inputs={len(offsets_ms)}:normalize=0[aud]"
    return ";".join([*delayed, mix])


def ffmpeg_command(
    webm: Path, clips: list[tuple[Path, int]], out: Path, stretch: float = 1.0
) -> list[str]:
    """`stretch` is wall clock over recording length: playwright's video runs slow on a loaded
    machine, so the picture is scaled in time to match the clock the voice offsets were taken on."""
    video = f"[0:v]setpts={stretch:.6f}*PTS,scale=1920:1080:flags=lanczos,fps=30[v]"
    graph = f"{video};{audio_filter([ms for _, ms in clips])}"
    inputs = ["-i", str(webm)]
    for file, _ in clips:
        inputs += ["-i", str(file)]
    return [
        "ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", graph,
        "-map", "[v]", "-map", "[aud]",
        "-c:v", "libx264", "-crf", "17", "-preset", "slow", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", str(out),
    ]  # fmt: skip


# ---- everything below needs the machine: subprocesses, network, a browser ----


def _run(cmd: list[str]) -> str:
    done = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=False)
    out = done.stdout + done.stderr
    if done.returncode:
        raise RuntimeError(f"{' '.join(cmd)} failed:\n{out[-2000:]}")
    return out


def _real_demo(html_path: Path) -> str:
    from cognee_community_connector_gitgraph.cli import main

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = main(["demo", "--html", str(html_path)])
    if code:
        raise RuntimeError(f"cognee-gitgraph demo exited {code}")
    return buf.getvalue().rstrip("\n")


def _speak(text: str, file: Path) -> int:
    """One edge-tts call at a time (60 s, three tries); returns the clip length in ms."""
    for attempt in range(1, 4):
        try:
            cmd = ["uvx", "edge-tts", "--voice", VOICE, "--rate=+8%", "--text", text]
            subprocess.run(
                [*cmd, "--write-media", str(file)], timeout=60, check=True, capture_output=True
            )
            break
        except (subprocess.SubprocessError, OSError):
            if attempt == 3:
                raise
    return round(_duration(file) * 1000)


def _duration(file: Path) -> float:
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0"]
    return float(
        subprocess.run([*cmd, str(file)], capture_output=True, text=True, check=True).stdout
    )


CSS = """
:root{--ink:#e6edf7;--dim:#9db0c8;--line:#26354d;--a1:#60a5fa;--ok:#34d399;--hot:#fb7185;--amber:#fbbf24}
*{box-sizing:border-box}
body{margin:0;height:100vh;background:#0a1220;color:var(--ink);font:19px/1.45 Inter,system-ui,sans-serif;overflow:hidden}
body::before{content:'';position:fixed;inset:0;background:radial-gradient(ellipse 60% 50% at 85% 0%,#12305c 0%,transparent 70%),radial-gradient(ellipse 50% 40% at 0% 100%,#0b2f3a 0%,transparent 70%);z-index:-1}
.stage{position:absolute;inset:0;padding:36px 56px 40px;display:flex;flex-direction:column;animation:enter .7s cubic-bezier(.2,.7,.2,1) both}
@keyframes enter{from{opacity:0;transform:translateY(14px)}}
.eyebrow{font:600 13px Inter,system-ui,sans-serif;letter-spacing:.16em;text-transform:uppercase;color:var(--a1);margin-bottom:6px}
h1{font:700 36px/1.15 Inter,system-ui,sans-serif;letter-spacing:-.02em;margin:0 0 8px}
.lead{color:var(--dim);margin:0 0 22px;font-size:18px}
.card{background:#111c30;border:1px solid var(--line);border-radius:16px;padding:16px 20px}
.cols{display:grid;gap:18px}
.in{animation:enter .6s cubic-bezier(.2,.7,.2,1) both}
.mono{font:15px/1.6 ui-monospace,'JetBrains Mono',monospace}
.chip{display:inline-block;font:500 13px ui-monospace,monospace;background:#10264a;color:#bfdbfe;border:1px solid #1e4a8a;border-radius:8px;padding:2px 8px;margin:4px 4px 0 0}
.dim{color:var(--dim);font-size:16px}
.big{font:700 64px/1 Inter,system-ui,sans-serif;letter-spacing:-.03em;color:var(--ok)}
.term{flex:1;min-height:0;background:#070d18;border:1px solid var(--line);border-radius:12px;display:flex;flex-direction:column}
.bar{padding:8px 14px;border-bottom:1px solid var(--line);color:var(--dim);font:13px ui-monospace,monospace}
#term{flex:1;min-height:0;overflow:hidden;padding:10px 16px;font:13px/1.32 ui-monospace,'JetBrains Mono',monospace;white-space:pre-wrap}
#term .l{display:none;min-height:1.32em}
.l.svc{color:var(--amber)}.l.sym{color:var(--hot)}.l.ci{color:#fda4af}.l.cmd{color:var(--ok)}
"""


def _slide(body: str) -> str:
    return (
        f'<!doctype html><html><head><meta charset="utf-8"><style>{CSS}</style></head>'
        f'<body><div class="stage">{body}</div></body></html>'
    )


def _card(delay: float, inner: str) -> str:
    return f'<div class="card in" style="animation-delay:{delay}s">{inner}</div>'


def _step(delay: float, name: str, role: str, detail: str) -> str:
    return _card(
        delay,
        f'<div class="eyebrow">{name}</div><b>{role}</b>'
        f'<p class="dim" style="margin:6px 0 0;font-size:14.5px">{detail}</p>',
    )


def _title() -> str:
    return _slide(f"""<div class="eyebrow">Built for CreateAThon and Mergetober, as a cognee-community connector</div>
<h1 style="font-size:56px">Cognee GitGraph</h1>
<p class="lead" style="font-size:22px">What a pull request can break, before it merges</p>
<div class="cols" style="grid-template-columns:1fr 1fr 1fr;flex:1;align-content:start">
  {_card(0.6, '<div class="eyebrow">The problem</div><b>A change to a shared auth utility silently breaks downstream services.</b><p class="dim">The diff shows one file. The callers, and the tests that fail because of it, are scattered across the monorepo.</p>')}
  {_card(1.2, '<div class="eyebrow">Who it is for</div><b>Reviewers of Python monorepos</b><p class="dim">People who need to know which services a branch reaches, and how far away they are, before they approve.</p>')}
  {_card(1.8, '<div class="eyebrow">What you get</div><b>A blast radius, with no API key</b><p class="dim">Plain graph traversal over git history and code. Zero LLM calls.</p>')}
</div>""")


def _how() -> str:
    return _slide(f"""<div class="eyebrow">How it works</div><h1>Git history in, a deterministic blast radius out</h1><p class="lead">No model decides anything. The same repo always gives the same answer.</p>
<div class="cols" style="grid-template-columns:repeat(3,1fr)">
  {_step(0.3, "Read", "Git history, symbols, CI failures", 'Commits and file changes from git. Functions, classes and calls from the stdlib <span class="chip">ast</span> module. Failing tests from <span class="chip">JUnit XML</span>.')}
  {_step(0.9, "Load", "A dlt source into Cognee", '<span class="chip">gitgraph_source</span> has five tables. <span class="chip">cognee.remember</span> loads them as rows, with no LLM extraction.')}
  {_step(1.5, "Link", "Row-to-row edges", '<span class="chip">link_rows</span> connects each reference to the symbols it joins, and each file change to its commit.')}
  {_step(2.1, "Traverse", "Breadth-first search", "A networkx graph from caller to callee. The search walks outward from the changed symbols, up to four hops.")}
  {_step(2.7, "Report", "Services, hops, CI failures", "Which top-level services are at risk, how many hops away, and which CI failures belong to them.")}
  {_step(3.3, "Prove", "Zero LLM calls, no key", "The tests run without an API key, and the demo does too.")}
</div>""")


def _tests(passed: int | None) -> str:
    count = "gate skipped" if passed is None else str(passed)
    label = "iteration run: the gate was skipped" if passed is None else "pytest tests passed"
    line = (
        "gate skipped"
        if passed is None
        else f'<span style="color:var(--ok)">&#10003; {passed} passed</span>'
    )
    return _slide(f"""<div class="eyebrow">Run just now, before this recording</div><h1>Tested</h1><p class="lead">The numbers come from the run that preceded this video.</p>
<div class="cols" style="grid-template-columns:repeat(2,1fr)">
  {_card(0.3, f'<div class="big">{count}</div><p class="dim">{label}</p>')}
  {_card(1.0, '<div class="big">0</div><p class="dim">mypy errors across the package, tests, examples and scripts</p>')}
</div>
<div class="card in mono" style="margin-top:22px;animation-delay:1.8s">$ bun run test &nbsp;{line}<br>$ bun run typecheck &nbsp;<span style="color:var(--ok)">&#10003; no errors</span></div>""")


def _outro() -> str:
    return _slide(f"""<div style="margin:auto;text-align:center">
  <h1 class="in" style="font-size:56px">Cognee GitGraph</h1>
  <p class="lead in" style="font-size:22px;animation-delay:.4s">Know what a pull request can break.</p>
  <div class="card in mono" style="display:inline-block;text-align:left;animation-delay:.9s">Code: {REPO_URL}</div>
</div>""")


def _terminal(lines: list[str]) -> str:
    def kind(line: str) -> str:
        if re.match(r"^  (billing|orders|notifications)  \d", line):
            return "svc"
        if line.startswith("Changed symbols"):
            return "sym"
        if re.match(r"^  \w+-tests  ", line):
            return "ci"
        return ""

    rows = "".join(f'<div class="l {kind(ln)}">{html.escape(ln)}</div>' for ln in lines)
    return _slide(f"""<div class="eyebrow">The real output</div><h1>cognee-gitgraph demo</h1>
<div class="term"><div class="bar">$ cognee-gitgraph demo --html blast.html</div><div id="term">{rows}</div></div>""")


REVEAL = """(k) => {
  document.querySelectorAll('#term .l').forEach((l, i) => { l.style.display = i < k ? 'block' : 'none'; });
  const t = document.getElementById('term'); t.scrollTop = t.scrollHeight;
}"""

TIDY = """() => {
  const g = window.graph, cols = {};
  g.onEngineStop(() => {});
  g.graphData().nodes.forEach((n) => {
    const h = n.status === 'changed' ? 0 : n.status === 'at-risk' ? n.hops : -1;
    (cols[h] = cols[h] || []).push(n);
  });
  Object.entries(cols).forEach(([h, ns]) => ns.forEach((n, i) => {
    n.fx = h < 0 ? 230 : h * 230;
    n.fy = h < 0 ? -230 : ns.length === 1 ? (h % 2 ? 60 : -60) : ((ns.length - 1) / 2 - i) * 120;
    n.fz = 0;
  }));
  g.d3ReheatSimulation();
  // Frame it ourselves: the whole layout, shifted right so the side panel stays clear of it.
  const xs = g.graphData().nodes.map((n) => n.fx), ys = g.graphData().nodes.map((n) => n.fy);
  const w = Math.max(...xs) - Math.min(...xs), h2 = Math.max(...ys) - Math.min(...ys);
  const c = { x: (Math.max(...xs) + Math.min(...xs)) / 2 - 140, y: (Math.max(...ys) + Math.min(...ys)) / 2 + 130, z: 0 };
  const d = 1.2 * Math.max((w / 2 + 300) / 0.83, (h2 / 2 + 120) / 0.46);
  const o = { x: 0, y: 0.25 * d, z: d };
  g.cameraPosition({ x: c.x + o.x, y: c.y + o.y, z: c.z + o.z }, c, 0);
  return { c, o };
}"""

# Sway the camera round the graph and push in slowly; the labels are page elements, so they follow.
ORBIT = """([ms, c, o]) => new Promise((resolve) => {
  const g = window.graph;
  const t0 = performance.now();
  const tick = () => {
    const f = Math.min(1, (performance.now() - t0) / ms);
    const a = 0.3 * Math.sin(2 * Math.PI * f), k = 1 - 0.12 * Math.sin(Math.PI * f);
    const cos = Math.cos(a), sin = Math.sin(a);
    g.cameraPosition({ x: c.x + k * (o.x * cos + o.z * sin), y: c.y + k * o.y,
                       z: c.z + k * (-o.x * sin + o.z * cos) }, c, 0);
    if (f < 1) requestAnimationFrame(tick); else resolve(null);
  };
  tick();
})"""


def _script(passed: int | None) -> dict[str, str]:
    tests = (
        "Before this recording, the full test suite ran, and the type checks. "
        + (
            f"{passed} Python tests passed, and mypy is clean."
            if passed is not None
            else "This is an iteration run, so the gate was skipped."
        )
        + " The tests need no API key."
    )
    return {
        "title": (
            "Cognee GitGraph answers one question for reviewers of Python monorepos: what can this "
            "pull request break? When someone changes a shared authentication utility, the diff "
            "shows one file. The services that call it, and the tests that fail because of it, "
            "are somewhere else. This is a Cognee community connector, built for CreateAThon and "
            "Mergetober."
        ),
        "terminal1": (
            "I run the demo. It builds a small sample monorepo and a branch that changes make "
            "token and verify token in the shared auth file. This is its real output. Billing "
            "calls verify token directly, so it is one hop away. Orders is two hops away. "
            "Notifications is four hops away, through session loading, the cart and checkout. "
            "Search never touches auth, so it is not at risk. Two CI failures belong to at risk "
            "services: one in billing, one in orders."
        ),
        "terminal2": (
            "The same run then loads the repo into Cognee, with no API key. The graph has eight "
            "commits, fifteen file changes, eight symbols, ten references and two CI failures, "
            "and the links between those rows are written as edges."
        ),
        "graph": (
            "Here is the real page the tool writes. Every node is a function. Arrows run from a "
            "caller to what it calls. The red nodes are the symbols this branch changed, make "
            "token and verify token. Amber nodes are at risk, and each label gives the service "
            "and how many hops away it is. Grey nodes are safe, like search. You can see the "
            "path from verify token to billing, and the long way round to notifications."
        ),
        "how": (
            "How it works. Git history, symbols and references from the standard library ast "
            "module, and failures from J Unit X M L become a D L T source. Cognee remember loads "
            "the rows, with no LLM extraction, and link rows adds the row to row edges. The "
            "blast radius is a breadth first search over a networkx graph from caller to callee. "
            "There are zero LLM calls, and no API key. The tests prove it."
        ),
        "tests": tests,
        "outro": (
            "Cognee GitGraph. Know what a pull request can break, before it merges. The code is "
            "on GitHub, and the link is on screen."
        ),
    }


def record(
    slides: dict[str, str],
    html_path: Path,
    stdout_lines: list[str],
    voice: dict[str, tuple[Path, int]],
    tmp: Path,
) -> tuple[Path, list[tuple[Path, int]], float]:
    from playwright.sync_api import ConsoleMessage, ViewportSize, sync_playwright

    clips: list[tuple[Path, int]] = []
    errors: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=CHROMIUM_ARGS)
        size = ViewportSize(width=1280, height=720)
        ctx = browser.new_context(
            viewport=size, color_scheme="dark", record_video_dir=str(tmp), record_video_size=size
        )
        page = ctx.new_page()

        def on_console(msg: ConsoleMessage) -> None:
            if msg.type == "error":
                errors.append(f"console: {msg.text}")

        page.on("console", on_console)
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        t0 = time.monotonic()

        def now_ms() -> int:
            return round((time.monotonic() - t0) * 1000)

        def narrate(name: str, steps: list[Callable[[], object]] | None = None) -> None:
            file, ms = voice[name]
            steps = steps or []
            at = now_ms()
            clips.append((file, at))
            for step in steps:
                step()
                time.sleep(ms / 1000 / (len(steps) + 1))
            time.sleep(max(0.0, (ms - (now_ms() - at)) / 1000) + 0.35)

        page.set_content(slides["title"], wait_until="load")
        time.sleep(0.9)
        narrate("title")

        cognee_at = next(i for i, ln in enumerate(stdout_lines) if ln.startswith("Cognee graph"))
        page.set_content(slides["terminal"], wait_until="load")
        time.sleep(0.9)
        narrate("terminal1", [partial(page.evaluate, REVEAL, k) for k in range(1, cognee_at)])
        total = len(stdout_lines)
        narrate(
            "terminal2", [partial(page.evaluate, REVEAL, k) for k in range(cognee_at, total + 1)]
        )

        page.goto(html_path.as_uri())
        page.wait_for_function("window.graph && window.graph.graphData().nodes.length > 0")
        # Recording only: lay the nodes out in columns by hop so no label sits on another, and
        # drop the page's own re-fit (15 s in) so the orbit is not jumped.
        view = page.evaluate(TIDY)
        time.sleep(3)
        ms = voice["graph"][1]
        narrate("graph", [partial(page.evaluate, ORBIT, [ms - 1500, view["c"], view["o"]])])

        page.set_content(slides["how"], wait_until="load")
        narrate("how")
        page.set_content(slides["tests"], wait_until="load")
        narrate("tests")
        page.set_content(slides["outro"], wait_until="load")
        narrate("outro")
        time.sleep(1.2)
        wall = now_ms() / 1000

        ctx.close()
        webm = Path(page.video.path()) if page.video else None
        browser.close()
    if errors:
        raise RuntimeError("the console logged errors while recording:\n" + "\n".join(errors))
    if webm is None:
        raise RuntimeError("playwright wrote no video")
    return webm, clips, wall


def main() -> int:
    home = Path.home()
    os.environ["PATH"] = f"{home}/.local/bin:{home}/.cargo/bin:{os.environ['PATH']}"
    passed: int | None = None
    if os.environ.get("DEMO_VIDEO_SKIP_GATE") == "1":
        print("gate skipped (DEMO_VIDEO_SKIP_GATE=1)")
    else:
        print("running the gate: bun run test, bun run typecheck ...")
        passed = parse_pytest_passed(_run(["bun", "run", "test"]))
        _run(["bun", "run", "typecheck"])
        print(f"gate green: {passed} passed")

    with tempfile.TemporaryDirectory(prefix="gitgraph-demo-") as tmp_name:
        tmp = Path(tmp_name)
        html_path = tmp / "blast.html"
        stdout = _real_demo(html_path)
        assert_facts(stdout)
        print("demo output matches the narration")

        script = _script(passed)
        voice = {}
        for name, text in script.items():
            file = tmp / f"{name}.mp3"
            voice[name] = (file, _speak(text, file))
            print(f"voice {name}: {voice[name][1] / 1000:.1f} s")

        slides = {
            "title": _title(),
            "terminal": _terminal(stdout.splitlines()),
            "how": _how(),
            "tests": _tests(passed),
            "outro": _outro(),
        }
        webm, clips, wall = record(slides, html_path, stdout.splitlines(), voice, tmp)
        OUT.parent.mkdir(exist_ok=True)
        stretch = wall / _duration(webm)
        print(f"recorded {wall:.1f} s of wall clock, stretch {stretch:.3f}")
        _run(ffmpeg_command(webm, clips, OUT, stretch))

    probe_cmd = ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format"]
    probe = json.loads(_run([*probe_cmd, str(OUT)]))
    problems = video_problems(probe)
    if problems:
        print("demo video failed: " + "; ".join(problems), file=sys.stderr)
        return 1
    v = next(s for s in probe["streams"] if s["codec_type"] == "video")
    a = next(s for s in probe["streams"] if s["codec_type"] == "audio")
    seconds = float(probe["format"]["duration"])
    print(
        f"demo video: {OUT} ({v['width']}x{v['height']} {v['codec_name']}, {a['codec_name']} audio, {seconds:.1f} s)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
