"""
build.py -- turns content/** into the static site in docs/.

    python3 tools/build.py            # build everything
    python3 tools/build.py --check    # build + validate links, images, lab + prompt ids

Sources of truth:
  content/site.json          navigation, page list, page metadata
  content/pages/*.md         prose pages
  content/projects/*.md      project pages (same format)
  content/prompts/*.md       copyable Developer Agent Prompts -> docs/assets/prompts/<key>.txt

Output is plain HTML/CSS/JS with no runtime build step, so it works from
file:// as well as from GitHub Pages / Netlify / Render.
"""

import json
import os
import re
import sys
import html as htmlmod
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CONTENT = os.path.join(ROOT, "content")
DOCS = os.path.join(ROOT, "docs")
sys.path.insert(0, HERE)
import md as mdmod  # noqa: E402


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def strip_md(text):
    t = re.sub(r"```.*?```", " ", text, flags=re.S)
    t = re.sub(r":::\s*\w*[^\n]*", " ", t)
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"^[#>\-\*\|+\s]+", "", t, flags=re.M)
    t = t.replace("`", "").replace("**", "").replace("*", "")
    return re.sub(r"\s+", " ", t).strip()


TEMPLATE = """<!DOCTYPE html>
<html lang="en" data-theme="auto">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="{desc}">
<meta name="color-scheme" content="light dark">
<link rel="icon" href="data:image/svg+xml,{favicon}">
<link rel="stylesheet" href="assets/css/main.css">
<link rel="canonical" href="{canonical}">
<script src="assets/js/app.js" defer></script>
<script>(function(){{try{{var t=localStorage.getItem("aca.theme");if(t)document.documentElement.dataset.theme=t;}}catch(e){{}}}})();</script>
</head>
<body data-page="{id}" data-kind="{kind}">
<a class="skip" href="#main">Skip to content</a>
<header class="top">
  <button class="iconbtn menubtn" data-drawer="1" aria-label="Open navigation">
    <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="1.8" d="M4 7h16M4 12h16M4 17h16"/></svg>
  </button>
  <a class="brand" href="index.html"><span class="mark">ACA</span><span class="brandtext">DevOps Academy</span></a>
  <nav class="topnav">{topnav}</nav>
  <div class="topright">
    <button class="iconbtn" data-search-open aria-label="Search" title="Search (/ )">
      <svg viewBox="0 0 24 24" width="19" height="19" aria-hidden="true"><circle cx="11" cy="11" r="6.5" fill="none" stroke="currentColor" stroke-width="1.8"/><path stroke="currentColor" stroke-width="1.8" d="M16 16l4.5 4.5"/></svg>
    </button>
    <button class="iconbtn" data-theme-toggle aria-label="Switch light or dark theme" title="Theme">
      <svg viewBox="0 0 24 24" width="19" height="19" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="1.7" d="M12 3v2m0 14v2m9-9h-2M5 12H3m14.5-6.5-1.4 1.4M8.9 15.1l-1.4 1.4m0-12 1.4 1.4m6.2 6.2 1.4 1.4M12 8.2a3.8 3.8 0 1 0 0 7.6 3.8 3.8 0 0 0 0-7.6Z"/></svg>
    </button>
  </div>
</header>
<div class="progress" data-progress-bar aria-hidden="true"></div>
<div class="layout">
  <aside class="sidebar" data-drawer-target>
    <div class="sidehead">
      <span class="sidetitle">Curriculum</span>
      <span class="sideprogress" data-progress-label></span>
    </div>
    <nav class="nav">{sidebar}</nav>
    <div class="sidefoot">
      <p>Everything here is offline-first: no account, no tracking, no backend. Your progress and answers stay in this browser.</p>
    </div>
  </aside>
  <main id="main">
    <article class="page">
      <nav class="crumbs" aria-label="Breadcrumb">{crumbs}</nav>
      {hero}
      {toc}
      <div class="prose">{body}</div>
      <div class="pagefoot">
        <p class="pagefootnote">{footnote}</p>
        <div class="pager">
          {prev}
          {next}
        </div>
        <p class="markread"><label class="tickall"><input type="checkbox" data-mark-done> <span>Mark this page as done</span></label></p>
      </div>
    </article>
  </main>
</div>
<div class="searchmodal" data-search-modal hidden>
  <div class="searchbox">
    <input type="search" data-search-input placeholder="Search the course: OOMKilled, terraform state lock, ALB 502, Jenkinsfile…" aria-label="Search">
    <ul class="searchresults" data-search-results></ul>
    <p class="searchhint">Press <kbd>Esc</kbd> to close · <kbd>/</kbd> to open</p>
  </div>
</div>
</body>
</html>
"""


