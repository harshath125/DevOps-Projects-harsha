"""
md.py -- a deliberately small Markdown-subset renderer for the DevOps Academy site.

Supported: headings (## .. ####, auto ids + TOC capture), paragraphs, **bold**,
*italic*, `code`, [link](url), ![img](src), > quotes, - / 1. lists, --- rules,
GFM tables, fenced code with an optional title (```bash title), and ::: containers:

  callout [note|warn|fix|aha|why] [Title]   step Title     flow            checklist
  cols / col                               revision       learn           figure
  term (terminal transcript)               prompt <key>   lab <id>        quiz <id>
  cards (link :: num :: desc :: chips)     grid2 / grid3  api

Rendering is offline and deterministic so the built site is plain HTML/CSS/JS.
"""

import html
import re

SLUG_RE = re.compile(r"[^a-z0-9]+")

CONTAINERS = {
    "callout": ("div", "callout"),
    "note": ("div", "callout note"),
    "warn": ("div", "callout warn"),
    "fix": ("div", "callout fix"),
    "aha": ("div", "callout aha"),
    "why": ("div", "callout why"),
    "step": ("div", "step"),
    "flow": ("div", "flow"),
    "checklist": ("ul", "checklist"),
    "cols": ("div", "cols"),
    "col": ("div", "col"),
    "revision": ("div", "revision"),
    "learn": ("div", "learn"),
    "figure": ("figure", "figure"),
    "term": ("div", "term"),
    "prompt": ("div", "promptbox"),
    "lab": ("div", "labhost"),
    "quiz": ("div", "quizhost"),
    "cards": ("div", "cards"),
    "grid2": ("div", "grid2"),
    "grid3": ("div", "grid3"),
    "api": ("div", "api"),
}

CALLOUT_MODS = ("note", "warn", "fix", "aha", "why")


def slugify(text):
    return SLUG_RE.sub("-", re.sub(r"[`*_\[\]()#]", "", text).strip().lower()).strip("-") or "s"


# --------------------------------------------------------------------------- inline

CODE_RE = re.compile(r"`([^`]+)`")
BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
ITALIC_RE = re.compile(r"(?<![\w*])\*([^*\n]+)\*(?!\w)")
DEL_RE = re.compile(r"~~([^~]+)~~")
IMG_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"([^\"]*)\")?\)")
LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)(?:\s+\"([^\"]*)\")?\)")


def inline(text, ctx=None):
    """Escape first, then apply inline markup on the escaped text."""
    if text is None:
        return ""
    out = html.escape(text, quote=False)
    out = IMG_RE.sub(
        lambda m: '<img src="%s" alt="%s"%s loading="lazy">'
        % (m.group(2), html.escape(m.group(1), quote=True), (' title="%s"' % html.escape(m.group(3), quote=True)) if m.group(3) else ""),
        out,
    )

    def _link(m):
        href, title = m.group(2), m.group(3)
        cls = ""
        if href.startswith("http"):
            cls = ' class="ext" target="_blank" rel="noopener"'
        t = ' title="%s"' % html.escape(title, quote=True) if title else ""
        return '<a href="%s"%s%s>%s</a>' % (href, t, cls, m.group(1))

    out = LINK_RE.sub(_link, out)
    out = CODE_RE.sub(lambda m: "<code>%s</code>" % m.group(1), out)
    out = BOLD_RE.sub(lambda m: "<strong>%s</strong>" % m.group(1), out)
    out = ITALIC_RE.sub(lambda m: "<em>%s</em>" % m.group(1), out)
    out = DEL_RE.sub(lambda m: "<del>%s</del>" % m.group(1), out)
    out = out.replace("<br>", "\n")
    return out


# ------------------------------------------------------------------------- renderer