def sidebar_html(cfg, current, done_ids=None):
    by_id = {p["id"]: p for p in cfg["pages"]}
    out = []
    for sec in cfg.get("sections", []):
        out.append('<p class="navsec">%s</p>' % htmlmod.escape(sec["name"]))
        for pid in sec.get("pages", []):
            p = by_id.get(pid)
            if not p:
                continue
            cls = "current" if pid == current else ""
            tick = '<span class="pdot" data-done-dot="%s"></span>' % pid
            out.append('<a class="%s" href="%s.html" data-nav-page="%s">%s%s%s</a>'
                       % (cls, pid, pid, tick, htmlmod.escape(p.get("nav") or p["title"]),
                          '<em class="tag %s">%s</em>' % (p["tag"], htmlmod.escape(p["tag"].upper())) if p.get("tag") else ""))
    return "".join(out)


def toc_html(entries):
    if len(entries) < 3:
        return ""
    lis = "".join('<li class="l%d"><a href="#%s">%s</a></li>' % (e["level"], e["id"], htmlmod.escape(e["text"]))
                  for e in entries if e["level"] <= 3)
    return '<details class="toc" open><summary>On this page</summary><ul>%s</ul></details>' % lis


def hero_html(p):
    if p.get("kind") == "home":
        return ""
    bits = []
    if p.get("kicker"):
        bits.append('<p class="kicker">%s</p>' % htmlmod.escape(p["kicker"]))
    bits.append("<h1>%s</h1>" % htmlmod.escape(p["title"]))
    if p.get("hero"):
        bits.append('<p class="herotext">%s</p>' % htmlmod.escape(p["hero"]))
    meta = []
    if p.get("time"):
        meta.append('<span class="mchip">%s</span>' % htmlmod.escape(p["time"]))
    if p.get("tag"):
        meta.append('<span class="mchip tag %s">%s</span>' % (p["tag"], htmlmod.escape(p["tag"].upper())))
    if p.get("level"):
        meta.append('<span class="mchip level">%s</span>' % htmlmod.escape(p["level"]))
    if meta:
        bits.append('<div class="herometa">%s</div>' % "".join(meta))
    return '<header class="hero">%s</header>' % "".join(bits)


def build(check=False, quiet=False):
    cfg = json.loads(read(os.path.join(CONTENT, "site.json")))
    site = cfg.get("site", {})
    problems = []
    pages = cfg["pages"]
    by_id = {p["id"]: p for p in pages}
    order = [p["id"] for p in pages]

    prompts = {}
    pdir = os.path.join(CONTENT, "prompts")
    if os.path.isdir(pdir):
        for fn in sorted(os.listdir(pdir)):
            if fn.endswith(".md"):
                prompts[fn[:-3]] = read(os.path.join(pdir, fn)).strip() + "\n"
                write(os.path.join(DOCS, "assets", "prompts", fn[:-3] + ".txt"), prompts[fn[:-3]])

    used_labs, used_prompts, used_quizzes = set(), set(), set()
    written = 0
    index_entries = []
    for p in pages:
        src = os.path.join(CONTENT, p["file"])
        if not os.path.exists(src):
            problems.append("missing content file: %s" % src)
            continue
        raw = read(src)
        ctx = {"prompts": prompts, "problems": []}
        body, ctx = (lambda r: (r.render(raw), r.ctx))(mdmod.Renderer(ctx))
        problems += ["%s: %s" % (p["id"], x) for x in ctx["problems"]]
        used_labs |= set(re.findall(r'data-lab-id="([^"]+)"', body))
        used_prompts |= set(re.findall(r'class="promptbox" data-prompt="([^"]+)"', body))
        used_quizzes |= set(re.findall(r'data-quiz="([^"]+)"', body))

        i = order.index(p["id"])
        prev_p = by_id[order[i - 1]] if i > 0 else None
        next_p = by_id[order[i + 1]] if i + 1 < len(order) else None
        sec_name = ""
        for sec in cfg.get("sections", []):
            if p["id"] in sec.get("pages", []):
                sec_name = sec["name"]
        crumbs = '<a href="index.html">Home</a><span>/</span>'
        if sec_name:
            crumbs += "%s<span>/</span>" % htmlmod.escape(sec_name)
        crumbs += '<b>%s</b>' % htmlmod.escape(p.get("short") or p["title"])

        prev_html = ('<a class="pg prev" href="%s.html"><span>Previous</span><strong>%s</strong></a>'
                     % (prev_p["id"], htmlmod.escape(prev_p.get("nav") or prev_p["title"]))) if prev_p else ""
        next_html = ('<a class="pg next" href="%s.html"><span>Next</span><strong>%s</strong></a>'
                     % (next_p["id"], htmlmod.escape(next_p.get("nav") or next_p["title"]))) if next_p else ""

        topnav = "".join('<a href="%s.html"%s>%s</a>' % (pid, ' class="on"' if pid == p["id"] else "",
                                                          htmlmod.escape((by_id[pid].get("short") or by_id[pid]["title"])))
                         for pid in cfg.get("topnav", []) if pid in by_id)
        out_html = TEMPLATE.format(
            title=htmlmod.escape(("%s · " % (p.get("short") or p["title"])) if p["id"] != "index" else "") + site.get("title", "DevOps Academy"),
            desc=htmlmod.escape(p.get("meta") or p.get("hero") or site.get("desc", ""), quote=True),
            favicon=urllib.parse.quote('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="7" fill="#0f172a"/><text x="16" y="21" font-family="ui-monospace,monospace" font-size="12" font-weight="700" fill="#5eead4" text-anchor="middle">&gt;_</text></svg>'),
            id=p["id"], kind=p.get("kind", "page"), topnav=topnav,
            sidebar=sidebar_html(cfg, p["id"]), crumbs=crumbs,
            hero=hero_html(p), toc=toc_html([e for e in ctx["toc"]][:60]),
            body=body, prev=prev_html, next=next_html,
            canonical="",
            footnote="Part of the DevOps Academy built from the 41-project reference repository · offline · no tracking",
        )
        outpath = os.path.join(DOCS, ("index.html" if p["id"] == "index" else p["id"] + ".html"))
        write(outpath, out_html)
        written += 1
        index_entries.append({
            "id": p["id"], "title": p.get("short") or p["title"], "desc": p.get("hero") or p.get("desc", ""),
            "tag": p.get("tag", ""), "time": p.get("time", ""), "section": sec_name,
            "sections": [e["text"] for e in ctx["toc"]][:40],
            "text": strip_md(raw)[:1400],
            "anchors": [e["id"] for e in ctx["toc"]][:40],
        })

    write(os.path.join(DOCS, "assets", "js", "search-index.js"),
          "window.ACADEMY_INDEX = %s;\n" % json.dumps(index_entries, ensure_ascii=False, separators=(",", ":")))
    write(os.path.join(DOCS, "sitemap.xml"),
          '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
          + "".join('  <url><loc>%s/%s.html</loc></url>\n' % (site.get("url", "").rstrip("/"), pid) for pid in order)
          + "</urlset>\n")
    write(os.path.join(DOCS, ".nojekyll"), "")

    if check:
        pending = {p["id"] + ".html" for p in pages} - {f for f in os.listdir(DOCS) if f.endswith(".html")}
        for fn in sorted(os.listdir(DOCS)):
            if not fn.endswith(".html"):
                continue
            src = read(os.path.join(DOCS, fn))
            for href in re.findall(r'href="([^"#]+?)(?:#[^"]*)?"', src):
                if href.startswith(("http", "mailto:", "data:", "#")):
                    continue
                if href in pending:
                    continue
                if not os.path.exists(os.path.normpath(os.path.join(DOCS, href))):
                    problems.append("%s -> broken link %s" % (fn, href))
            for im in re.findall(r'<img[^>]+src="([^"]+)"', src):
                if im.startswith(("http", "data:")):
                    continue
                if not os.path.exists(os.path.normpath(os.path.join(DOCS, im))):
                    problems.append("%s -> missing image %s" % (fn, im))
        labsdir = os.path.join(DOCS, "assets", "js", "labs")
        lab_ids = {f[:-3] for f in os.listdir(labsdir)} if os.path.isdir(labsdir) else set()
        for lid in sorted(used_labs):
            if lid not in lab_ids:
                problems.append("lab '%s' has no file docs/assets/js/labs/%s.js" % (lid, lid))
        for lid in sorted(lab_ids - used_labs):
            problems.append("lab file %s.js is not used by any page" % lid)
        for key in sorted(prompts):
            if key not in used_prompts:
                problems.append("prompt file %s.md is never referenced" % key)
        qdir = os.path.join(DOCS, "assets", "js")
        qsrc = read(os.path.join(qdir, "quizzes.js")) if os.path.exists(os.path.join(qdir, "quizzes.js")) else ""
        q_ids = set(re.findall(r"AcademyQuiz\.register\(\s*[\"']([^\"']+)", qsrc))
        for qid in sorted(used_quizzes):
            if qid not in q_ids:
                problems.append("quiz '%s' is not registered in docs/assets/js/quizzes.js" % qid)

    if not quiet:
        print("built %d pages" % written)
    if check:
        if problems:
            print("PROBLEMS (%d):" % len(problems))
            for x in problems:
                print("  - " + x)
            return 1
        print("check OK · %d pages · %d labs · %d prompts · %d quizzes" % (written, len(used_labs), len(prompts), len(used_quizzes)))
    return 0


if __name__ == "__main__":
    sys.exit(build(check="--check" in sys.argv))