class Renderer:
    def __init__(self, ctx=None):
        self.ctx = ctx if ctx is not None else {"toc": [], "ids": set(), "prompts": {}, "problems": []}
        self.ctx.setdefault("toc", [])
        self.ctx.setdefault("ids", set())
        self.ctx.setdefault("prompts", {})
        self.ctx.setdefault("problems", [])

    # -- helpers -----------------------------------------------------------
    def _uid(self, text):
        base = slugify(re.sub(r"^[^A-Za-z0-9]+", "", text))
        uid, n = base, 2
        while uid in self.ctx["ids"]:
            uid = "%s-%d" % (base, n)
            n += 1
        self.ctx["ids"].add(uid)
        return uid

    @staticmethod
    def _is_container(line):
        return re.match(r"^:::\s*([\w-]+)?(.*)$", line)

    def _fence(self, lines, i):
        """Collect a fenced code block starting at i (```lang optional-title)."""
        m = re.match(r"^\s*```+\s*([\w+#.-]*)\s*(.*)$", lines[i])
        lang = (m.group(1) or "text").lower()
        title = (m.group(2) or "").strip()
        i += 1
        buf = []
        while i < len(lines) and not re.match(r"^\s*```+\s*$", lines[i]):
            buf.append(lines[i])
            i += 1
        i += 1  # closing fence
        code = html.escape("\n".join(buf).rstrip("\n"), quote=False)
        label = title or {"bash": "terminal", "sh": "terminal", "console": "terminal", "yaml": "yaml",
                          "hcl": "terraform", "json": "json", "groovy": "jenkinsfile", "python": "python",
                          "sql": "sql", "ini": "ini", "dockerfile": "dockerfile", "text": "text",
                          "md": "markdown", "tsx": "tsx", "js": "javascript", "mermaid": "mermaid"}.get(lang, lang)
        head = '<div class="codehead"><span class="lang">%s</span>%s</div>' % (
            html.escape(label), '<button class="copybtn" data-copy>copy</button>')
        return i, '<div class="codewrap with-shell">%s<pre><code class="lang-%s">%s</code></pre></div>' % (head, lang, code)

    def _table(self, lines, i):
        head = [c.strip() for c in lines[i].strip().strip("|").split("|")]
        sep = [c.strip() for c in lines[i + 1].strip().strip("|").split("|")]
        aligns = []
        for c in sep:
            if re.match(r"^:?-+:?$", c):
                aligns.append("right" if c.endswith(":") and not c.startswith(":") else
                              "center" if c.startswith(":") and c.endswith(":") else "left")
            else:
                return None
        i += 2
        rows = []
        while i < len(lines) and "|" in lines[i] and lines[i].strip():
            rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
            i += 1
        n = len(head)
        th = "".join('<th class="al-%s">%s</th>' % (aligns[k], inline(head[k] if k < len(head) else "", self.ctx)) for k in range(n))
        body = []
        for r in rows:
            r = r + [""] * (n - len(r))
            body.append("<tr>" + "".join('<td class="al-%s">%s</td>' % (aligns[k], inline(r[k], self.ctx)) for k in range(n)) + "</tr>")
        return i, '<div class="tablewrap"><table><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>' % (th, "".join(body))

    def _list(self, lines, i, ordered):
        pat = re.compile(r"^\s*%s\s+(.*)$" % (r"\d+[.)]" if ordered else r"[-*+]"))
        items = []
        while i < len(lines):
            m = pat.match(lines[i])
            if not m:
                if lines[i].startswith("   ") and items:  # continuation
                    items[-1] += " " + lines[i].strip()
                    i += 1
                    continue
                break
            items.append(m.group(1))
            i += 1
        lis = []
        for it in items:
            chk = re.match(r"^\[( |x|X)\]\s+(.*)$", it)
            if chk:
                on = chk.group(1).lower() == "x"
                lis.append("<li class='task%s'><span class='box'>%s</span><span>%s</span></li>"
                           % (" done" if on else "", "\u2713" if on else "", inline(chk.group(2), self.ctx)))
            else:
                lis.append("<li>%s</li>" % inline(it, self.ctx))
        tag = "ol" if ordered else "ul"
        cls = " tasks" if any(l.startswith("<li class='task") for l in lis) else ""
        return i, "<%s class=\"%s\">%s</%s>" % (tag, ("numlist" if ordered else "plain") + cls, "".join(lis), tag)

    def _container(self, lines, i):
        m = self._is_container(lines[i])
        kind = (m.group(1) or "").strip()
        arg = (m.group(2) or "").strip()
        if kind == "":  # bare ::: -- treat as div close guard
            return None
        spec = CONTAINERS.get(kind)
        if spec is None:
            self.ctx["problems"].append("unknown container kind: %r" % kind)
            spec = ("div", "box " + kind)
        i += 1
        depth = 1
        buf = []
        while i < len(lines):
            cur = lines[i].strip()
            if re.match(r"^:::\s*[\w-]+", cur):
                depth += 1
            elif cur == ":::":
                depth -= 1
                if depth == 0:
                    i += 1
                    break
            buf.append(lines[i])
            i += 1
        body = "\n".join(buf)
        tag, cls = spec
        head_html = ""

        if kind == "callout":
            parts = arg.split(" ", 1) if arg else [""]
            mod, title = parts[0].strip(), (parts[1].strip() if len(parts) > 1 else "")
            if mod in CALLOUT_MODS:
                cls = "callout " + mod
            elif arg:
                title = arg
            if title:
                head_html = '<p class="ctitle">%s</p>' % inline("**" + title + "**", self.ctx)
            inner = self.render(body)
            return i, '<div class="%s">%s%s</div>' % (cls, head_html, inner)

        if kind in ("step", "revision", "learn", "figure", "api"):
            if arg:
                head_html = '<p class="ctitle">%s</p>' % inline("**" + arg + "**", self.ctx)
            inner = self.render(body)
            if kind == "figure":
                nonblank = [l.strip() for l in buf if l.strip()]
                cap = ""
                if nonblank and not nonblank[-1].startswith("!["):
                    cap = "<figcaption>%s</figcaption>" % inline(nonblank[-1], self.ctx)
                    nonblank = nonblank[:-1]
                inner = self.render("\n".join(nonblank))
                return i, '<figure class="figure">%s%s%s</figure>' % (head_html, inner, cap)
            return i, '<div class="%s">%s%s</div>' % (cls, head_html, inner)

        if kind == "flow":
            nodes = []
            for raw in body.split("\n"):
                t = raw.strip()
                if not t:
                    continue
                if "\u2192" in t:
                    for part in [p.strip() for p in t.split("\u2192") if p.strip()]:
                        nodes.append('<span class="fnode">%s</span><span class="farrow">\u2192</span>' % inline(part, self.ctx))
                elif "\u2190" in t:
                    for part in [p.strip() for p in t.split("\u2190") if p.strip()]:
                        nodes.append('<span class="fnode">%s</span><span class="farrow">\u2190</span>' % inline(part, self.ctx))
                else:
                    while nodes and nodes[-1].startswith('<span class="farrow"'):
                        nodes.pop()
                    nodes.append('<span class="fnode wide">%s</span>' % inline(re.sub(r"^[|`\-\u2514\u251c\u2500 ]+", "", t), self.ctx))
            while nodes and nodes[-1].startswith('<span class="farrow"'):
                nodes.pop()
            return i, '<div class="flow">%s%s</div>' % (head_html, "".join(nodes))

        if kind == "term":
            out = []
            for raw in body.split("\n"):
                t = raw.rstrip()
                if not t.strip():
                    continue
                esc = html.escape(t, quote=False)
                if t.startswith("$ ") or re.match(r"^[\w@.-]+:~?\$ ?$", t) or t.startswith("# "):
                    out.append('<div class="tl cmd"><span class="promptline">%s</span></div>' % esc)
                elif re.match(r"^[\w.-]+@[\w.-]+[:\s]", t):
                    out.append('<div class="tl cmd"><span class="promptline">%s</span></div>' % esc)
                elif t.startswith("!!"):
                    out.append('<div class="tl err">%s</div>' % esc)
                elif t.startswith("++"):
                    out.append('<div class="tl ok">%s</div>' % esc)
                elif t.startswith("##"):
                    out.append('<div class="tl dim">%s</div>' % esc)
                elif t.startswith("--"):
                    out.append('<div class="tl dim">%s</div>' % esc)
                else:
                    out.append('<div class="tl out">%s</div>' % esc)
            title = '<p class="ctitle">%s</p>' % inline("**" + arg + "**", self.ctx) if arg else ""
            return i, '<div class="term">%s<div class="toutwrap">%s</div></div>' % (title, "".join(out))

        if kind == "checklist":
            items = re.findall(r"^\s*[-*]\s+\[( |x|X)\]\s+(.*)$", body, flags=re.M)
            if items:
                lis = "".join(
                    "<li class='%s'><span class='box'>%s</span><span>%s</span></li>"
                    % ("tick" if on.strip().lower() == "x" else "\u2713" if on.strip().lower() == "x" else "",
                       "\u2713" if on.strip().lower() == "x" else "", inline(txt, self.ctx))
                    for on, txt in items
                )
                title = '<p class="ctitle">%s</p>' % inline(arg, self.ctx) if arg else ""
                return i, "%s<ul class=\"checklist\">%s</ul>" % (title, lis)
            return i, '<div class="box note">%s%s</div>' % (head_html, self.render(body))

        if kind == "cards":
            cards = []
            for raw in body.split("\n"):
                t = raw.strip()
                if not t.startswith("-"):
                    continue
                t = t.lstrip("- ").strip()
                bits = [b.strip() for b in t.split("::")]
                head = bits[0] if bits else ""
                num = bits[1] if len(bits) > 1 else ""
                desc = bits[2] if len(bits) > 2 else ""
                chips = [c.strip() for c in (bits[3].split(",") if len(bits) > 3 else []) if c.strip()]
                lm = re.match(r"^\[([^\]]+)\]\(([^)\s]+)\)\s*(?:\u2014|-{1,2})\s*(.*)$", head)
                bare = re.match(r"^\[([^\]]+)\]\(([^)\s]+)\)$", head)
                href = title = ""
                if lm:
                    title, href, extra = lm.group(1), lm.group(2), lm.group(3)
                    if extra and not desc:
                        desc = extra
                elif bare:
                    title, href = bare.group(1), bare.group(2)
                else:
                    title = re.sub(r"^[-\u2014]\s*", "", head)
                num_html = '<span class="num">%s</span>' % inline(num, self.ctx) if num else ""
                chip_html = '<div class="chips">%s</div>' % "".join('<span class="chip">%s</span>' % inline(c, self.ctx) for c in chips) if chips else ""
                t_html = inline(title, self.ctx)
                title_html = '<a href="%s">%s</a>' % (href, t_html) if href else t_html
                desc_html = "<p>%s</p>" % inline(desc, self.ctx) if desc else ""
                inner = "%s<h3>%s</h3>%s%s" % (num_html, title_html, desc_html, chip_html)
                cards.append('<a class="card" href="%s">%s</a>' % (href, inner) if href else '<div class="card">%s</div>' % inner)
            return i, '<div class="cards">%s%s</div>' % (head_html, "".join(cards))

        if kind == "prompt":
            key = arg.strip()
            text = self.ctx.get("prompts", {}).get(key)
            if text is None:
                self.ctx["problems"].append("missing prompt file: content/prompts/%s.md" % key)
                return i, '<div class="promptbox missing"><p>Prompt file <code>%s</code> is missing.</p></div>' % html.escape(key)
            title = re.sub(r"^#+\s*", "", text.strip().split("\n", 1)[0])
            meta = '<div class="promptmeta"><span class="pwords">%d words</span><button class="copybtn" data-prompt-copy="%s">COPY PROMPT</button></div>' % (
                len(text.split()), key)
            body_html = "<pre class=\"prompttext\">%s</pre>" % html.escape(text, quote=False)
            label = '<p class="ctitle">%s</p>' % inline("**Developer Agent Prompt — " + title[:60] + "**", self.ctx)
            return i, '<div class="promptbox" data-prompt="%s">%s%s%s</div>' % (key, label, body_html, meta)

        if kind == "lab":
            lid = arg.strip()
            return i, ('<div class="labhost"><div class="labbar"><span class="labname">Virtual terminal</span>'
                       '<span class="labstep" data-lab-progress></span></div>'
                       '<div class="labterm" data-lab="lab" data-lab-id="%s"></div></div>' % html.escape(lid))

        if kind == "quiz":
            qid = arg.strip()
            return i, '<div class="quizhost"><div class="quiz" data-quiz="%s"></div></div>' % html.escape(qid)

        if kind in ("cols", "grid2", "grid3"):
            # direct children are ::: col blocks or paragraphs
            return i, '<div class="%s">%s%s</div>' % (cls, head_html, self.render(body))

        if kind == "col":
            return i, '<div class="col">%s%s</div>' % (head_html, self.render(body))

        inner = self.render(body)
        return i, '<%s class="%s">%s%s</%s>' % (tag, cls, head_html, inner, tag)

    # -- main loop ---------------------------------------------------------
    def render(self, text):
        lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
        out = []
        i = 0
        first_para_lead = out_first = True
        while i < len(lines):
            line = lines[i]
            s = line.strip()
            if not s:
                i += 1
                continue
            if s.startswith("LEAD:") and out_first:
                out.append('<p class="lead">%s</p>' % inline(s[5:].strip(), self.ctx))
                out_first = False
                i += 1
                continue
            if re.match(r"^:::\s*[\w-]+", s):
                j, frag = self._container(lines, i)
                if j is not None:
                    out.append(frag)
                    i = j
                    out_first = False
                    continue
            if s == ":::":
                i += 1
                continue
            hm = re.match(r"^(#{1,5})\s+(.*)$", s)
            if hm:
                level = len(hm.group(1))
                raw = hm.group(2).strip()
                uid = self._uid(raw)
                txt = inline(raw, self.ctx)
                out.append('<h%d id="%s">%s</h%d>' % (level, uid, txt, level))
                if level <= 3:
                    self.ctx["toc"].append({"level": level, "id": uid, "text": re.sub(r"[`*]", "", raw)})
                i += 1
                out_first = False
                continue
            if re.match(r"^\s*```+\s*[\w+#.-]*", line) and (i + 1 < len(lines)):
                i, frag = self._fence(lines, i)
                out.append(frag)
                out_first = False
                continue
            if "|" in line and i + 1 < len(lines) and re.match(r"^\s*\|?[\s:|-]+\|[\s:|-]*$", lines[i + 1]):
                res = self._table(lines, i)
                if res:
                    i, frag = res
                    out.append(frag)
                    out_first = False
                    continue
            if s.startswith(">"):
                buf = []
                while i < len(lines) and lines[i].strip().startswith(">"):
                    buf.append(re.sub(r"^\s*>\s?", "", lines[i]))
                    i += 1
                out.append("<blockquote>%s</blockquote>" % self.render("\n".join(buf)))
                out_first = False
                continue
            if s == "---" or re.match(r"^\*{3,}$", s):
                out.append("<hr>")
                i += 1
                continue
            if re.match(r"^\s*[-*+]\s+", line):
                i, frag = self._list(lines, i, False)
                out.append(frag)
                out_first = False
                continue
            if re.match(r"^\s*\d+[.)]\s+", line):
                i, frag = self._list(lines, i, True)
                out.append(frag)
                out_first = False
                continue
            # paragraph
            buf = []
            while i < len(lines):
                nxt = lines[i]
                ns = nxt.strip()
                if (not ns or re.match(r"^(#{1,5})\s", ns) or ns.startswith(":::") or nxt.strip().startswith("```")
                        or re.match(r"^\s*[-*+]\s", nxt) or re.match(r"^\s*\d+[.)]\s", nxt) or ns.startswith(">")
                        or ("|" in nxt and i + 1 < len(lines) and re.match(r"^\s*\|?[\s:|-]+\|[\s:|-]*$", lines[i + 1]))):
                    break
                buf.append(ns)
                i += 1
            para = " ".join(buf)
            style = ""
            mstyle = re.search(r"\{\{([^}]*)\}\}$", para)
            if mstyle:
                style = ' style="%s"' % mstyle.group(1)
                para = para[: mstyle.start()].rstrip()
            out.append("<p%s>%s</p>" % (style, inline(para, self.ctx)))
            out_first = False
        return "\n".join(out)


def render(text, ctx=None):
    r = Renderer(ctx)
    body = r.render(text)
    return body, r.ctx
